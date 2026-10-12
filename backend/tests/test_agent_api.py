"""真实 PostgreSQL 验证智能体入口、范围、事务与工具失败恢复；模型均为替身。"""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction, PlannerTimeoutError
from app.agents.engine import run_agent, validate_scope
from app.agents.tools import KnowledgeSearchTool, ToolRegistry
from app.api import agent_runs as api
from app.api import answers as answers_api
from app.providers.embeddings import EmbeddingTimeoutError
from test_agent_engine import ControlledGeneration, ControlledPlanner, search
from test_hybrid_retrieval import set_contents
from test_retrieval import QueryProvider, retrieval_context, vector


def url(context, **options):
    return context.url(**options).removesuffix("/search") + "/agent-runs"


def install(context, decisions):
    planner, generation = ControlledPlanner(decisions), ControlledGeneration()
    context.client.app.dependency_overrides[api.get_planner_provider] = lambda: planner
    context.client.app.dependency_overrides[answers_api.get_generation_provider] = lambda: generation
    return planner, generation


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_actual_search_then_finish_exposes_trace_and_original_citation(retrieval_context, strategy):
    context = retrieval_context
    document_id, ids = context.document([vector(1)])
    original = "退款审核通过后，三个工作日到账。"
    set_contents(context, ids, [original])
    planner, generation = install(context, [search("退款到账"), FinishAction(action="finish")])
    response = context.client.post(url(context), json={"question": "退款多久到账？", "strategy": strategy})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["stop_reason"] is None
    assert [item["action"] for item in body["steps"]] == ["tool", "finish"]
    assert body["steps"][0]["query"] == "退款到账"
    citation = body["result"]["citations"][0]
    assert citation["chunk_id"] == str(ids[0]) and citation["document_id"] == str(document_id)
    assert citation["quote"] == original and citation["start_char"] == 0
    assert body["evidence"][0]["evidence_id"] == 1 and body["evidence"][0]["chunk_id"] == str(ids[0])
    assert "content" not in body["evidence"][0]
    assert context.provider.calls == len(generation.calls) == 1 and len(planner.states) == 2


def test_models_run_outside_transactions_and_scope_is_bound(retrieval_context):
    context = retrieval_context
    _, ids = context.document([vector(1)])
    set_contents(context, ids, ["当前知识库资料。"])
    other = context.knowledge_base(workspace_id=context.workspace())
    context.document([vector(1)], knowledge_base_id=other)
    with Session(context.engine) as session:
        validate_scope(session, context.workspace_id, context.knowledge_base_id)
        registry = ToolRegistry()
        def inspect_embedding(texts):
            assert not session.in_transaction()
        registry.register(KnowledgeSearchTool(session, QueryProvider(on_call=inspect_embedding), context.workspace_id,
                                              context.knowledge_base_id))
        class InspectingPlanner(ControlledPlanner):
            def decide(self, state):
                assert not session.in_transaction()
                if state["evidence"]:
                    assert [item["content"] for item in state["evidence"]] == ["当前知识库资料。"]
                return super().decide(state)
        class InspectingGeneration(ControlledGeneration):
            def generate_answer(self, question, sources):
                assert not session.in_transaction()
                return super().generate_answer(question, sources)
        result = run_agent(InspectingPlanner([search(), FinishAction(action="finish")]), InspectingGeneration(), registry, "问题")
    assert result.status == "completed" and result.result.citations[0].chunk_id == ids[0]


@pytest.mark.parametrize("mismatch", ["knowledge_base", "workspace"])
def test_unknown_or_wrong_scope_rejected_before_planning(retrieval_context, mismatch):
    context = retrieval_context
    planner, generation = install(context, [])
    options = {"knowledge_base_id": uuid4()} if mismatch == "knowledge_base" else {"workspace_id": context.workspace()}
    response = context.client.post(url(context, **options), json={"question": "问题"})
    assert response.status_code == 404 and not planner.states and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("body", [
    {"question": " "}, {"question": "问题", "max_steps": True}, {"question": "问题", "max_steps": 9},
    {"question": "问题", "max_steps": 0}, {"question": "问题", "rerank": "true"},
    {"question": "问题", "tool_name": "delete_files"},
])
def test_invalid_request_rejected_before_models(retrieval_context, body):
    context = retrieval_context
    planner, generation = install(context, [])
    response = context.client.post(url(context), json=body)
    assert response.status_code == 422 and not planner.states and not generation.calls


