"""候选文档工具的注册、范围绑定和独立元数据；纯替身，无数据库或模型。"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.agents import document_candidates as tool_module
from app.agents.contracts import ToolExecutionError, ToolOutput
from app.agents.document_candidates import DocumentCandidatesTool
from app.agents.tools import SearchArguments, ToolRegistry
from app.knowledge.document_candidates import (
    DocumentCandidate, DocumentCandidateList, DocumentFilenameQuery,
)


def candidate_result(count: int = 1, *, has_more: bool = False) -> DocumentCandidateList:
    return DocumentCandidateList(candidates=tuple(
        DocumentCandidate(
            document_id=uuid4(), original_filename=f"资料{index}.txt",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), byte_size=10 + index,
        )
        for index in range(count)
    ), has_more=has_more)


@pytest.fixture
def registered_tool(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        session=object(), workspace_id=uuid4(), knowledge_base_id=uuid4(),
        calls=[], result=candidate_result(), error=None,
    )

    def query(session, workspace_id, knowledge_base_id, filename_query):
        state.calls.append((session, workspace_id, knowledge_base_id, filename_query))
        if state.error is not None:
            raise state.error
        return state.result

    monkeypatch.setattr(tool_module, "find_document_candidates", query)
    state.tool = DocumentCandidatesTool(state.session, state.workspace_id, state.knowledge_base_id)
    state.registry = ToolRegistry()
    state.registry.register(state.tool)
    return state


def test_registry_exposes_only_filename_query(registered_tool: SimpleNamespace) -> None:
    definition, = registered_tool.registry.definitions()
    assert definition["name"] == "find_documents"
    assert registered_tool.tool.argument_schema is DocumentFilenameQuery
    assert set(definition["parameters"]["properties"]) == {"filename_query"}
    assert definition["parameters"]["required"] == ["filename_query"]
    assert definition["parameters"]["additionalProperties"] is False


def test_execution_uses_bound_scope_and_trimmed_query(registered_tool: SimpleNamespace) -> None:
    arguments = registered_tool.registry.validate("find_documents", {"filename_query": "  资料  "})
    output = registered_tool.registry.execute("find_documents", arguments)
    assert registered_tool.calls == [(
        registered_tool.session, registered_tool.workspace_id, registered_tool.knowledge_base_id, "资料",
    )]
    assert output.hits == ()
    assert output.document_candidates == registered_tool.result
    assert output.calculation is None and output.clarification is None
    candidate = registered_tool.result.candidates[0]
    wire = output.document_candidates.model_dump(mode="json")
    json.dumps(wire)
    item = wire["candidates"][0]
    assert set(item) == {"document_id", "original_filename", "created_at", "byte_size"}
    assert UUID(item["document_id"]) == candidate.document_id
    assert datetime.fromisoformat(item["created_at"]) == candidate.created_at


def test_empty_candidates_are_a_normal_structured_result(registered_tool: SimpleNamespace) -> None:
    registered_tool.result = candidate_result(0)
    arguments = registered_tool.registry.validate("find_documents", {"filename_query": "没有匹配"})
    output = registered_tool.registry.execute("find_documents", arguments)
    assert output.document_candidates == registered_tool.result
    assert output.document_candidates.candidates == ()
    assert output.document_candidates.has_more is False
    assert output.hits == ()


def test_truncation_flag_and_all_returned_candidates_are_preserved(registered_tool: SimpleNamespace) -> None:
    registered_tool.result = candidate_result(5, has_more=True)
    arguments = registered_tool.registry.validate("find_documents", {"filename_query": "资料"})
    output = registered_tool.registry.execute("find_documents", arguments)
    assert output.document_candidates == registered_tool.result
    assert len(output.document_candidates.candidates) == 5
    assert output.document_candidates.has_more is True
    assert output.hits == ()


@pytest.mark.parametrize("field", ["workspace_id", "knowledge_base_id", "sql", "limit", "document_id"])
def test_extra_parameters_are_rejected_before_query(registered_tool: SimpleNamespace, field: str) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_tool.registry.validate("find_documents", {"filename_query": "资料", field: "injected"})
    assert error.value.code == "invalid_arguments"
    assert registered_tool.calls == []


@pytest.mark.parametrize("arguments", [{}, {"filename_query": True}, {"filename_query": "   "}])
def test_invalid_arguments_are_rejected_before_query(registered_tool: SimpleNamespace, arguments: dict) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_tool.registry.validate("find_documents", arguments)
    assert error.value.code == "invalid_arguments"
    assert registered_tool.calls == []


def test_wrong_argument_model_is_rejected(registered_tool: SimpleNamespace) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_tool.tool.execute(SearchArguments(query="资料"))
    assert error.value.code == "invalid_arguments"
    assert registered_tool.calls == []


def test_constructed_arguments_are_strictly_revalidated(registered_tool: SimpleNamespace) -> None:
    forged = DocumentFilenameQuery.model_construct(filename_query=True)
    with pytest.raises(ToolExecutionError) as error:
        registered_tool.tool.execute(forged)
    assert error.value.code == "invalid_arguments"
    assert registered_tool.calls == []


def test_database_errors_have_a_fixed_safe_message(registered_tool: SimpleNamespace) -> None:
    registered_tool.error = SQLAlchemyError("private database diagnostics")
    arguments = registered_tool.registry.validate("find_documents", {"filename_query": "资料"})
    with pytest.raises(ToolExecutionError) as error:
        registered_tool.registry.execute("find_documents", arguments)
    assert error.value.code == "tool_service_error"
    assert str(error.value) == "tool_service_error"
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    assert len(registered_tool.calls) == 1


def test_unexpected_programming_errors_are_not_hidden(registered_tool: SimpleNamespace) -> None:
    defect = RuntimeError("unexpected programming error")
    registered_tool.error = defect
    arguments = registered_tool.registry.validate("find_documents", {"filename_query": "资料"})
    with pytest.raises(RuntimeError) as error:
        registered_tool.registry.execute("find_documents", arguments)
    assert error.value is defect


def test_existing_tool_output_positional_arguments_keep_their_meaning() -> None:
    reranking = object()
    legacy = ToolOutput((), reranking)
    assert legacy.hits == ()
    assert legacy.reranking is reranking
    assert legacy.document_candidates is None
