"""独立测试库中的候选快照、用户选择和状态查询；模型均为替身。"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents import document_status as status_module
from app.agents.contracts import AgentRun, FinishAction
from app.conversations.contracts import ConversationConflictError, MessageRequest
from app.conversations.document_selection import DocumentSelectionTask
from app.conversations.service import send_message
from app.db.models import Conversation, ConversationTurn, Document, WorkspaceMembership
from app.main import create_app
from auth_helpers import copy_auth
from test_conversation_api import create, install, payload, post, root
from test_document_candidates_observation import find_documents
from test_document_candidates_tool import candidate_result
from test_retrieval import retrieval_context


def waiting_selection(context, *, count=2):
    ids = [context.document([], status="parsed", name="请假规定.txt")[0] for _ in range(count)]
    with Session(context.engine) as session, session.begin():
        for index, identifier in enumerate(ids):
            session.execute(update(Document).where(Document.id == identifier).values(
                created_at=datetime(2026, 10, index + 1, tzinfo=timezone.utc), byte_size=100 + index,
            ))
    planner, generation = install(context, [find_documents("请假规定")])
    identifier = create(context)
    request = payload("请假规定处理好了吗？", response_mode="document_status")
    response = post(context, identifier, request)
    assert response.status_code == 200
    first = response.json()
    assert first["status"] == "waiting_for_user" and first["task"]["kind"] == "document_selection"
    assert first["run"]["status"] == "waiting_for_user"
    assert [item["document_id"] for item in first["task"]["candidates"]["candidates"]] == [str(item) for item in ids[:5]]
    assert first["task"]["candidates"]["has_more"] is (count > 5)
    assert "上传时间" in first["reply"] and "大小" in first["reply"]
    assert "pending_document_selection" not in first["run"] and first["run"]["evidence"] == []
    assert len(planner.states) == 1 and generation.calls == [] and context.provider.calls == 0
    return identifier, request, first, planner, generation, ids


def test_fresh_app_selects_saved_second_document_and_reads_its_latest_state(retrieval_context):
    context = retrieval_context
    identifier, _, first, planner, generation, ids = waiting_selection(context)
    # 新增更早的匹配会改变重新搜索的编号；选择仍须绑定原来的快照。
    inserted = context.document([], name="请假规定.txt")[0]
    with Session(context.engine) as session, session.begin():
        session.execute(update(Document).where(Document.id == inserted).values(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        session.execute(update(Document).where(Document.id == ids[1]).values(status="ready", error_code=None))
    fresh_app = create_app()
    fresh_app.dependency_overrides.update(context.client.app.dependency_overrides)
    with TestClient(fresh_app) as client:
        copy_auth(context.client, client)
        saved = client.get(f"{root(context)}/{identifier}").json()
        assert saved["task"] == first["task"]
        response = client.post(f"{root(context)}/{identifier}/messages", json=payload("2", 1))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "idle" and body["task"] is None and body["version"] == 2
    assert body["run"]["status"] == "completed" and "可检索" in body["reply"]
    assert body["run"]["steps"][0]["document_status"]["document_id"] == str(ids[1])
    assert body["run"]["steps"][0]["status"] == "success" and body["run"]["result"]["citations"] == []
    assert len(planner.states) == 1 and generation.calls == [] and context.provider.calls == 0
    with Session(context.engine) as session:
        row = session.get(Conversation, UUID(identifier))
        assert row.task is None and row.version == 2
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 2


@pytest.mark.parametrize("text", ["0", "3", "二", "2或1", "第二份", "请帮我决定"])
def test_invalid_choice_preserves_list_without_calling_models(retrieval_context, text):
    context = retrieval_context
    identifier, _, first, planner, generation, _ = waiting_selection(context)
    response = post(context, identifier, payload(text, 1))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "waiting_for_user" and body["task"] == first["task"]
    assert body["version"] == 2 and body["run"] is None and len(planner.states) == 1
    assert generation.calls == [] and context.provider.calls == 0


def test_has_more_is_visible_and_selection_is_limited_to_saved_page(retrieval_context):
    context = retrieval_context
    identifier, _, first, planner, _, ids = waiting_selection(context, count=6)
    assert "未展示全部" in first["reply"]
    assert post(context, identifier, payload("6", 1)).json()["status"] == "waiting_for_user"
    body = post(context, identifier, payload("5", 2)).json()
    assert body["run"]["steps"][0]["document_status"]["document_id"] == str(ids[4])
    assert len(planner.states) == 1


def test_replays_do_not_query_again_and_stale_or_changed_requests_conflict(retrieval_context, monkeypatch):
    context = retrieval_context
    identifier, request, first, planner, _, ids = waiting_selection(context)
    assert post(context, identifier, request).json() == first
    assert post(context, identifier, {**request, "response_mode": "knowledge"}).status_code == 409
    query, calls = status_module.get_document_status, []

    def recorded(*args):
        calls.append(args[-1])
        return query(*args)

    monkeypatch.setattr(status_module, "get_document_status", recorded)
    choice = payload("2", 1)
    saved = post(context, identifier, choice).json()
    with Session(context.engine) as session, session.begin():
        session.execute(update(Document).where(Document.id == ids[1]).values(status="ready"))
    assert post(context, identifier, choice).json() == saved
    assert calls == [ids[1]] and len(planner.states) == 1
    assert post(context, identifier, {**choice, "content": "1"}).status_code == 409
    assert post(context, identifier, payload("1", 1)).status_code == 409


def test_selected_document_moved_to_other_kb_cannot_return_a_status(retrieval_context):
    context = retrieval_context
    identifier, _, first, planner, _, ids = waiting_selection(context)
    other_kb = context.knowledge_base()
    with Session(context.engine) as session, session.begin():
        session.execute(update(Document).where(Document.id == ids[1]).values(knowledge_base_id=other_kb))
    response = post(context, identifier, payload("2", 1))
    assert response.status_code == 409 and "候选文档已变化" in response.json()["detail"]
    saved = context.client.get(f"{root(context)}/{identifier}").json()
    assert saved["version"] == 1 and saved["task"] == first["task"] and len(planner.states) == 1


def test_query_failure_keeps_pending_choice_and_can_be_retried_with_a_new_request(retrieval_context, monkeypatch):
    context = retrieval_context
    identifier, _, first, planner, _, _ = waiting_selection(context)
    original = status_module.get_document_status

    def failure(*args):
        raise SQLAlchemyError("private diagnostic")

    monkeypatch.setattr(status_module, "get_document_status", failure)
    response = post(context, identifier, payload("2", 1))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "waiting_for_user" and body["task"] == first["task"]
    assert body["run"]["status"] == "failed" and body["run"]["steps"][0]["error_code"] == "tool_service_error"
    assert "private" not in body["reply"] and body["version"] == 2
    monkeypatch.setattr(status_module, "get_document_status", original)
    assert post(context, identifier, payload("2", 2)).json()["run"]["status"] == "completed"
    assert len(planner.states) == 1


def test_new_task_replaces_selection_and_uses_the_requested_mode(retrieval_context):
    context = retrieval_context
    identifier, _, _, _, _, _ = waiting_selection(context)
    planner, _ = install(context, [FinishAction(action="finish")])
    response = post(context, identifier, payload("年假有哪些规则？", 1, mode="new_task"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "idle" and body["task"] is None
    assert planner.states[0]["conversation_context"] == {"recent_messages": []}
    assert "response_mode" not in planner.states[0]


def test_single_match_and_no_match_complete_without_selection(retrieval_context):
    context = retrieval_context
    doc_id, _ = context.document([], status="processing", name="唯一.txt")
    from test_document_status_agent import status_action
    planner, generation = install(context, [find_documents("唯一"), status_action(doc_id), FinishAction(action="finish")])
    identifier = create(context)
    body = post(context, identifier, payload("唯一文件状态", response_mode="document_status")).json()
    assert body["task"] is None and "解析中" in body["reply"] and len(planner.states) == 3
    planner, _ = install(context, [find_documents("不存在")])
    empty = post(context, identifier, payload("不存在文件状态", 1, response_mode="document_status")).json()
    assert empty["run"]["status"] == "completed" and empty["task"] is None and "没有匹配" in empty["reply"]
    assert generation.calls == [] and context.provider.calls == 0


def test_query_mode_cannot_use_knowledge_tools_or_client_supplied_target(retrieval_context):
    context = retrieval_context
    from test_agent_engine import search
    planner, _ = install(context, [search(), FinishAction(action="finish")])
    identifier = create(context)
    body = post(context, identifier, payload("查询状态", response_mode="document_status")).json()
    assert body["run"]["status"] == "failed" and body["run"]["steps"][0]["error_code"] == "unknown_tool"
    assert context.provider.calls == 0
    response = post(context, identifier, payload("1", 1, selected_document_id=str(uuid4())))
    assert response.status_code == 422


def test_membership_is_checked_again_before_resuming_selection(retrieval_context):
    context = retrieval_context
    identifier, _, _, planner, _, _ = waiting_selection(context)
    with Session(context.engine) as session, session.begin():
        membership = session.scalar(select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == context.workspace_id,
            WorkspaceMembership.user_id == context.client.app.state.test_user_id,
        ))
        session.delete(membership)
    response = post(context, identifier, payload("2", 1))
    assert response.status_code == 404 and len(planner.states) == 1
    with Session(context.engine) as session:
        assert session.get(Conversation, UUID(identifier)).version == 1


def test_fabricated_foreign_candidate_cannot_be_persisted(retrieval_context):
    context = retrieval_context
    other = context.knowledge_base()
    foreign, _ = context.document([], knowledge_base_id=other)
    candidates = candidate_result(2)
    candidates = candidates.model_copy(update={"candidates": (
        candidates.candidates[0].model_copy(update={"document_id": foreign}), candidates.candidates[1],
    )})
    task = DocumentSelectionTask(question="状态", filename_query="文件", candidates=candidates)
    result = AgentRun(uuid4(), "waiting_for_user", None, [], None, [], pending_document_selection=task)
    identifier = create(context)
    with Session(context.engine) as session:
        with pytest.raises(ConversationConflictError):
            send_message(session, context.workspace_id, context.knowledge_base_id, UUID(identifier),
                         MessageRequest(**payload("状态", response_mode="document_status")), lambda *args: result)
    with Session(context.engine) as session:
        assert session.get(Conversation, UUID(identifier)).version == 0
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0


@pytest.mark.parametrize("count", [0, 1, 6])
def test_pending_selection_rejects_empty_unique_or_unbounded_lists(count):
    with pytest.raises(ValidationError):
        DocumentSelectionTask(question="状态", filename_query="文件", candidates=candidate_result(count))
