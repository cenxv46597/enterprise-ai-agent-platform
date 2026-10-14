"""状态观察与确定性回答闭环；纯替身和 MockTransport，不访问数据库或真实模型。"""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.agents import document_candidates as candidates_module
from app.agents.contracts import AgentInputError, FinishAction, ToolAction, ToolExecutionError
from app.agents.document_candidates import DocumentCandidatesTool
from app.agents.engine import run_agent
from app.knowledge.document_status import DocumentProcessingStatus, DocumentStatusNotFoundError
from test_agent_engine import ControlledGeneration, ControlledPlanner, ControlledTool, hit, search
from test_document_candidates_observation import find_documents
from test_document_candidates_tool import candidate_result
from test_document_status_tool import registered_status_tool  # noqa: F401 -- 复用纯替身 fixture
from test_native_planner import call_payload, finish_payload, provider
from test_planner_provider import make_provider, payload


def status_action(document_id) -> ToolAction:
    return ToolAction(action="tool", tool_name="get_document_status",
                      arguments={"document_id": str(document_id)})


@pytest.fixture
def status_flow(registered_status_tool, monkeypatch) -> SimpleNamespace:
    context = registered_status_tool
    context.candidates = candidate_result()
    context.result = context.result.model_copy(update={
        "document_id": context.candidates.candidates[0].document_id,
        "original_filename": context.candidates.candidates[0].original_filename,
    })
    context.candidate_calls = []

    def query(session, workspace_id, knowledge_base_id, filename_query):
        context.candidate_calls.append((session, workspace_id, knowledge_base_id, filename_query))
        return context.candidates

    monkeypatch.setattr(candidates_module, "find_document_candidates", query)
    context.registry.register(DocumentCandidatesTool(context.session, context.workspace_id, context.knowledge_base_id))
    return context


@pytest.mark.parametrize("status,label", [
    ("uploaded", "已上传"), ("processing", "解析中"), ("parsed", "已解析"),
    ("ready", "可检索"), ("failed", "解析失败"),
])
def test_candidate_to_status_to_answer_preserves_two_levels_of_status(status_flow, status, label) -> None:
    status_flow.result = status_flow.result.model_copy(update={
        "status": status, "error_code": "PDF_NO_TEXT" if status == "failed" else None,
    })
    planner = ControlledPlanner([
        find_documents(), status_action(status_flow.result.document_id), FinishAction(action="finish"),
    ])
    generation = ControlledGeneration()
    run = run_agent(planner, generation, status_flow.registry, "这份资料处理好了吗？", response_mode="document_status")
    observation = planner.states[2]["observations"][1]
    assert observation["status"] == "success" and observation["error_code"] is None
    assert observation["document_status"] == status_flow.result.model_dump(mode="json")
    assert observation["document_status"]["status"] == status
    assert planner.states[0]["response_mode"] == "document_status"
    assert run.steps[1].document_status == status_flow.result and run.steps[1].evidence_ids == []
    assert run.status == "completed" and run.stop_reason is None and not run.result.abstained
    assert status_flow.result.original_filename in run.result.answer and f"{label}（{status}）" in run.result.answer
    assert run.result.citations == run.evidence == generation.calls == []
    assert status_flow.calls == [(
        status_flow.session, status_flow.workspace_id, status_flow.knowledge_base_id, status_flow.result.document_id,
    )]
    assert status_flow.candidate_calls == [(
        status_flow.session, status_flow.workspace_id, status_flow.knowledge_base_id, "资料",
    )]
    json.dumps(planner.states[2])
    if status == "failed":
        assert "没有可提取的文字" in run.result.answer
    if status == "parsed":
        assert "是否已切块需另行查询" in run.result.answer


def test_default_knowledge_mode_does_not_answer_policy_from_status_metadata(status_flow) -> None:
    planner = ControlledPlanner([status_action(status_flow.result.document_id), FinishAction(action="finish")])
    generation = ControlledGeneration()
    run = run_agent(planner, generation, status_flow.registry, "公司年假有几天？")
    assert run.status == "completed" and run.result.abstained and run.result.citations == []
    assert "response_mode" not in planner.states[0] and generation.calls == []


