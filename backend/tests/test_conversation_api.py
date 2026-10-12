"""真实独立 PostgreSQL 验证跨请求接续；模型使用替身和受控 HTTP。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction, PlannerConfigurationError, PlannerTimeoutError
from app.agents.engine import run_agent
from app.agents.tools import ToolRegistry
from app.api import agent_runs as agent_api
from app.api import answers as answers_api
from app.conversations.contracts import ConversationConflictError, MessageRequest
from app.conversations.service import read_conversation, send_message
from app.db.models import Conversation, ConversationTurn
from test_agent_engine import ControlledGeneration, ControlledPlanner, search
from test_clarification_agent import ask
from test_hybrid_retrieval import set_contents
from test_retrieval import retrieval_context, vector


SOURCE = "月费 120 元，每次上门服务费 30 元。"
QUESTION = "全年费用是多少？"


def root(context, **options):
    return context.url(**options).removesuffix("/search") + "/conversations"


def install(context, decisions):
    planner, generation = ControlledPlanner(decisions), ControlledGeneration()
    context.client.app.dependency_overrides[agent_api.get_planner_provider] = lambda: planner
    context.client.app.dependency_overrides[answers_api.get_generation_provider] = lambda: generation
    return planner, generation


def create(context):
    response = context.client.post(root(context))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "idle" and body["version"] == 0 and body["task"] is None
    return body["conversation_id"]


def payload(text=QUESTION, version=0, **overrides):
    return {"request_id": str(uuid4()), "expected_version": version, "content": text, **overrides}


def post(context, identifier, body, **options):
    return context.client.post(f"{root(context, **options)}/{identifier}/messages", json=body)


def waiting(context):
    _, ids = context.document([vector(1)])
    set_contents(context, ids, [SOURCE])
    planner, generation = install(context, [search("收费标准"), ask()])
    identifier, body = create(context), payload()
    response = post(context, identifier, body)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "waiting_for_user" and result["version"] == 1
    assert result["run"]["status"] == "waiting_for_user" and result["run"]["result"] is None
    assert "pending_task" not in result["run"] and "content" not in result["task"]["evidence"][0]
    return identifier, body, result, planner, generation, ids


def test_wait_persists_and_three_resumes_using_a_new_app_and_database_session(retrieval_context):
    from fastapi.testclient import TestClient
    from app.main import create_app
    context = retrieval_context
    identifier, _, first, planner, generation, ids = waiting(context)
    # 新应用对象没有旧控制器或全局内存；沿用的只有数据库连接依赖与模型替身。
    fresh_app = create_app()
    fresh_app.dependency_overrides.update(context.client.app.dependency_overrides)
    with TestClient(fresh_app) as fresh_client:
        restored = fresh_client.get(f"{root(context)}/{identifier}")
        assert restored.status_code == 200
        restored = restored.json()
        assert restored["task"]["question"] == QUESTION
        assert restored["task"]["clarification"]["field_name"] == "visit_count"
        assert restored["recent_messages"][0]["content"] == QUESTION
        body = payload("三次", 1)
        response = fresh_client.post(f"{root(context)}/{identifier}/messages", json=body)
        assert response.status_code == 200
        result = response.json()
    assert result["status"] == "ready" and result["version"] == 2
    assert result["task"]["question"] == QUESTION
    user_input = result["task"]["confirmed_inputs"][0]
    assert user_input == {"field_name": "visit_count", "value": 3, "unit": "次",
                          "raw_text": "三次", "request_id": body["request_id"], "provenance": "user_message"}
    assert result["task"]["evidence"][0]["chunk_id"] == str(ids[0])
    assert result["execution_deferred"] == "explicit_execution_required" and result["run"] is None
    assert "暂未执行总额计算" in result["reply"] and "1530" not in result["reply"]
    assert len(planner.states) == 2 and context.provider.calls == 1 and not generation.calls
    with Session(context.engine) as session:
        row = session.get(Conversation, UUID(identifier))
        assert row.task["evidence"][0]["content"] == SOURCE
        assert row.task["confirmed_inputs"][0]["provenance"] == "user_message"
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 2


@pytest.mark.parametrize("text", ["三个月", "三次或四次", "-1次", "大概三次", "换个问题"])
def test_invalid_reply_keeps_original_wait_and_does_not_call_models(retrieval_context, text):
    context = retrieval_context
    identifier, _, first, planner, generation, _ = waiting(context)
    response = post(context, identifier, payload(text, 1))
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "waiting_for_user" and result["task"] == first["task"]
    assert result["version"] == 2 and "仍需补充全年上门次数" in result["reply"]
    assert len(planner.states) == 2 and not generation.calls and context.provider.calls == 1


def test_zero_is_a_valid_count_not_missing_information(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    result = post(context, identifier, payload("零次", 1)).json()
    assert result["status"] == "ready" and result["task"]["confirmed_inputs"][0]["value"] == 0


def test_ready_does_not_implicitly_reinterpret_another_reply_or_execute_calculation(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    assert post(context, identifier, payload("三次", 1)).json()["status"] == "ready"
    response = post(context, identifier, payload("四次", 2))
    assert response.status_code == 409
    assert context.client.get(f"{root(context)}/{identifier}").json()["version"] == 2


def test_explicit_new_task_replaces_pending_slot_and_resets_planner_context(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    planner, generation = install(context, [search("服务资料"), FinishAction(action="finish")])
    result = post(context, identifier, payload("收费规则是什么？", 1, mode="new_task")).json()
    assert result["status"] == "idle" and result["task"] is None
    assert result["run"]["status"] == "completed" and len(generation.calls) == 1
    assert planner.states[0]["conversation_context"] == {"recent_messages": []}
    planner, _ = install(context, [FinishAction(action="finish")])
    assert post(context, identifier, payload("还有其他规则？", 2)).status_code == 200
    memory = planner.states[0]["conversation_context"]["recent_messages"]
    assert [item["content"] for item in memory] == ["收费规则是什么？", result["reply"]]


def test_regular_followup_passes_bounded_history_to_planner(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    identifier = create(context)
    install(context, [search(), FinishAction(action="finish")])
    first = post(context, identifier, payload("退款规则？")).json()
    planner, _ = install(context, [FinishAction(action="finish")])
    second = post(context, identifier, payload("还有哪些条件？", 1)).json()
    assert second["status"] == "idle"
    memory = planner.states[0]["conversation_context"]["recent_messages"]
    assert memory == [{"role": "user", "content": "退款规则？"}, {"role": "assistant", "content": first["reply"]}]
    assert planner.states[0]["evidence"] == []  # 旧回复不变成当前企业证据。


def test_committed_retry_returns_original_result_without_new_calls_or_turn(retrieval_context):
    context = retrieval_context
    identifier, original, first, planner, generation, _ = waiting(context)
    retry = post(context, identifier, original)
    assert retry.status_code == 200 and retry.json() == first
    assert len(planner.states) == 2 and not generation.calls and context.provider.calls == 1
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1


def test_supplement_retry_is_replayed_even_after_state_is_ready(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    body = payload("三次", 1)
    first = post(context, identifier, body).json()
    assert post(context, identifier, body).json() == first


def test_request_id_reuse_with_other_content_or_version_is_conflict(retrieval_context):
    context = retrieval_context
    identifier, original, *_ = waiting(context)
    for changes in ({"content": "其他问题"}, {"expected_version": 1}):
        assert post(context, identifier, {**original, **changes}).status_code == 409


def test_stale_version_precedes_planning_and_keeps_wait(retrieval_context):
    context = retrieval_context
    identifier, _, _, planner, generation, _ = waiting(context)
    assert post(context, identifier, payload("三次", 0)).status_code == 409
    assert len(planner.states) == 2 and not generation.calls


@pytest.mark.parametrize("mismatch", ["knowledge_base", "workspace", "conversation"])
def test_scope_mismatch_rejected_before_models_for_read_and_write(retrieval_context, mismatch):
    context = retrieval_context
    identifier = create(context)
    planner, generation = install(context, [])
    options = {}
    if mismatch == "knowledge_base":
        options["knowledge_base_id"] = context.knowledge_base()
    elif mismatch == "workspace":
        options["workspace_id"] = context.workspace()
    else:
        identifier = str(uuid4())
    assert context.client.get(f"{root(context, **options)}/{identifier}").status_code == 404
    assert post(context, identifier, payload(), **options).status_code == 404
    assert not planner.states and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("changes", [{"content": " "}, {"expected_version": True}, {"request_id": "bad"},
    {"mode": "guess"}, {"content": 3}, {"extra": "not-allowed"}])
def test_invalid_request_rejected_before_models(retrieval_context, changes):
    context = retrieval_context
    identifier = create(context)
    planner, generation = install(context, [])
    assert post(context, identifier, payload(**changes)).status_code == 422
    assert not planner.states and not generation.calls


def test_model_failure_is_persisted_as_failed_run_not_waiting_or_quality_failure(retrieval_context):
    context = retrieval_context
    identifier = create(context)
    install(context, [PlannerTimeoutError("private-output")])
    response = post(context, identifier, payload())
    assert response.status_code == 200 and "private-output" not in response.text
    result = response.json()
    assert result["status"] == "idle" and result["task"] is None
    assert result["run"]["status"] == "failed" and result["run"]["stop_reason"] == "planner_timeout"


def test_config_failure_during_explicit_new_task_does_not_erase_waiting_state(retrieval_context):
    context = retrieval_context
    identifier, _, first, *_ = waiting(context)
    install(context, [PlannerConfigurationError("private")])
    assert post(context, identifier, payload("新问题", 1, mode="new_task")).status_code == 503
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["version"] == 1 and restored["task"] == first["task"]


def test_evidence_change_during_wait_rejects_stale_binding(retrieval_context):
    context = retrieval_context
    identifier, _, first, _, _, ids = waiting(context)
    set_contents(context, ids, ["月费 999 元，每次上门服务费 30 元。"])
    response = post(context, identifier, payload("三次", 1))
    assert response.status_code == 409
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["status"] == "waiting_for_user" and restored["version"] == 1
    assert restored["task"] == first["task"]


def test_commit_failure_rolls_back_task_input_and_turn_then_retry_succeeds(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    request = MessageRequest.model_validate(payload("三次", 1))
    def no_runner(*args):
        pytest.fail("补充次数不应调用模型")
    with Session(context.engine) as session:
        def mark_write(current, flush_context):
            current.info["write_seen"] = True
        def fail_commit(current):
            if current.info.get("write_seen"):
                raise RuntimeError("受控提交故障")
        event.listen(session, "after_flush", mark_write)
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="受控提交故障"):
            send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, no_runner)
    with Session(context.engine) as session:
        restored = read_conversation(session, context.workspace_id, context.knowledge_base_id, UUID(identifier))
        assert restored.status == "waiting_for_user" and restored.version == 1
        assert restored.task.confirmed_inputs == []
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
        session.rollback()
        result = send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, no_runner)
        assert result.status == "ready" and result.version == 2


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_first_turns_use_version_cas_and_replay(same_request, retrieval_context):
    context = retrieval_context
    identifier = UUID(create(context))
    barrier = Barrier(2)
    first = MessageRequest.model_validate(payload("第一问"))
    second = first if same_request else MessageRequest.model_validate(payload("第二问"))
    def execute(request):
        with Session(context.engine) as session:
            def runner(question, memory):
                assert not session.in_transaction()
                barrier.wait(timeout=10)
                return run_agent(ControlledPlanner([FinishAction(action="finish")]), ControlledGeneration(),
                                 ToolRegistry(), question)
            try:
                return send_message(session, context.workspace_id, context.knowledge_base_id, identifier, request, runner)
            except ConversationConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, [first, second]))
    if same_request:
        assert results[0] == results[1] and results[0].version == 1
    else:
        assert results.count("conflict") == 1
    with Session(context.engine) as session:
        assert session.get(Conversation, identifier).version == 1
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1


def test_turn_limit_stops_before_planning(retrieval_context):
    context = retrieval_context
    identifier = create(context)
    with Session(context.engine) as session, session.begin():
        session.execute(update(Conversation).where(Conversation.id == UUID(identifier)).values(version=200))
    planner, generation = install(context, [])
    assert post(context, identifier, payload(version=200)).status_code == 409
    assert not planner.states and not generation.calls


def test_invalid_persisted_task_returns_safe_error(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    with Session(context.engine) as session, session.begin():
        session.execute(update(Conversation).where(Conversation.id == UUID(identifier)).values(task={"private": "secret-data"}))
    response = context.client.get(f"{root(context)}/{identifier}")
    assert response.status_code == 500 and "secret-data" not in response.text
