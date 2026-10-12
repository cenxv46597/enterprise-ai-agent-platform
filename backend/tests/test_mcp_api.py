"""独立 PostgreSQL 回验 MCP 原文与 API 边界；协议服务和模型均为替身。"""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.agents.contracts import FinishAction, ToolAction, ToolExecutionError
from app.agents.engine import run_agent, validate_scope
from app.agents.tools import SearchArguments, ToolRegistry
from app.api import agent_runs as api
from app.db.models import Chunk, Document, KnowledgeBase
from app.knowledge.retrieval import SearchHit
from app.mcp_tools.contracts import McpSearchError, SearchEnvelope, SearchHitPayload
from app.mcp_tools.tool import McpKnowledgeSearchTool
from test_agent_api import install, url
from test_agent_engine import ControlledGeneration, ControlledPlanner, search
from test_hybrid_retrieval import set_contents
from test_retrieval import DEFAULT_PROFILE, retrieval_context, vector


class ControlledMcpClient:
    """记录查询；不启动进程、连接模型或进行真正的协议交换。"""

    def __init__(self, outcome, on_call=None):
        self.outcome, self.on_call = outcome, on_call
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        if self.on_call:
            self.on_call(query)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def original_payload(context, chunk_id):
    with Session(context.engine) as session:
        chunk = session.get(Chunk, chunk_id)
        document = session.get(Document, chunk.document_id)
        return SearchHitPayload.from_hit(SearchHit(
            chunk.id, document.id, document.original_filename, chunk.chunk_index,
            chunk.content, chunk.start_char, chunk.end_char, 0.1,
        ))


def envelope(context, identifiers=(), *, strategy="vector"):
    return SearchEnvelope(
        workspace_id=context.workspace_id, knowledge_base_id=context.knowledge_base_id,
        strategy=strategy, hits=[original_payload(context, identifier) for identifier in identifiers],
    )


def install_mcp(context, outcome, decisions=None, *, on_call=None):
    client = ControlledMcpClient(outcome, on_call)
    context.client.app.dependency_overrides[api.get_mcp_search_client] = lambda: client
    planner, generation = install(context, decisions or [search(), FinishAction(action="finish")])
    return client, planner, generation


def post_mcp(context, **options):
    return context.client.post(url(context), json={"question": "如何恢复服务？", "search_backend": "mcp", **options})


def assert_rejected_evidence(context, outcome):
    client, planner, generation = install_mcp(context, outcome)
    response = post_mcp(context)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed" and body["stop_reason"] == "tool_error"
    assert body["steps"][0]["error_code"] == "mcp_evidence_invalid"
    assert body["evidence"] == [] and body["result"] is None
    assert len(client.calls) == 1 and len(planner.states) == 2
    assert not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_mcp_agent_reuses_verified_original_and_citation(retrieval_context, strategy):
    context = retrieval_context
    document_id, identifiers = context.document([vector(1)])
    original = "重启后检查日志，再恢复服务。"
    set_contents(context, identifiers, [original])
    client, planner, generation = install_mcp(context, envelope(context, identifiers, strategy=strategy))
    response = post_mcp(context, strategy=strategy)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["stop_reason"] is None
    assert [item["action"] for item in body["steps"]] == ["tool", "finish"]
    assert body["steps"][0]["evidence_ids"] == [1]
    citation = body["result"]["citations"][0]
    assert citation["chunk_id"] == str(identifiers[0]) and citation["document_id"] == str(document_id)
    assert citation["quote"] == original and citation["start_char"] == 0
    assert generation.calls[0][1][0].content == original
    assert planner.states[1]["evidence"] == [{"evidence_id": 1, "content": original}]
    assert client.calls == ["退款流程"] and context.provider.calls == 0


def test_empty_mcp_result_is_normal_abstention_not_tool_failure(retrieval_context):
    context = retrieval_context
    client, _, generation = install_mcp(context, envelope(context))
    response = post_mcp(context)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["result"]["abstained"]
    assert body["evidence"] == [] and body["steps"][0]["status"] == "success"
    assert len(client.calls) == 1 and not generation.calls and context.provider.calls == 0


