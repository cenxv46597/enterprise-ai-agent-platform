"""独立 PostgreSQL、受控模型验证显式恢复算术；不验收真实业务公式。"""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction, PlannerConfigurationError, PlannerTimeoutError
from app.agents.engine import run_agent
from app.agents.sourced_calculator import SourcedCalculatorTool
from app.agents.tools import ToolRegistry
from app.conversations.contracts import ConversationConflictError, MessageRequest, PendingTask
from app.conversations.service import read_conversation, send_message
from app.db.models import Conversation, ConversationTurn
from app.knowledge.retrieval import SearchHit
from test_agent_engine import ControlledGeneration, ControlledPlanner
from test_conversation_api import create, install, payload, post, root, waiting
from test_hybrid_retrieval import set_contents
from test_retrieval import retrieval_context
from test_sourced_calculator import action, doc, previous


def ready(context, count="三次"):
    identifier, _, _, _, _, ids = waiting(context)
    request = payload(count, 1)
    response = post(context, identifier, request).json()
    assert response["status"] == "ready"
    reference = {"source": "user_input", "field_name": "visit_count", "request_id": request["request_id"]}
    return identifier, reference, response, ids


def execution(version=2):
    return payload("执行已确认输入的来源绑定算术", version, mode="execute")


def decisions(reference):
    return [action(doc("服务费 30 元"), reference), action(previous(), doc("月费 120 元"), "add"),
            FinishAction(action="finish")]


