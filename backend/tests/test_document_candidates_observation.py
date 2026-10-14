"""候选元数据的 JSON/native 观察与原文隔离；纯替身，无数据库或真实模型。"""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.agents import document_candidates as tool_module
from app.agents.contracts import AgentStep, FinishAction, ToolAction, ToolExecutionError
from app.agents.document_candidates import DocumentCandidatesTool
from app.agents.engine import run_agent
from app.agents.tools import ToolRegistry
from test_agent_engine import ControlledGeneration, ControlledPlanner, ControlledTool, hit, search
from test_document_candidates_tool import candidate_result
from test_native_planner import call_payload, finish_payload, provider


def find_documents(query: str = "  资料  ") -> ToolAction:
    return ToolAction(action="tool", tool_name="find_documents", arguments={"filename_query": query})


@pytest.fixture
def candidates_run(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = SimpleNamespace(result=candidate_result(), calls=[], registry=ToolRegistry())

    def query(session, workspace_id, knowledge_base_id, filename_query):
        context.calls.append(filename_query)
        return context.result

    monkeypatch.setattr(tool_module, "find_document_candidates", query)
    context.registry.register(DocumentCandidatesTool(object(), uuid4(), uuid4()))
    return context


@pytest.mark.parametrize("count,has_more", [(0, False), (1, False), (5, True)])
def test_json_observes_candidates_and_finish_abstains_without_content(candidates_run, count, has_more) -> None:
    candidates_run.result = candidate_result(count, has_more=has_more)
    planner = ControlledPlanner([find_documents(), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, candidates_run.registry, "公司年假如何申请？")
    observation = planner.states[1]["observations"][0]
    wire = candidates_run.result.model_dump(mode="json")
    assert observation["document_candidates"] == wire
    json.dumps(planner.states[1])
    if count:
        candidate = candidates_run.result.candidates[0]
        assert wire["candidates"][0]["document_id"] == str(candidate.document_id)
        assert datetime.fromisoformat(wire["candidates"][0]["created_at"]) == candidate.created_at
    assert observation["query"] == result.steps[0].query == "资料"
    assert result.steps[0].status == "success"
    assert result.steps[0].document_candidates == candidates_run.result
    assert result.steps[0].evidence_ids == []
    assert planner.states[1]["evidence"] == result.evidence == []
    assert result.status == "completed" and result.result.abstained
    assert result.result.citations == [] and generation.calls == []
    assert candidates_run.calls == ["资料"]


def test_candidates_then_search_uses_only_original_content_and_citations(candidates_run) -> None:
    original = hit("年假须提前三个工作日申请。")
    candidates_run.registry.register(ControlledTool([[original]]))
    planner = ControlledPlanner([find_documents(), search("年假规则"), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, candidates_run.registry, "公司年假如何申请？")
    assert result.status == "completed" and not result.result.abstained
    assert len(result.evidence) == len(result.result.citations) == 1
    assert result.evidence[0].chunk_id == result.result.citations[0].chunk_id == original.chunk_id
    assert [source.content for source in generation.calls[0][1]] == [original.content]
    assert result.steps[0].evidence_ids == [] and result.steps[1].evidence_ids == [1]
    assert result.steps[1].document_candidates is None
    assert planner.states[2]["evidence"] == [{"evidence_id": 1, "content": original.content}]


def test_metadata_success_cannot_hide_a_failed_content_search(candidates_run) -> None:
    candidates_run.registry.register(ControlledTool([ToolExecutionError("tool_timeout")]))
    planner = ControlledPlanner([find_documents(), search("年假规则"), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, candidates_run.registry, "公司年假如何申请？")
    assert result.steps[0].status == "success" and result.steps[0].document_candidates is not None
    assert result.steps[1].error_code == "tool_timeout"
    assert result.status == "failed" and result.stop_reason == "tool_error"
    assert result.result is None and result.evidence == [] and generation.calls == []


def test_planner_cannot_mutate_candidate_steps_through_observation_copies(candidates_run) -> None:
    expected = candidates_run.result.model_dump(mode="json")

    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            if len(self.states) == 1:
                observed = state["observations"][0]["document_candidates"]
                observed["candidates"][0]["original_filename"] = "被修改的名字.txt"
                observed["has_more"] = True
            elif len(self.states) == 2:
                assert state["observations"][0]["document_candidates"] == expected
            return super().decide(state)

    planner = MutatingPlanner([find_documents("资料"), find_documents("其他资料"), FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), candidates_run.registry, "查找文件候选")
    assert result.steps[0].document_candidates.model_dump(mode="json") == expected
    assert result.steps[1].document_candidates.model_dump(mode="json") == expected
    assert len(candidates_run.calls) == 2


@pytest.mark.parametrize("count,has_more", [(0, False), (1, False), (5, True)])
def test_native_provider_receives_paired_metadata_without_content(candidates_run, count, has_more) -> None:
    candidates_run.result = candidate_result(count, has_more=has_more)
    bodies = []
    raw = json.dumps({"filename_query": "  资料  "}, ensure_ascii=False)

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload(name="find_documents", raw=raw, identifier="find_1"))
        assistant, tool = body["messages"][2:4]
        call = assistant["tool_calls"][0]
        assert call["id"] == tool["tool_call_id"] == "find_1"
        assert call["function"] == {"name": "find_documents", "arguments": raw}
        assert tool["role"] == "tool"
        assert json.loads(tool["content"]) == {
            "status": "success", "evidence": [],
            "document_candidates": candidates_run.result.model_dump(mode="json"),
        }
        return httpx.Response(200, json=finish_payload("年假共九十九天。"))

    planner, client = provider(handler)
    generation = ControlledGeneration()
    with client:
        result = run_agent(planner, generation, candidates_run.registry, "公司年假如何申请？")
    assert len(bodies) == 2 and candidates_run.calls == ["资料"]
    assert result.steps[0].tool_call_id == "find_1" and result.steps[0].query == "资料"
    assert result.steps[0].document_candidates == candidates_run.result
    assert result.evidence == [] and result.result.abstained
    assert result.result.citations == [] and generation.calls == []
    assert "九十九" not in result.result.answer


def test_existing_tool_observation_shape_and_step_defaults_remain_compatible() -> None:
    step = AgentStep(step=1, action="finish", status="success", planner_ms=0.0)
    assert step.document_candidates is None
    registry = ToolRegistry()
    registry.register(ControlledTool([[hit()]]))
    planner = ControlledPlanner([search("年假"), FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), registry, "公司年假如何申请？")
    assert result.steps[0].document_candidates is None
    assert set(planner.states[1]["observations"][0]) == {
        "step", "action", "tool_name", "tool_call_id", "query", "status", "error_code",
        "evidence_ids", "calculation_id",
    }