def test_actual_production_stdio_and_database_empty_search_without_model_call(retrieval_context, monkeypatch):
    """真实固定模块/stdio/独立数据库的空结果链；不覆盖非空向量或真实模型质量。"""
    context = retrieval_context
    schema = context.engine.get_execution_options()["schema_translate_map"][None]
    # 子进程不能继承 Host 的 schema_translate_map，使用仅本次测试的连接级 search_path。
    # URL（可能含测试库口令）只用于配置传递，不写文件、不打印。
    database_url = context.engine.url.set(query={"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("EAAP_DATABASE_URL", database_url.render_as_string(hide_password=False))
    monkeypatch.setenv("EAAP_EMBEDDING_BASE_URL", DEFAULT_PROFILE.provider)
    monkeypatch.setenv("EAAP_EMBEDDING_MODEL", DEFAULT_PROFILE.model)
    monkeypatch.setenv("EAAP_EMBEDDING_DIMENSIONS", str(DEFAULT_PROFILE.dimensions))
    monkeypatch.setenv("EAAP_EMBEDDING_API_KEY", "fixture-key-no-network")
    planner, generation = install(context, [search(), FinishAction(action="finish")])
    assert api.get_mcp_search_client not in context.client.app.dependency_overrides
    response = post_mcp(context)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["result"]["abstained"]
    assert body["steps"][0]["status"] == "success" and body["evidence"] == []
    assert len(planner.states) == 2 and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("error, code", [
    (McpSearchError("mcp_timeout"), "mcp_timeout"),
    (McpSearchError("mcp_discovery_invalid"), "mcp_discovery_invalid"),
    (McpSearchError("mcp_result_invalid"), "mcp_result_invalid"),
    (RuntimeError("内部连接口令-不得暴露"), "mcp_service_error"),
])
def test_mcp_failure_is_fixed_observation_not_successful_abstention(retrieval_context, error, code):
    context = retrieval_context
    client, planner, generation = install_mcp(context, error)
    response = post_mcp(context)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed" and body["stop_reason"] == "tool_error"
    assert body["steps"][0]["error_code"] == code and body["result"] is None
    assert planner.states[1]["observations"][0]["status"] == "error"
    assert len(client.calls) == 1 and not generation.calls and context.provider.calls == 0
    assert "内部连接口令" not in response.text


def test_different_query_can_recover_after_mcp_timeout(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    accepted = envelope(context, identifiers)
    client, _, generation = install_mcp(
        context, McpSearchError("mcp_timeout"),
        [search("第一问"), search("换个问法"), FinishAction(action="finish")],
    )

    def recover(query):
        if query == "换个问法":
            client.outcome = accepted

    client.on_call = recover
    body = post_mcp(context).json()
    assert body["status"] == "completed"
    assert body["steps"][0]["error_code"] == "mcp_timeout"
    assert body["steps"][1]["status"] == "success"
    assert client.calls == ["第一问", "换个问法"] and len(generation.calls) == 1


@pytest.mark.parametrize("field", ["workspace_id", "knowledge_base_id", "strategy"])
def test_remote_envelope_scope_and_strategy_must_match_host(retrieval_context, field):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    changed = "hybrid" if field == "strategy" else uuid4()
    result = envelope(context, identifiers).model_copy(update={field: changed})
    assert_rejected_evidence(context, result)


@pytest.mark.parametrize("field", [
    "chunk_id", "document_id", "document_name", "chunk_index", "content", "offsets",
])
def test_remote_original_fields_are_verified_against_database(retrieval_context, field):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    result = envelope(context, identifiers)
    original = result.hits[0]
    changes = {
        "chunk_id": {"chunk_id": uuid4()},
        "document_id": {"document_id": uuid4()},
        "document_name": {"document_name": "伪造来源.txt"},
        "chunk_index": {"chunk_index": original.chunk_index + 1},
        "content": {"content": "正文已被替换。", "end_char": original.start_char + len("正文已被替换。")},
        "offsets": {"start_char": original.start_char + 1, "end_char": original.end_char + 1},
    }[field]
    forged = original.model_copy(update=changes)
    assert_rejected_evidence(context, result.model_copy(update={"hits": [forged]}))


def test_chunk_from_other_knowledge_base_cannot_be_laundered_into_host_scope(retrieval_context):
    context = retrieval_context
    other = context.knowledge_base()
    _, identifiers = context.document([vector(1)], knowledge_base_id=other)
    assert_rejected_evidence(context, envelope(context, identifiers))


@pytest.mark.parametrize("status", ["uploaded", "processing", "parsed", "failed"])
def test_nonready_document_is_not_valid_remote_evidence(retrieval_context, status):
    context = retrieval_context
    _, identifiers = context.document([vector(1)], status=status)
    assert_rejected_evidence(context, envelope(context, identifiers))


def test_chunk_without_embedding_is_not_valid_remote_evidence(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([None])
    assert_rejected_evidence(context, envelope(context, identifiers))


def test_one_invalid_hit_rejects_entire_batch_without_partial_evidence(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([vector(1), vector(1)])
    result = envelope(context, identifiers)
    invalid_second = result.hits[1].model_copy(update={"document_id": uuid4()})
    assert_rejected_evidence(context, result.model_copy(update={"hits": [result.hits[0], invalid_second]}))


@pytest.mark.parametrize("change", ["moved_scope", "changed_original", "became_nonready", "embedding_removed"])
def test_database_changed_during_external_call_is_rechecked(retrieval_context, change):
    context = retrieval_context
    document_id, identifiers = context.document([vector(1)])
    accepted = envelope(context, identifiers)
    another_workspace = context.workspace() if change == "moved_scope" else None

    def mutate(query):
        with Session(context.engine) as session, session.begin():
            if change == "moved_scope":
                session.get(KnowledgeBase, context.knowledge_base_id).workspace_id = another_workspace
            elif change == "became_nonready":
                session.get(Document, document_id).status = "parsed"
            elif change == "embedding_removed":
                session.get(Chunk, identifiers[0]).embedding = None
            else:
                chunk = session.get(Chunk, identifiers[0])
                chunk.content = "已更新的正文。"
                chunk.end_char = chunk.start_char + len(chunk.content)

    client, _, generation = install_mcp(context, accepted, on_call=mutate)
    body = post_mcp(context).json()
    assert body["status"] == "failed" and body["steps"][0]["error_code"] == "mcp_evidence_invalid"
    assert body["evidence"] == [] and not generation.calls and len(client.calls) == 1


def test_mcp_call_planner_and_generation_run_outside_host_transaction(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    result = envelope(context, identifiers)
    with Session(context.engine) as session:
        validate_scope(session, context.workspace_id, context.knowledge_base_id)

        def inspect(query):
            assert not session.in_transaction()

        client = ControlledMcpClient(result, inspect)
        registry = ToolRegistry()
        registry.register(McpKnowledgeSearchTool(session, client, context.workspace_id, context.knowledge_base_id))

        class InspectingPlanner(ControlledPlanner):
            def decide(self, state):
                assert not session.in_transaction()
                return super().decide(state)

        class InspectingGeneration(ControlledGeneration):
            def generate_answer(self, question, sources):
                assert not session.in_transaction()
                return super().generate_answer(question, sources)

        run = run_agent(InspectingPlanner([search(), FinishAction(action="finish")]),
                        InspectingGeneration(), registry, "如何恢复服务？")
        assert not session.in_transaction()
    assert run.status == "completed" and len(client.calls) == 1


def test_open_host_transaction_prevents_external_mcp_call(retrieval_context):
    context = retrieval_context
    client = ControlledMcpClient(envelope(context))
    with Session(context.engine) as session, session.begin():
        tool = McpKnowledgeSearchTool(session, client, context.workspace_id, context.knowledge_base_id)
        with pytest.raises(ToolExecutionError) as caught:
            tool.execute(SearchArguments(query="问题"))
        assert caught.value.code == "mcp_transaction_open"
    assert not client.calls


@pytest.mark.parametrize("backend", [None, "local"])
def test_default_and_explicit_local_backend_keep_existing_search(retrieval_context, backend):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    client, _, generation = install_mcp(context, RuntimeError("不得调用 MCP"))
    payload = {"question": "如何恢复服务？"}
    if backend is not None:
        payload["search_backend"] = backend
    body = context.client.post(url(context), json=payload).json()
    assert body["status"] == "completed" and body["evidence"][0]["chunk_id"] == str(identifiers[0])
    assert not client.calls and context.provider.calls == len(generation.calls) == 1


def test_unapproved_delete_action_never_reaches_mcp_client(retrieval_context):
    context = retrieval_context
    delete = ToolAction(action="tool", tool_name="delete_document", arguments={"document_id": str(uuid4())})
    client, planner, generation = install_mcp(context, envelope(context), [delete, FinishAction(action="finish")])
    body = post_mcp(context).json()
    assert body["status"] == "failed" and body["steps"][0]["error_code"] == "unknown_tool"
    assert {tool["name"] for tool in planner.states[0]["tools"]} == {"knowledge_search", "calculator"}
    assert not client.calls and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("mismatch", ["workspace", "knowledge_base"])
def test_wrong_host_scope_fails_before_planner_and_mcp_call(retrieval_context, mismatch):
    context = retrieval_context
    client, planner, generation = install_mcp(context, envelope(context))
    options = {"workspace_id": context.workspace()} if mismatch == "workspace" else {"knowledge_base_id": uuid4()}
    response = context.client.post(url(context, **options), json={"question": "问题", "search_backend": "mcp"})
    assert response.status_code == 404
    assert not planner.states and not client.calls and not generation.calls and context.provider.calls == 0


def test_first_mcp_backend_rejects_reranking_before_any_external_call(retrieval_context):
    context = retrieval_context
    client, planner, generation = install_mcp(context, envelope(context))
    response = post_mcp(context, rerank=True)
    assert response.status_code == 422
    assert not planner.states and not client.calls and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("backend", ["remote", "MCP", "", 1, None])
def test_unknown_backend_is_422_without_tool_or_model_call(retrieval_context, backend):
    context = retrieval_context
    client, planner, generation = install_mcp(context, envelope(context))
    response = post_mcp(context, search_backend=backend)
    assert response.status_code == 422
    assert not client.calls and not planner.states and not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("backend", ["local", "mcp"])
def test_backend_contract_keeps_question_and_strategy_shape(backend):
    payload = api.AgentRequest(question=" 问题 ", search_backend=backend)
    assert payload.question == "问题" and payload.strategy == "vector" and not payload.rerank
    assert payload.search_backend == backend


def test_backend_contract_defaults_to_local():
    assert api.AgentRequest(question="问题").search_backend == "local"
