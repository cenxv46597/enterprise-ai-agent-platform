"""文档状态工具的参数、绑定范围与安全错误；纯替身，无数据库或模型。"""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.agents import document_status as tool_module
from app.agents.contracts import ToolExecutionError, ToolOutput
from app.agents.document_status import DocumentStatusArguments, DocumentStatusTool
from app.agents.tools import ToolRegistry
from app.knowledge.document_candidates import DocumentFilenameQuery
from app.knowledge.document_status import DocumentProcessingStatus, DocumentStatusNotFoundError
from test_document_candidates_tool import candidate_result


DOCUMENT_ID = "12345678-1234-4234-8234-123456789abc"


@pytest.fixture
def registered_status_tool(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = SimpleNamespace(
        session=object(), workspace_id=uuid4(), knowledge_base_id=uuid4(), calls=[], error=None,
        result=DocumentProcessingStatus(
            document_id=UUID(DOCUMENT_ID), original_filename="请假规定.txt", status="ready", error_code=None,
        ),
    )

    def query(session, workspace_id, knowledge_base_id, document_id):
        context.calls.append((session, workspace_id, knowledge_base_id, document_id))
        if context.error is not None:
            raise context.error
        return context.result

    monkeypatch.setattr(tool_module, "get_document_status", query)
    context.tool = DocumentStatusTool(context.session, context.workspace_id, context.knowledge_base_id)
    context.registry = ToolRegistry()
    context.registry.register(context.tool)
    return context


def test_registry_schema_exposes_only_document_id(registered_status_tool) -> None:
    definition, = registered_status_tool.registry.definitions()
    assert definition["name"] == "get_document_status"
    assert registered_status_tool.tool.argument_schema is DocumentStatusArguments
    assert set(definition["parameters"]["properties"]) == {"document_id"}
    assert definition["parameters"]["required"] == ["document_id"]
    assert definition["parameters"]["additionalProperties"] is False


def test_uuid_string_normalization_binding_and_json_arguments(registered_status_tool) -> None:
    arguments = registered_status_tool.registry.validate(
        "get_document_status", {"document_id": f"  {DOCUMENT_ID.upper()}  "},
    )
    assert arguments.document_id == DOCUMENT_ID
    # 引擎的动作 fingerprint 使用普通 model_dump 后 json.dumps，参数不能提前变为 UUID。
    assert json.loads(json.dumps(arguments.model_dump())) == {"document_id": DOCUMENT_ID}
    output = registered_status_tool.registry.execute("get_document_status", arguments)
    assert registered_status_tool.calls == [(
        registered_status_tool.session, registered_status_tool.workspace_id,
        registered_status_tool.knowledge_base_id, UUID(DOCUMENT_ID),
    )]
    assert output.document_status == registered_status_tool.result


@pytest.mark.parametrize("status,error_code", [("ready", None), ("failed", "PDF_NO_TEXT")])
def test_processing_status_is_normal_metadata_not_content(registered_status_tool, status, error_code) -> None:
    registered_status_tool.result = DocumentProcessingStatus(
        document_id=UUID(DOCUMENT_ID), original_filename="请假规定.txt", status=status, error_code=error_code,
    )
    arguments = registered_status_tool.registry.validate("get_document_status", {"document_id": DOCUMENT_ID})
    output = registered_status_tool.registry.execute("get_document_status", arguments)
    assert output.hits == () and output.document_candidates is None
    assert output.calculation is None and output.clarification is None
    wire = output.document_status.model_dump(mode="json")
    assert json.loads(json.dumps(wire)) == {
        "document_id": DOCUMENT_ID, "original_filename": "请假规定.txt",
        "status": status, "error_code": error_code,
    }


@pytest.mark.parametrize("field", ["sql", "workspace_id", "knowledge_base_id"])
def test_extra_parameters_are_rejected_without_query(registered_status_tool, field) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_status_tool.registry.validate(
            "get_document_status", {"document_id": DOCUMENT_ID, field: "injected"},
        )
    assert error.value.code == "invalid_arguments"
    assert registered_status_tool.calls == []


@pytest.mark.parametrize("arguments", [
    {},
    {"document_id": True},
    {"document_id": UUID(DOCUMENT_ID)},
    {"document_id": "   "},
    {"document_id": "not-a-uuid"},
    {"document_id": DOCUMENT_ID.replace("-", "")},
    {"document_id": "g2345678-1234-4234-8234-123456789abc"},
])
def test_invalid_uuid_arguments_are_rejected_without_query(registered_status_tool, arguments) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_status_tool.registry.validate("get_document_status", arguments)
    assert error.value.code == "invalid_arguments"
    assert registered_status_tool.calls == []


@pytest.mark.parametrize("failure,code", [
    (DocumentStatusNotFoundError("private not-found details"), "document_not_found"),
    (SQLAlchemyError("private database details"), "tool_service_error"),
])
def test_not_found_and_database_errors_are_sanitized(registered_status_tool, failure, code) -> None:
    registered_status_tool.error = failure
    arguments = registered_status_tool.registry.validate("get_document_status", {"document_id": DOCUMENT_ID})
    with pytest.raises(ToolExecutionError) as error:
        registered_status_tool.registry.execute("get_document_status", arguments)
    assert error.value.code == str(error.value) == code
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    assert len(registered_status_tool.calls) == 1


def test_wrong_argument_model_is_rejected(registered_status_tool) -> None:
    with pytest.raises(ToolExecutionError) as error:
        registered_status_tool.tool.execute(DocumentFilenameQuery(filename_query="请假规定"))
    assert error.value.code == "invalid_arguments"
    assert registered_status_tool.calls == []


def test_constructed_arguments_are_revalidated(registered_status_tool) -> None:
    forged = DocumentStatusArguments.model_construct(document_id="not-a-uuid")
    with pytest.raises(ToolExecutionError) as error:
        registered_status_tool.tool.execute(forged)
    assert error.value.code == "invalid_arguments"
    assert registered_status_tool.calls == []


def test_unexpected_programming_errors_are_not_hidden(registered_status_tool) -> None:
    defect = RuntimeError("unexpected programming error")
    registered_status_tool.error = defect
    arguments = registered_status_tool.registry.validate("get_document_status", {"document_id": DOCUMENT_ID})
    with pytest.raises(RuntimeError) as error:
        registered_status_tool.registry.execute("get_document_status", arguments)
    assert error.value is defect


def test_existing_candidates_positional_output_remains_compatible() -> None:
    candidates = candidate_result()
    legacy = ToolOutput((), None, None, None, None, (), candidates)
    assert legacy.document_candidates == candidates
    assert legacy.document_status is None and legacy.hits == ()