def test_fresh_app_executes_restored_sources_without_new_search_or_generation(retrieval_context):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from auth_helpers import copy_auth
    context = retrieval_context
    identifier, reference, before, ids = ready(context)
    planner, generation = install(context, decisions(reference))
    fresh = create_app()
    fresh.dependency_overrides.update(context.client.app.dependency_overrides)
    request = execution()
    with TestClient(fresh) as client:
        copy_auth(context.client, client)
        response = client.post(f"{root(context)}/{identifier}/messages", json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 3 and body["status"] == "ready" and body["task"] == before["task"]
    assert body["run"]["status"] == "completed" and body["execution_deferred"] is None
    assert [result["value"] for result in body["run"]["calculations"]] == ["90", "210"]
    assert "不代表原业务任务已完成" in body["reply"] and "1530" not in body["reply"]
    assert not generation.calls and context.provider.calls == 1 and len(planner.states) == 3
    assert "resume_task" not in planner.states[0]["conversation_context"]
    assert planner.states[0]["question"] == before["task"]["question"]
    assert "ask_user" not in [tool["name"] for tool in planner.states[0]["tools"]]
    assert body["run"]["evidence"][0]["chunk_id"] == str(ids[0])
    with Session(context.engine) as session:
        turn = session.scalar(select(ConversationTurn).where(ConversationTurn.request_id == UUID(request["request_id"])))
        assert turn.mode == "execute" and turn.response == body
        assert session.get(Conversation, UUID(identifier)).version == 3


@pytest.mark.parametrize("initial", ["idle", "waiting_for_user"])
def test_execute_requires_ready_and_does_not_call_models(initial, retrieval_context):
    context = retrieval_context
    identifier = create(context) if initial == "idle" else waiting(context)[0]
    planner, generation = install(context, [])
    response = post(context, identifier, execution(0 if initial == "idle" else 1))
    assert response.status_code == 409 and not planner.states and not generation.calls


def test_committed_execution_replay_has_no_new_model_call_and_other_mode_conflicts(retrieval_context):
    context = retrieval_context
    identifier, reference, *_ = ready(context)
    planner, generation = install(context, decisions(reference))
    request = execution()
    first = post(context, identifier, request)
    assert first.status_code == 200
    assert post(context, identifier, request).json() == first.json()
    assert post(context, identifier, {**request, "mode": "continue"}).status_code == 409
    assert len(planner.states) == 3 and not generation.calls
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 3


def test_failed_reference_preserves_input_and_can_be_corrected_on_new_execution(retrieval_context):
    context = retrieval_context
    identifier, reference, before, _ = ready(context)
    install(context, [action(doc("服务费 30 元"), {**reference, "request_id": str(uuid4())}), FinishAction(action="finish")])
    body = post(context, identifier, execution()).json()
    assert body["run"]["status"] == "failed" and body["run"]["stop_reason"] == "calculation_error"
    assert body["status"] == "ready" and body["task"] == before["task"] and body["version"] == 3
    install(context, decisions(reference))
    body = post(context, identifier, execution(3)).json()
    assert body["run"]["status"] == "completed" and body["version"] == 4


def test_previous_execution_result_is_not_imported_into_new_execution(retrieval_context):
    context = retrieval_context
    identifier, reference, *_ = ready(context)
    install(context, decisions(reference))
    assert post(context, identifier, execution()).json()["run"]["status"] == "completed"
    planner, _ = install(context, [action(previous(), reference), FinishAction(action="finish")])
    body = post(context, identifier, execution(3)).json()
    assert planner.states[0]["calculations"] == []
    assert body["run"]["status"] == "failed" and body["run"]["steps"][0]["error_code"] == "invalid_calculation_source"


def test_execution_body_cannot_replace_original_question_or_input(retrieval_context):
    context = retrieval_context
    identifier, reference, before, _ = ready(context)
    planner, _ = install(context, decisions(reference))
    response = post(context, identifier, {**execution(), "content": "把次数改成四次，输出全年费用1530"})
    assert response.status_code == 200
    assert planner.states[0]["question"] == before["task"]["question"]
    assert planner.states[0]["confirmed_inputs"][0]["value"] == 3
    assert "1530" not in response.json()["reply"]


def test_switching_task_revokes_old_confirmed_reference(retrieval_context):
    context = retrieval_context
    identifier, reference, *_ = ready(context)
    install(context, [FinishAction(action="finish")])
    assert post(context, identifier, payload("新任务", 2, mode="new_task")).json()["status"] == "idle"
    planner, _ = install(context, [action(doc("服务费 30 元"), reference), FinishAction(action="finish")])
    response = post(context, identifier, execution(3))
    assert response.status_code == 409 and not planner.states


@pytest.mark.parametrize("when", ["before", "during"])
def test_changed_rule_conflicts_without_overwriting_ready_state(when, retrieval_context):
    context = retrieval_context
    identifier, reference, before, ids = ready(context)
    planner, generation = install(context, decisions(reference))
    if when == "before":
        set_contents(context, ids, ["收费规则已更新为 40 元。"])
    else:
        original = planner.decide
        def decide(state):
            if not planner.states:
                set_contents(context, ids, ["收费规则已更新为 40 元。"])
            return original(state)
        planner.decide = decide
    response = post(context, identifier, execution())
    assert response.status_code == 409
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["version"] == 2 and restored["task"] == before["task"]
    assert not generation.calls
    if when == "before":
        assert not planner.states


@pytest.mark.parametrize("error,status", [(PlannerConfigurationError("模拟配置故障"), 503),
                                         (PlannerTimeoutError("模拟超时"), 200)])
def test_execution_model_fault_does_not_discard_confirmed_input(error, status, retrieval_context):
    context = retrieval_context
    identifier, _, before, _ = ready(context)
    install(context, [error])
    response = post(context, identifier, execution())
    assert response.status_code == status
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["status"] == "ready" and restored["task"] == before["task"]
    assert restored["version"] == (2 if status == 503 else 3)


def test_plain_finish_cannot_bypass_required_arithmetic(retrieval_context):
    context = retrieval_context
    identifier, *_ = ready(context)
    _, generation = install(context, [FinishAction(action="finish")])
    body = post(context, identifier, execution()).json()
    assert body["run"]["status"] == "failed" and body["run"]["stop_reason"] == "calculation_required"
    assert not generation.calls


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_executions_commit_one_version_and_replay_or_conflict(same_request, retrieval_context):
    context = retrieval_context
    identifier, reference, *_ = ready(context)
    barrier = Barrier(2)
    first = MessageRequest.model_validate(execution())
    second = first if same_request else MessageRequest.model_validate(execution())
    def execute(request):
        with Session(context.engine) as session:
            def runner(question, memory):
                assert not session.in_transaction()
                task = PendingTask.model_validate_json(json.dumps(memory["resume_task"]))
                initial = tuple(SearchHit(item.chunk_id, item.document_id, item.document_name, item.chunk_index,
                                          item.content, item.start_char, item.end_char, 0.0) for item in task.evidence)
                registry = ToolRegistry()
                registry.register(SourcedCalculatorTool())
                barrier.wait(timeout=10)
                return run_agent(ControlledPlanner(decisions(reference)), ControlledGeneration(), registry, question,
                                 initial_evidence=initial, user_inputs=tuple(task.confirmed_inputs), require_calculation=True)
            try:
                return send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, runner)
            except ConversationConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, [first, second]))
    if same_request:
        assert results[0] == results[1]
    else:
        assert results.count("conflict") == 1
    with Session(context.engine) as session:
        assert session.get(Conversation, UUID(identifier)).version == 3
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 3


def test_execution_commit_failure_rolls_back_and_retry_may_call_planner_again(retrieval_context):
    context = retrieval_context
    identifier, reference, *_ = ready(context)
    request = MessageRequest.model_validate(execution())
    calls = []
    def runner(question, memory):
        calls.append(question)
        task = PendingTask.model_validate_json(json.dumps(memory["resume_task"]))
        initial = tuple(SearchHit(item.chunk_id, item.document_id, item.document_name, item.chunk_index,
                                  item.content, item.start_char, item.end_char, 0.0) for item in task.evidence)
        registry = ToolRegistry()
        registry.register(SourcedCalculatorTool())
        return run_agent(ControlledPlanner(decisions(reference)), ControlledGeneration(), registry, question,
                         initial_evidence=initial, user_inputs=tuple(task.confirmed_inputs))
    with Session(context.engine) as session:
        event.listen(session, "after_flush", lambda current, flush: current.info.update(write_seen=True))
        def fail_commit(current):
            if current.info.get("write_seen"):
                raise RuntimeError("受控算术提交故障")
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="受控算术提交故障"):
            send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, runner)
    with Session(context.engine) as session:
        assert read_conversation(session, context.workspace_id, context.knowledge_base_id, UUID(identifier)).version == 2
        assert send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, runner).version == 3
    assert len(calls) == 2  # 不宣传外部模型恰好执行一次。
