"""独立 PostgreSQL 与受控模型验证固定计费工作流；不验收真实模型质量。"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction, ToolAction
from app.conversations.contracts import ConversationConflictError, MessageRequest
from app.conversations.service import read_conversation, send_message
from app.db.models import Conversation, ConversationTurn
from test_agent_engine import search
from test_conversation_api import SOURCE, QUESTION, create, install, payload, post, root, waiting
from test_hybrid_retrieval import set_contents
from test_retrieval import retrieval_context, vector


def prepare(source=SOURCE):
    return ToolAction(action="tool", tool_name="prepare_billing",
                      arguments={"evidence_id": 1, "rule_quote": source})


def billing_wait(context, source=SOURCE):
    _, ids = context.document([vector(1)])
    set_contents(context, ids, [source])
    planner, generation = install(context, [search("收费标准"), prepare(source)])
    identifier, request = create(context), payload()
    response = post(context, identifier, request)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "waiting_for_user" and body["version"] == 1
    assert body["task"]["clarification"]["field_name"] == "visit_count"
    assert body["task"]["billing_plan"]["workflow"] == "monthly_visit_v1"
    return identifier, request, body, planner, generation, ids


def billing_ready(context, source=SOURCE, visits="三次", months="十二个月"):
    identifier, _, _, _, _, ids = billing_wait(context, source)
    visit_request = payload(visits, 1)
    first = post(context, identifier, visit_request)
    assert first.status_code == 200 and first.json()["status"] == "waiting_for_user"
    month_request = payload(months, 2)
    second = post(context, identifier, month_request)
    assert second.status_code == 200 and second.json()["status"] == "ready"
    return identifier, second.json(), visit_request, month_request, ids


def execution(version=3, **changes):
    return payload("确认按已展示的标准规则估算并执行", version,
                   mode="execute", confirm_billing_rule=True, **changes)


def no_runner(*args):
    pytest.fail("补充业务条件或固定计费执行不应调用模型")


def test_three_binds_to_visit_count_then_waits_for_explicit_months(retrieval_context):
    context = retrieval_context
    identifier, _, first, planner, generation, ids = billing_wait(context)
    requirements = first["task"]["clarifications"]
    assert [(item["field_name"], item["unit"], item["min_value"], item["max_value"])
            for item in requirements] == [("visit_count", "次", 0, 9999), ("billing_months", "个月", 1, 12)]
    assert first["task"]["confirmed_inputs"] == [] and first["billing_result"] is None
    request = payload("三次", 1)
    result = post(context, identifier, request).json()
    assert result["status"] == "waiting_for_user" and result["version"] == 2
    assert result["task"]["question"] == QUESTION
    assert result["task"]["clarification"]["field_name"] == "billing_months"
    assert result["task"]["confirmed_inputs"] == [{
        "field_name": "visit_count", "value": 3, "unit": "次", "request_id": request["request_id"],
        "raw_text": "三次", "provenance": "user_message",
    }]
    assert result["task"]["evidence"][0]["chunk_id"] == str(ids[0])
    assert "content" not in result["task"]["evidence"][0]
    assert result["run"] is None and result["billing_result"] is None
    assert "1530" not in result["reply"]
    assert len(planner.states) == 2 and context.provider.calls == 1 and not generation.calls


def test_explicit_months_makes_ready_without_implicit_execution(retrieval_context):
    context = retrieval_context
    identifier, before, visit_request, month_request, _ = billing_ready(context)
    assert before["version"] == 3 and before["run"] is None and before["billing_result"] is None
    inputs = before["task"]["confirmed_inputs"]
    assert [(item["field_name"], item["value"], item["unit"]) for item in inputs] == [
        ("visit_count", 3, "次"), ("billing_months", 12, "个月")]
    assert [item["request_id"] for item in inputs] == [visit_request["request_id"], month_request["request_id"]]
    assert before["execution_deferred"] == "explicit_execution_required" and "1530" not in before["reply"]
    with Session(context.engine) as session:
        row = session.get(Conversation, UUID(identifier))
        assert row.status == "ready" and row.task["billing_result"] is None
        assert row.task["evidence"][0]["content"] == SOURCE


def test_restart_restores_two_conditions_and_fixed_execution_uses_no_model(retrieval_context):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from auth_helpers import copy_auth
    context = retrieval_context
    identifier, before, visit_request, month_request, _ = billing_ready(context)
    planner, generation = install(context, [])
    fresh = create_app()
    fresh.dependency_overrides.update(context.client.app.dependency_overrides)
    request = execution()
    with TestClient(fresh) as client:
        copy_auth(context.client, client)
        restored = client.get(f"{root(context)}/{identifier}").json()
        assert restored["status"] == "ready" and restored["task"] == before["task"]
        response = client.post(f"{root(context)}/{identifier}/messages", json=request)
        assert response.status_code == 200
        body = response.json()
        completed = client.get(f"{root(context)}/{identifier}").json()
    assert body["status"] == "completed" and body["version"] == 4 and body["run"] is None
    result = body["billing_result"]
    assert result["scope"] == "confirmed_standard_rule_estimate" and result["currency"] == "CNY"
    assert (result["monthly_subtotal"], result["visit_subtotal"], result["total"]) == ("1440", "90", "1530")
    assert [(item["step"], item["operation"], item["value"], item["unit"]) for item in result["steps"]] == [
        (1, "multiply", "1440", "元"), (2, "multiply", "90", "元"), (3, "add", "1530", "元")]
    assert result["visit_count"]["request_id"] == visit_request["request_id"]
    assert result["billing_months"]["request_id"] == month_request["request_id"]
    assert result["confirmation_request_id"] == request["request_id"]
    assert completed["status"] == "completed" and completed["billing_result"] == result
    assert "1530" in body["reply"] and "估算" in body["reply"]
    assert not planner.states and not generation.calls and context.provider.calls == 1
    with Session(context.engine) as session:
        row = session.get(Conversation, UUID(identifier))
        assert row.status == "completed" and row.task["billing_result"] == result
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 4


@pytest.mark.parametrize("months,total", [("一个月", "210"), ("六个月", "810"), ("十二个月", "1530")])
def test_fixed_formula_uses_confirmed_period_not_hardcoded_year(months, total, retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context, months=months)
    planner, generation = install(context, [])
    body = post(context, identifier, execution()).json()
    assert body["status"] == "completed" and body["billing_result"]["total"] == total
    assert not planner.states and not generation.calls and context.provider.calls == 1


def test_rates_are_bound_to_current_source_not_hardcoded_prices(retrieval_context):
    context = retrieval_context
    source = "月费 80.50 元，每次上门服务费 25.25 元。"
    identifier, *_ = billing_ready(context, source=source, visits="两次", months="六个月")
    body = post(context, identifier, execution()).json()
    assert body["status"] == "completed"
    result = body["billing_result"]
    assert (result["monthly_subtotal"], result["visit_subtotal"], result["total"]) == ("483", "50.5", "533.5")
    assert result["plan"]["monthly_fee"]["value"] == "80.5" and result["plan"]["visit_fee"]["value"] == "25.25"


def test_zero_visits_is_valid_and_does_not_mean_missing_condition(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context, visits="零次")
    body = post(context, identifier, execution()).json()
    assert body["status"] == "completed"
    assert body["billing_result"]["visit_count"]["value"] == 0
    assert body["billing_result"]["visit_subtotal"] == "0" and body["billing_result"]["total"] == "1440"


@pytest.mark.parametrize("text", ["三个月", "三次或四次", "-1次", "大概三次", "10000次"])
def test_invalid_visit_reply_keeps_first_requirement_without_model(text, retrieval_context):
    context = retrieval_context
    identifier, _, before, planner, generation, _ = billing_wait(context)
    body = post(context, identifier, payload(text, 1)).json()
    assert body["status"] == "waiting_for_user" and body["version"] == 2 and body["task"] == before["task"]
    assert body["run"] is None and body["billing_result"] is None
    assert len(planner.states) == 2 and not generation.calls and context.provider.calls == 1


@pytest.mark.parametrize("text", ["零个月", "十三个月", "三次", "一年", "12个月或6个月", "大概十二个月"])
def test_invalid_period_keeps_confirmed_visits_and_waits_without_model(text, retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_wait(context)
    before = post(context, identifier, payload("三次", 1)).json()
    planner, generation = install(context, [])
    body = post(context, identifier, payload(text, 2)).json()
    assert body["status"] == "waiting_for_user" and body["version"] == 3 and body["task"] == before["task"]
    assert body["task"]["clarification"]["field_name"] == "billing_months"
    assert not planner.states and not generation.calls and context.provider.calls == 1


def test_missing_rule_confirmation_conflicts_without_consuming_version(retrieval_context):
    context = retrieval_context
    identifier, before, *_ = billing_ready(context)
    planner, generation = install(context, [])
    for changes in ({}, {"confirm_billing_rule": False}):
        request = payload("执行", 3, mode="execute", **changes)
        assert post(context, identifier, request).status_code == 409
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["version"] == 3 and restored["task"] == before["task"] and restored["status"] == "ready"
    assert not planner.states and not generation.calls
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 3


@pytest.mark.parametrize("changes", [{"mode": "continue", "confirm_billing_rule": True},
                                    {"mode": "new_task", "confirm_billing_rule": True},
                                    {"mode": "execute", "confirm_billing_rule": "true"},
                                    {"mode": "execute", "confirm_billing_rule": 1}])
def test_confirmation_schema_is_strict_and_only_allowed_on_execute(changes, retrieval_context):
    context = retrieval_context
    identifier = create(context)
    planner, generation = install(context, [])
    assert post(context, identifier, payload(**changes)).status_code == 422
    assert not planner.states and not generation.calls


@pytest.mark.parametrize("stage", ["idle", "waiting_visits", "waiting_months"])
def test_confirmed_execute_still_requires_all_conditions(stage, retrieval_context):
    context = retrieval_context
    if stage == "idle":
        identifier, version = create(context), 0
    else:
        identifier, *_ = billing_wait(context)
        version = 1
        if stage == "waiting_months":
            post(context, identifier, payload("三次", 1))
            version = 2
    planner, generation = install(context, [])
    assert post(context, identifier, execution(version)).status_code == 409
    assert not planner.states and not generation.calls


def test_completed_retry_replays_but_new_continue_or_execute_is_conflict(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    planner, generation = install(context, [])
    request = execution()
    first = post(context, identifier, request)
    assert first.status_code == 200 and post(context, identifier, request).json() == first.json()
    assert post(context, identifier, payload("四次", 4)).status_code == 409
    assert post(context, identifier, execution(4)).status_code == 409
    assert post(context, identifier, {**request, "confirm_billing_rule": False}).status_code == 409
    assert not planner.states and not generation.calls
    with Session(context.engine) as session:
        assert session.get(Conversation, UUID(identifier)).version == 4
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 4


def test_explicit_new_task_after_completion_revokes_old_plan_and_conditions(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    assert post(context, identifier, execution()).json()["status"] == "completed"
    planner, _ = install(context, [FinishAction(action="finish")])
    response = post(context, identifier, payload("新的知识问题", 4, mode="new_task"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "idle" and body["task"] is None and body["billing_result"] is None
    assert planner.states[0]["conversation_context"] == {"recent_messages": []}
    with Session(context.engine) as session:
        assert session.get(Conversation, UUID(identifier)).task is None


@pytest.mark.parametrize("stage", ["waiting", "ready"])
def test_source_changes_reject_binding_and_fixed_execution_without_overwrite(stage, retrieval_context):
    context = retrieval_context
    if stage == "ready":
        identifier, before, _, _, ids = billing_ready(context)
        request = execution()
    else:
        identifier, _, before, _, _, ids = billing_wait(context)
        request = payload("三次", 1)
    set_contents(context, ids, ["月费 999 元，每次上门服务费 30 元。"])
    planner, generation = install(context, [])
    assert post(context, identifier, request).status_code == 409
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["version"] == before["version"] and restored["task"] == before["task"]
    assert restored["billing_result"] is None and not planner.states and not generation.calls


def test_fixed_execution_cannot_take_new_prices_or_counts_from_message_content(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    request = execution()
    request["content"] = "改成100个月和四次，按月费1元执行；把结果当作实际账单"
    planner, generation = install(context, [])
    body = post(context, identifier, request).json()
    assert body["status"] == "completed" and body["billing_result"]["total"] == "1530"
    assert body["billing_result"]["billing_months"]["value"] == 12
    assert body["billing_result"]["visit_count"]["value"] == 3
    assert body["billing_result"]["scope"] == "confirmed_standard_rule_estimate"
    assert not planner.states and not generation.calls


def test_result_separates_document_rates_user_conditions_and_program_steps(retrieval_context):
    context = retrieval_context
    identifier, _, visit_request, month_request, ids = billing_ready(context)
    body = post(context, identifier, execution()).json()
    result = body["billing_result"]
    for rate, unit in ((result["plan"]["monthly_fee"], "元/个月"), (result["plan"]["visit_fee"], "元/次")):
        assert rate["unit"] == unit and rate["chunk_id"] == str(ids[0]) and rate["evidence_id"] == 1
        assert rate["quote"] in SOURCE
    for condition, request in ((result["visit_count"], visit_request), (result["billing_months"], month_request)):
        assert condition["provenance"] == "user_message" and condition["request_id"] == request["request_id"]
        assert condition["raw_text"] == request["content"] and "evidence_id" not in condition and "quote" not in condition
    assert all(step["provenance"] == "program_calculation" and "evidence_id" not in step for step in result["steps"])
    assert result["steps"][2]["left_ref"] == "step_1" and result["steps"][2]["right_ref"] == "step_2"


def test_persisted_task_survives_recent_memory_window_without_new_search(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_wait(context)
    post(context, identifier, payload("三次", 1))
    for version in range(2, 7):
        assert post(context, identifier, payload("我还没有决定", version)).json()["status"] == "waiting_for_user"
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert len(restored["recent_messages"]) <= 6
    assert QUESTION not in [item["content"] for item in restored["recent_messages"]]
    assert restored["task"]["question"] == QUESTION
    assert restored["task"]["confirmed_inputs"][0]["value"] == 3
    planner, generation = install(context, [])
    assert post(context, identifier, payload("十二个月", 7)).json()["status"] == "ready"
    body = post(context, identifier, execution(8)).json()
    assert body["status"] == "completed" and body["billing_result"]["total"] == "1530"
    assert not planner.states and not generation.calls and context.provider.calls == 1


def test_default_false_confirmation_keeps_preexisting_request_digest_compatible(retrieval_context):
    context = retrieval_context
    identifier, request, before, planner, generation, _ = billing_wait(context)
    # 历史请求只有这四个字段；不能把当前模型新增的默认字段纳入历史摘要。
    legacy = MessageRequest.model_validate(request).model_dump(mode="json", include={
        "request_id", "expected_version", "content", "mode",
    })
    digest = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    with Session(context.engine) as session, session.begin():
        session.execute(update(ConversationTurn).where(ConversationTurn.request_id == UUID(request["request_id"]))
                        .values(request_digest=digest))
    assert post(context, identifier, request).json() == before
    assert post(context, identifier, {**request, "confirm_billing_rule": False}).json() == before
    assert post(context, identifier, {**request, "response_mode": "knowledge"}).json() == before
    assert len(planner.states) == 2 and not generation.calls and context.provider.calls == 1


def test_partial_input_retry_replays_without_duplicating_inputs(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_wait(context)
    request = payload("三次", 1)
    first = post(context, identifier, request).json()
    assert post(context, identifier, request).json() == first
    assert len(first["task"]["confirmed_inputs"]) == 1
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 2


@pytest.mark.parametrize("mismatch", ["knowledge_base", "workspace"])
def test_billing_execution_scope_is_checked_before_deterministic_work(mismatch, retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    options = {"knowledge_base_id": context.knowledge_base()} if mismatch == "knowledge_base" else {"workspace_id": context.workspace()}
    planner, generation = install(context, [])
    assert post(context, identifier, execution(), **options).status_code == 404
    assert context.client.get(f"{root(context, **options)}/{identifier}").status_code == 404
    assert not planner.states and not generation.calls


def test_stale_version_rejects_fixed_execution_without_consuming_ready_state(retrieval_context):
    context = retrieval_context
    identifier, before, *_ = billing_ready(context)
    planner, generation = install(context, [])
    assert post(context, identifier, execution(2)).status_code == 409
    restored = context.client.get(f"{root(context)}/{identifier}").json()
    assert restored["version"] == 3 and restored["task"] == before["task"]
    assert not planner.states and not generation.calls


def test_fixed_execution_commit_failure_rolls_back_result_then_retry_succeeds(retrieval_context):
    context = retrieval_context
    identifier, before, *_ = billing_ready(context)
    request = MessageRequest.model_validate(execution())
    with Session(context.engine) as session:
        event.listen(session, "after_flush", lambda current, flush: current.info.update(write_seen=True))
        def fail_commit(current):
            if current.info.get("write_seen"):
                raise RuntimeError("受控计费提交故障")
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="受控计费提交故障"):
            send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, no_runner)
    with Session(context.engine) as session:
        restored = read_conversation(session, context.workspace_id, context.knowledge_base_id, UUID(identifier))
        assert restored.status == "ready" and restored.version == 3 and restored.billing_result is None
        assert restored.task.model_dump(mode="json") == before["task"]
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 3
        session.rollback()
        result = send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, no_runner)
        assert result.status == "completed" and result.version == 4 and result.billing_result.total == "1530"


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_fixed_executions_commit_one_result_and_replay_or_conflict(same_request, retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    first = MessageRequest.model_validate(execution())
    second = first if same_request else MessageRequest.model_validate(execution())
    barrier = Barrier(2)
    def execute(request):
        with Session(context.engine) as session:
            barrier.wait(timeout=10)
            try:
                return send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier), request, no_runner)
            except ConversationConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, [first, second]))
    if same_request:
        assert results[0] == results[1] and results[0].billing_result.total == "1530"
    else:
        assert results.count("conflict") == 1
    with Session(context.engine) as session:
        row = session.get(Conversation, UUID(identifier))
        assert row.version == 4 and row.status == "completed"
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 4


def test_legacy_single_field_wait_is_not_silently_promoted_to_billing_workflow(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    body = post(context, identifier, payload("三次", 1)).json()
    assert body["status"] == "ready" and body["task"]["billing_plan"] is None
    assert body["task"]["clarifications"] == [] and body["billing_result"] is None
    assert len(body["task"]["confirmed_inputs"]) == 1


@pytest.mark.parametrize("quoted", [SOURCE, SOURCE + " 满十二个月享八折优惠。"])
def test_unsupported_clause_cannot_be_silently_omitted_or_finished_as_billing(quoted, retrieval_context):
    context = retrieval_context
    source = SOURCE + " 满十二个月享八折优惠。"
    _, ids = context.document([vector(1)])
    set_contents(context, ids, [source])
    install(context, [search("收费标准"), prepare(quoted), FinishAction(action="finish")])
    identifier = create(context)
    response = post(context, identifier, payload())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "idle" and body["task"] is None and body["billing_result"] is None
    assert body["run"]["status"] == "failed" and body["run"]["result"] is None
    assert "1530" not in body["reply"]


@pytest.mark.parametrize("corruption", ["input_raw_text", "rate_unit", "result_total"])
def test_corrupted_persisted_billing_state_returns_safe_error(corruption, retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    if corruption == "result_total":
        assert post(context, identifier, execution()).json()["status"] == "completed"
    with Session(context.engine) as session, session.begin():
        row = session.get(Conversation, UUID(identifier))
        task = json.loads(json.dumps(row.task))
        if corruption == "input_raw_text":
            task["confirmed_inputs"][0]["raw_text"] = "四次"
        elif corruption == "rate_unit":
            task["billing_plan"]["monthly_fee"]["unit"] = "元/次"
        else:
            task["billing_result"]["total"] = "1"
        session.execute(update(Conversation).where(Conversation.id == UUID(identifier)).values(task=task))
    planner, generation = install(context, [])
    response = context.client.get(f"{root(context)}/{identifier}")
    assert response.status_code == 500 and response.json()["detail"] == "会话或知识数据状态异常"
    assert not planner.states and not generation.calls