def test_status_metadata_does_not_replace_original_content_or_hide_search_failure(status_flow) -> None:
    status_flow.registry.register(ControlledTool([ToolExecutionError("tool_timeout")]))
    planner = ControlledPlanner([
        status_action(status_flow.result.document_id), search("年假"), FinishAction(action="finish"),
    ])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "年假规则")
    assert run.status == "failed" and run.stop_reason == "tool_error" and run.result is None


def test_knowledge_mode_still_generates_only_from_original_content(status_flow) -> None:
    original = hit("年假须提前三个工作日申请。")
    status_flow.registry.register(ControlledTool([[original]]))
    planner = ControlledPlanner([
        status_action(status_flow.result.document_id), search("年假"), FinishAction(action="finish"),
    ])
    generation = ControlledGeneration()
    run = run_agent(planner, generation, status_flow.registry, "年假怎么申请？")
    assert run.result.answer == original.content and run.result.citations[0].chunk_id == original.chunk_id
    assert [item.content for item in generation.calls[0][1]] == [original.content]


@pytest.mark.parametrize("failure,code", [
    (DocumentStatusNotFoundError("private"), "document_not_found"),
    (SQLAlchemyError("private"), "tool_service_error"),
])
def test_query_failure_after_candidates_is_not_reported_as_document_failure(status_flow, failure, code) -> None:
    status_flow.error = failure
    planner = ControlledPlanner([
        find_documents(), status_action(status_flow.result.document_id), FinishAction(action="finish"),
    ])
    generation = ControlledGeneration()
    run = run_agent(planner, generation, status_flow.registry, "处理状态", response_mode="document_status")
    assert planner.states[2]["observations"][1]["error_code"] == code
    assert run.steps[1].status == "error" and run.steps[1].document_status is None
    assert run.status == "failed" and run.stop_reason == "tool_error" and run.result is None
    assert generation.calls == [] and "private" not in str(run.steps)


@pytest.mark.parametrize("with_candidates", [False, True])
def test_finish_without_status_does_not_claim_task_completion(status_flow, with_candidates) -> None:
    decisions = ([find_documents()] if with_candidates else []) + [FinishAction(action="finish")]
    run = run_agent(ControlledPlanner(decisions), ControlledGeneration(), status_flow.registry,
                    "处理状态", response_mode="document_status")
    assert run.status == "failed" and run.stop_reason == "document_status_required" and run.result is None


@pytest.mark.parametrize("count,has_more", [(0, False), (2, False), (5, True), (1, True)])
def test_empty_or_ambiguous_candidates_stop_before_a_guessed_id_is_queried(status_flow, count, has_more) -> None:
    status_flow.candidates = candidate_result(count, has_more=has_more)
    planner = ControlledPlanner([
        find_documents(), status_action(status_flow.result.document_id), FinishAction(action="finish"),
    ])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert status_flow.calls == [] and len(planner.states) == 1
    if count == 0:
        assert run.status == "completed" and "没有匹配" in run.result.answer
    else:
        assert run.status == "waiting_for_user" and run.result is None
        assert run.pending_document_selection.candidates == status_flow.candidates


def test_single_candidate_must_match_queried_document(status_flow) -> None:
    status_flow.candidates = candidate_result()  # 与状态结果的文档不同
    planner = ControlledPlanner([
        find_documents(), status_action(status_flow.result.document_id), FinishAction(action="finish"),
    ])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert run.stop_reason == "document_selection_required" and run.result is None


def test_multiple_status_results_are_not_silently_reduced_to_last_document(status_flow) -> None:
    other_id = uuid4()

    class UpdatingPlanner(ControlledPlanner):
        def decide(self, state):
            if len(self.states) == 1:
                status_flow.result = status_flow.result.model_copy(update={"document_id": other_id})
            return super().decide(state)

    planner = UpdatingPlanner([
        status_action(status_flow.result.document_id), status_action(other_id), FinishAction(action="finish"),
    ])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert run.stop_reason == "document_selection_required" and run.result is None


