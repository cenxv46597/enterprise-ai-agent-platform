"""独立测试库验证运行统计在HTTP、会话保存及重放中的传递；模型替身。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction
from app.db.models import ConversationTurn
from test_agent_api import install, url
from test_agent_engine import search
from test_conversation_api import payload, post
from test_document_selection_api import waiting_selection
from test_retrieval import retrieval_context, vector


def test_single_run_response_exposes_metrics(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    planner, _ = install(context, [search(), FinishAction(action="finish")])
    response = context.client.post(url(context), json={"question": "如何恢复服务？"})
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert set(metrics) == {"total_ms", "decision_count", "tool_execution_count"}
    assert metrics["total_ms"] >= 0 and metrics["decision_count"] == len(planner.states) == 2
    assert metrics["tool_execution_count"] == 1


def test_conversation_persists_and_replays_program_decision_metrics(retrieval_context):
    context = retrieval_context
    identifier, _, first, planner, generation, _ = waiting_selection(context)
    assert first["run"]["metrics"]["decision_count"] == first["run"]["metrics"]["tool_execution_count"] == 1
    choice = payload("2", 1)
    response = post(context, identifier, choice)
    assert response.status_code == 200
    body = response.json()
    metrics = body["run"]["metrics"]
    # 选定文档使用程序决策两次，并非又发出两次模型请求。
    assert metrics["decision_count"] == 2 and metrics["tool_execution_count"] == 1
    assert len(planner.states) == 1 and generation.calls == []
    with Session(context.engine) as session:
        turn = session.scalar(select(ConversationTurn).where(ConversationTurn.request_id == UUID(choice["request_id"])))
        assert turn.response["run"]["metrics"] == metrics
    assert post(context, identifier, choice).json() == body
    assert len(planner.states) == 1
