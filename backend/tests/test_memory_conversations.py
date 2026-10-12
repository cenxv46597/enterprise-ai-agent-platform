"""独立数据库中的跨会话偏好闭环；所有规划与生成均为替身。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction
from app.api import agent_runs as agent_api, answers as answer_api
from app.conversations.contracts import ConversationStateError, MessageRequest
from app.conversations.service import send_message
from app.db.models import AnswerPreference, ConversationTurn
from app.memory.contracts import PreferenceRead, PreferenceUpdate
from app.memory.service import set_preference
from test_agent_engine import ControlledGeneration, ControlledPlanner, search
from test_billing_conversation import billing_ready, execution
from test_conversation_api import create, payload, post, root, waiting
from test_hybrid_retrieval import set_contents
from test_retrieval import retrieval_context, vector


SOURCE = "通过健康检查后，恢复文件导入。"


class StyledGeneration(ControlledGeneration):
    def __init__(self, callback=None):
        super().__init__()
        self.preferences = []
        self.callback = callback
    def generate_answer_with_preferences(self, question, sources, detail):
        self.preferences.append(detail)
        if self.callback:
            self.callback()
        return super().generate_answer(question, sources)


def pref(context, detail, version=0):
    with Session(context.engine) as session:
        return set_preference(session, context.workspace_id, context.knowledge_base_id,
                              PreferenceUpdate(expected_version=version, answer_detail=detail))


def install(context, generation=None, decisions=None):
    planner = ControlledPlanner(decisions or [search("恢复导入"), FinishAction(action="finish")])
    generation = generation or StyledGeneration()
    context.client.app.dependency_overrides[agent_api.get_planner_provider] = lambda: planner
    context.client.app.dependency_overrides[answer_api.get_generation_provider] = lambda: generation
    return planner, generation


def document(context):
    _, ids = context.document([vector(1)])
    set_contents(context, ids, [SOURCE])


def test_new_conversations_share_explicit_style_but_not_chat_or_task(retrieval_context):
    context = retrieval_context
    document(context)
    saved = pref(context, "detailed")
    first = create(context)
    for identifier in (first, create(context)):
        planner, generation = install(context)
        body = post(context, identifier, payload("如何恢复导入？")).json()
        assert body["answer_preference"] == saved.model_dump(mode="json")
        assert body["task"] is None and generation.preferences == ["detailed"]
        assert planner.states[0]["conversation_context"] == {"recent_messages": []}
        assert "answer_preference" not in body["run"]


def test_replay_keeps_old_snapshot_and_next_request_uses_new_setting(retrieval_context):
    context = retrieval_context
    document(context)
    pref(context, "concise")
    identifier, request = create(context), payload("如何恢复导入？")
    _, generation = install(context)
    first = post(context, identifier, request).json()
    pref(context, "detailed", 1)
    assert post(context, identifier, request).json() == first
    assert generation.preferences == ["concise"]
    _, generation = install(context)
    second = post(context, identifier, payload("如何恢复导入？", 1)).json()
    assert second["answer_preference"]["version"] == 2 and generation.preferences == ["detailed"]


def test_mid_generation_update_is_next_round_only(retrieval_context):
    context = retrieval_context
    document(context)
    pref(context, "concise")
    def update_setting():
        pref(context, "detailed", 1)
    _, generation = install(context, StyledGeneration(update_setting))
    identifier = create(context)
    first = post(context, identifier, payload("如何恢复导入？")).json()
    assert first["answer_preference"]["answer_detail"] == "concise" and generation.preferences == ["concise"]
    _, generation = install(context)
    second = post(context, identifier, payload("如何恢复导入？", 1)).json()
    assert second["answer_preference"]["answer_detail"] == "detailed" and generation.preferences == ["detailed"]


def test_clear_preserves_old_replay_but_new_requests_have_no_style(retrieval_context):
    context = retrieval_context
    document(context)
    pref(context, "concise")
    identifier, request = create(context), payload("如何恢复导入？")
    install(context)
    first = post(context, identifier, request).json()
    pref(context, None, 1)
    assert post(context, identifier, request).json() == first
    _, generation = install(context)
    second = post(context, identifier, payload("如何恢复导入？", 1, mode="new_task")).json()
    assert second["answer_preference"] is None and not generation.preferences and len(generation.calls) == 1


@pytest.mark.parametrize("text", ["这次请详细解释", "以后请一直详细解释"])
def test_chat_alone_never_writes_long_term_preferences(retrieval_context, text):
    context = retrieval_context
    install(context, decisions=[FinishAction(action="finish")])
    assert post(context, create(context), payload(text)).status_code == 200
    with Session(context.engine) as session:
        assert session.scalar(select(AnswerPreference.knowledge_base_id)) is None


def test_no_evidence_does_not_claim_style_application(retrieval_context):
    context = retrieval_context
    pref(context, "concise")
    _, generation = install(context, decisions=[FinishAction(action="finish")])
    body = post(context, create(context), payload("如何恢复导入？")).json()
    assert body["answer_preference"] is None and body["run"]["result"]["abstained"]
    assert not generation.calls and not generation.preferences


def test_preferences_do_not_transfer_to_another_knowledge_base(retrieval_context):
    context = retrieval_context
    pref(context, "detailed")
    other = context.knowledge_base()
    identifier = context.client.post(root(context, knowledge_base_id=other)).json()["conversation_id"]
    planner, _ = install(context, decisions=[FinishAction(action="finish")])
    body = post(context, identifier, payload("问题"), knowledge_base_id=other).json()
    assert body["answer_preference"] is None and planner.states[0]["conversation_context"] == {"recent_messages": []}


def test_explicit_new_task_reuses_style_without_inheriting_confirmed_count(retrieval_context):
    context = retrieval_context
    identifier, *_ = waiting(context)
    assert post(context, identifier, payload("三次", 1)).json()["status"] == "ready"
    pref(context, "concise")
    planner, generation = install(context)
    body = post(context, identifier, payload("收费规则是什么？", 2, mode="new_task")).json()
    assert body["task"] is None and generation.preferences == ["concise"]
    assert not planner.states[0].get("confirmed_inputs") and planner.states[0]["conversation_context"] == {"recent_messages": []}


def test_confirmed_count_and_deterministic_billing_do_not_apply_style(retrieval_context):
    context = retrieval_context
    identifier, *_ = billing_ready(context)
    pref(context, "detailed")
    planner, generation = install(context)
    body = post(context, identifier, execution()).json()
    assert body["status"] == "completed" and body["billing_result"]["total"] == "1530"
    assert body["answer_preference"] is None and not planner.states and not generation.calls


def test_saved_legacy_response_without_preference_field_still_replays(retrieval_context):
    context = retrieval_context
    identifier, request = create(context), payload("问题")
    install(context, decisions=[FinishAction(action="finish")])
    first = post(context, identifier, request).json()
    with Session(context.engine) as session, session.begin():
        turn = session.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == UUID(identifier)))
        saved = dict(turn.response)
        saved.pop("answer_preference")
        turn.response = saved
    pref(context, "detailed")
    assert post(context, identifier, request).json() == first


def test_runner_cannot_forge_a_cross_scope_snapshot(retrieval_context):
    context = retrieval_context
    pref(context, "concise")
    identifier = UUID(create(context))
    def forged(question, state):
        from app.agents.contracts import AgentRun
        return AgentRun(uuid4(), "stopped", "controlled", [], None, [], answer_preference=PreferenceRead(
            knowledge_base_id=uuid4(), version=1, answer_detail="concise"))
    with Session(context.engine) as session, pytest.raises(ConversationStateError, match="快照不一致"):
        send_message(session, context.workspace_id, context.knowledge_base_id, identifier,
                     MessageRequest(request_id=uuid4(), expected_version=0, content="问题"), forged)