def test_repeated_search_is_visible_stop_without_second_embedding(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    planner, generation = install(context, [search(), search()])
    body = context.client.post(url(context), json={"question": "问题"}).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "duplicate_action" and body["result"] is None
    assert context.provider.calls == 1 and len(planner.states) == 2 and not generation.calls


def test_planner_timeout_is_failed_run_not_no_evidence_abstention(retrieval_context):
    context = retrieval_context
    planner, generation = install(context, [PlannerTimeoutError("private")])
    response = context.client.post(url(context), json={"question": "问题"})
    assert response.status_code == 200  # 已返回运行记录，业务状态须另行检查。
    body = response.json()
    assert body["status"] == "failed" and body["stop_reason"] == "planner_timeout" and body["result"] is None
    assert not generation.calls and "private" not in response.text


def test_tool_timeout_observation_allows_different_search(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    def fail_once(texts):
        if context.provider.calls == 1:
            raise EmbeddingTimeoutError("private")
    context.provider.on_call = fail_once
    planner, generation = install(context, [search("第一问"), search("换个问题"), FinishAction(action="finish")])
    body = context.client.post(url(context), json={"question": "问题"}).json()
    assert body["status"] == "completed" and context.provider.calls == 2 and len(generation.calls) == 1
    assert body["steps"][0]["error_code"] == "tool_timeout"
    assert planner.states[1]["observations"][0]["status"] == "error"


def test_empty_search_then_finish_is_normal_abstention(retrieval_context):
    context = retrieval_context
    _, generation = install(context, [search(), FinishAction(action="finish")])
    body = context.client.post(url(context), json={"question": "问题"}).json()
    assert body["status"] == "completed" and body["result"]["abstained"] and not generation.calls
    assert context.provider.calls == 0


def test_missing_planner_config_is_503_and_does_not_search(retrieval_context, monkeypatch):
    from app.core.config import Settings
    context = retrieval_context
    monkeypatch.setattr(api, "Settings", lambda: Settings(_env_file=None, generation_base_url=None,
                                                         generation_api_key=None, generation_model=None))
    response = context.client.post(url(context), json={"question": "问题"})
    assert response.status_code == 503 and context.provider.calls == 0


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_native_protocol_through_actual_search_api_has_paired_history_and_citation(retrieval_context, strategy):
    import json
    import httpx
    from pydantic import SecretStr
    from app.providers.native_planner import NativePlannerProvider
    from test_native_planner import call_payload, finish_payload
    context = retrieval_context
    document_id, ids = context.document([vector(1)])
    original = "退款须先审核，通过后到账。"
    set_contents(context, ids, [original])
    _, generation = install(context, [])
    bodies = []
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload())
        assert body["messages"][2]["tool_calls"][0]["id"] == body["messages"][3]["tool_call_id"] == "call_1"
        assert json.loads(body["messages"][3]["content"])["evidence"][0]["content"] == original
        return httpx.Response(200, json=finish_payload("这句话不得成为实际回答"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        planner = NativePlannerProvider("https://example.test", SecretStr("fake"), "test-model", client=transport)
        context.client.app.dependency_overrides[api.get_planner_provider] = lambda: planner
        response = context.client.post(url(context), json={"question": "退款流程？", "strategy": strategy})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["result"]["answer"] == original
    assert body["result"]["citations"][0]["chunk_id"] == str(ids[0])
    assert body["result"]["citations"][0]["document_id"] == str(document_id)
    assert body["steps"][0]["tool_call_id"] == "call_1" and "native_call" not in response.text
    assert len(bodies) == 2 and context.provider.calls == len(generation.calls) == 1


def test_native_parallel_response_is_failed_record_before_database_search(retrieval_context):
    import httpx
    from pydantic import SecretStr
    from app.providers.native_planner import NativePlannerProvider
    from test_native_planner import call_payload
    context = retrieval_context
    _, generation = install(context, [])
    payload = call_payload()
    payload["choices"][0]["message"]["tool_calls"].append(call_payload(identifier="call_2")["choices"][0]["message"]["tool_calls"][0])
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as transport:
        planner = NativePlannerProvider("https://example.test", SecretStr("fake"), "test-model", client=transport)
        context.client.app.dependency_overrides[api.get_planner_provider] = lambda: planner
        response = context.client.post(url(context), json={"question": "问题"})
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert response.json()["stop_reason"] == "planner_error" and not generation.calls and context.provider.calls == 0