def test_planner_cannot_change_answer_mode_or_saved_status_through_observation(status_flow) -> None:
    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            if len(self.states) == 1:
                state["response_mode"] = "knowledge"
                state["observations"][0]["document_status"]["status"] = "failed"
                state["observations"][0]["document_status"]["original_filename"] = "伪造文件.pdf"
            return super().decide(state)

    planner = MutatingPlanner([status_action(status_flow.result.document_id), FinishAction(action="finish")])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert run.result.answer.startswith(f"查询时，文件“{status_flow.result.original_filename}”")
    assert "ready" in run.result.answer and "伪造文件" not in run.result.answer
    assert run.steps[0].document_status.status == "ready"


def test_unknown_document_error_is_not_echoed_as_an_exception(status_flow) -> None:
    status_flow.result = status_flow.result.model_copy(update={"status": "failed", "error_code": "private-error-body"})
    planner = ControlledPlanner([status_action(status_flow.result.document_id), FinishAction(action="finish")])
    run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert "未取得可识别的失败原因" in run.result.answer and "private" not in run.result.answer


@pytest.mark.parametrize("options", [
    {"response_mode": "other"}, {"response_mode": None}, {"response_mode": True},
    {"response_mode": "document_status", "require_calculation": True},
])
def test_invalid_response_mode_precedes_any_model_call(status_flow, options) -> None:
    planner = ControlledPlanner([])
    with pytest.raises(AgentInputError):
        run_agent(planner, ControlledGeneration(), status_flow.registry, "问题", **options)
    assert planner.states == status_flow.calls == []


@pytest.mark.parametrize("status", ["processing", "failed"])
def test_native_protocol_observes_status_and_discards_fabricated_finish_text(status_flow, status) -> None:
    status_flow.result = status_flow.result.model_copy(update={
        "status": status, "error_code": "PDF_NO_TEXT" if status == "failed" else None,
    })
    bodies = []
    raw = json.dumps({"document_id": str(status_flow.result.document_id)})

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert json.loads(body["messages"][1]["content"])["response_mode"] == "document_status"
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload(name="get_document_status", raw=raw, identifier="status_1"))
        assistant, tool = body["messages"][2:4]
        assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "status_1"
        assert json.loads(tool["content"]) == {
            "status": "success", "evidence": [], "document_status": status_flow.result.model_dump(mode="json"),
        }
        return httpx.Response(200, json=finish_payload("已经处理完成，并有九十九天年假。"))

    planner, client = provider(handler)
    generation = ControlledGeneration()
    with client:
        run = run_agent(planner, generation, status_flow.registry, "处理状态", response_mode="document_status")
    assert len(bodies) == 2 and run.status == "completed" and generation.calls == []
    assert f"（{status}）" in run.result.answer and "九十九" not in run.result.answer
    assert run.steps[0].tool_call_id == "status_1" and run.result.citations == []


def test_json_provider_receives_status_observation_and_task_mode(status_flow) -> None:
    calls = []

    def handler(request):
        state = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(state)
        assert state["response_mode"] == "document_status"
        if len(calls) == 1:
            decision = status_action(status_flow.result.document_id).model_dump(exclude={"native_call"})
        else:
            assert state["observations"][0]["document_status"] == status_flow.result.model_dump(mode="json")
            decision = {"action": "finish"}
        return httpx.Response(200, json=payload(decision))

    planner, client = make_provider(handler)
    with client:
        run = run_agent(planner, ControlledGeneration(), status_flow.registry, "处理状态", response_mode="document_status")
    assert len(calls) == 2 and run.status == "completed" and "ready" in run.result.answer


def test_native_ambiguous_candidates_wait_without_another_model_or_status_call(status_flow) -> None:
    status_flow.candidates = candidate_result(2)
    requests = []

    def handler(request):
        requests.append(request)
        assert len(requests) == 1
        return httpx.Response(200, json=call_payload(name="find_documents", raw='{"filename_query":"资料"}', identifier="find_1"))

    planner, client = provider(handler)
    generation = ControlledGeneration()
    with client:
        run = run_agent(planner, generation, status_flow.registry, "文件状态", response_mode="document_status")
    assert run.status == "waiting_for_user" and run.result is None
    assert run.pending_document_selection.candidates == status_flow.candidates
    assert run.steps[0].tool_call_id == "find_1" and len(requests) == 1
    assert status_flow.calls == generation.calls == []
