"""持久工作流契约、真实 PostgreSQL 检查点与受控故障；不调用真实模型。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event, current_thread
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.answers import get_generation_provider
from app.api.documents import get_embedding_provider
from app.db.models import AnswerWorkflow, AnswerWorkflowAttempt, Chunk, Document, KnowledgeBase
from app.db.session import get_session
from app.main import create_app
from auth_helpers import copy_auth
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError
from app.providers.generation import (
    GeneratedAnswer, GenerationConfigurationError, GenerationError, GenerationTimeoutError,
)
from app.workflows import service
from app.workflows.contracts import (
    MAX_WORKFLOW_ATTEMPTS, WorkflowAnswer, WorkflowCheckpoint, WorkflowConflictError,
    WorkflowCreate, WorkflowEvidence, WorkflowExecute, WorkflowExecutionRead, WorkflowRead,
)
from test_agent_engine import ControlledGeneration, hit
from test_hybrid_retrieval import set_contents
from test_retrieval import retrieval_context, vector


class ObservedGeneration(ControlledGeneration):
    """调用时可注入数据库变更、重入竞争或生成故障。"""

    def __init__(self, error=None, draft=None, on_call=None):
        super().__init__(error, draft)
        self.on_call = on_call

    def generate_answer(self, question, sources):
        if self.on_call:
            self.on_call(question, sources)
        return super().generate_answer(question, sources)


def url(context, workflow_id=None, *, workspace_id=None, knowledge_base_id=None):
    base = (f"/api/v1/workspaces/{workspace_id or context.workspace_id}"
            f"/knowledge-bases/{knowledge_base_id or context.knowledge_base_id}/answer-workflows")
    return base if workflow_id is None else f"{base}/{workflow_id}"


def install(context, generation=None):
    generation = generation or ObservedGeneration()
    context.client.app.dependency_overrides[get_generation_provider] = lambda: generation
    return generation


def create(context, *, request_id=None, question="服务如何恢复？", strategy="vector", client=None):
    return (client or context.client).post(url(context), json={
        "request_id": str(request_id or uuid4()), "question": question, "strategy": strategy,
    })


def execute(context, workflow, *, request_id=None, version=None, recover=False, client=None):
    return (client or context.client).post(f"{url(context, workflow['workflow_id'])}/execute", json={
        "request_id": str(request_id or uuid4()),
        "expected_version": workflow["version"] if version is None else version,
        "recover_interrupted": recover,
    })


def read(context, workflow):
    return context.client.get(url(context, workflow["workflow_id"]))


def saved(context, workflow):
    with Session(context.engine) as session:
        record = session.get(AnswerWorkflow, UUID(workflow["workflow_id"]))
        assert record is not None
        return record


def other_app(context, generation):
    """新 FastAPI 应用与全新 Session，明确不依赖旧请求内存。"""
    app = create_app()

    def session_provider():
        with Session(context.engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = session_provider
    app.dependency_overrides[get_embedding_provider] = lambda: context.provider
    app.dependency_overrides[get_generation_provider] = lambda: generation
    client = TestClient(app)
    copy_auth(context.client, client)
    return client


def valid_checkpoint(**updates):
    value = dict(workspace_id=uuid4(), knowledge_base_id=uuid4(), strategy="vector",
                 hits=[WorkflowEvidence.from_hit(hit())])
    return WorkflowCheckpoint(**(value | updates))


def pending_read(**updates):
    value = dict(workflow_id=uuid4(), workspace_id=uuid4(), knowledge_base_id=uuid4(),
                 question="问题", strategy="vector", max_context_characters=12000,
                 status="pending", next_step="retrieve",
                 version=0, attempt_count=0, checkpoint=None, error_code=None, retryable=False,
                 active_request_id=None, lease_expires_at=None, result=None)
    return WorkflowRead(**(value | updates))


@pytest.mark.parametrize("question", ["问题", " 问题\n", "问" * 2000], ids=["plain", "trimmed", "maximum"])
def test_create_contract_trims_question(question):
    request = WorkflowCreate(request_id=uuid4(), question=question)
    assert request.question == question.strip() and request.strategy == "vector"


@pytest.mark.parametrize("changes", [
    {"question": ""}, {"question": " \n"}, {"question": "问" * 2001},
    {"question": 3}, {"question": None}, {"strategy": "mcp"},
    {"rerank": True}, {"top_k": 10}, {"search_backend": "mcp"},
    {"request_id": "not-a-uuid"},
])
def test_create_contract_rejects_unsupported_or_invalid_data(changes):
    with pytest.raises(ValidationError):
        WorkflowCreate.model_validate(dict(request_id=uuid4(), question="问题") | changes)


@pytest.mark.parametrize("changes", [
    {"expected_version": -1}, {"expected_version": 33}, {"expected_version": True},
    {"expected_version": 1.0}, {"expected_version": "1"}, {"recover_interrupted": 1},
    {"recover_interrupted": "true"}, {"unknown": True}, {"request_id": "bad"},
])
def test_execution_contract_is_strict(changes):
    with pytest.raises(ValidationError):
        WorkflowExecute.model_validate(dict(request_id=uuid4(), expected_version=0) | changes)


@pytest.mark.parametrize("version", [0, 32])
def test_execution_contract_accepts_boundaries(version):
    request = WorkflowExecute(request_id=uuid4(), expected_version=version)
    assert request.expected_version == version and request.recover_interrupted is False


@pytest.mark.parametrize("version", [True, 1.0, "1", 0, 2, None])
def test_checkpoint_version_is_exact_integer_one(version):
    with pytest.raises(ValidationError):
        valid_checkpoint(schema_version=version)


def test_checkpoint_json_roundtrip_preserves_source_and_scope():
    checkpoint = valid_checkpoint()
    parsed = WorkflowCheckpoint.model_validate_json(checkpoint.model_dump_json())
    assert parsed == checkpoint
    assert WorkflowEvidence.from_hit(parsed.hits[0].to_hit()) == parsed.hits[0]


def test_checkpoint_duplicate_ids_are_rejected():
    evidence = WorkflowEvidence.from_hit(hit())
    with pytest.raises(ValidationError):
        valid_checkpoint(hits=[evidence, evidence])


@pytest.mark.parametrize("count", [6, 21])
def test_checkpoint_count_is_bounded(count):
    with pytest.raises(ValidationError):
        valid_checkpoint(hits=[WorkflowEvidence.from_hit(hit()) for _ in range(count)])


def test_checkpoint_total_original_characters_are_bounded():
    with pytest.raises(ValidationError):
        valid_checkpoint(hits=[WorkflowEvidence.from_hit(hit("甲" * 6001)),
                               WorkflowEvidence.from_hit(hit("乙" * 6000))])


@pytest.mark.parametrize("changes", [
    {"status": "running"}, {"active_request_id": uuid4()},
    {"lease_expires_at": datetime.now(timezone.utc)}, {"next_step": "generate"},
    {"status": "completed"}, {"status": "failed"}, {"error_code": "generation_timeout"},
    {"retryable": True}, {"attempt_count": 1}, {"next_step": "done"},
])
def test_read_contract_rejects_inconsistent_initial_state(changes):
    with pytest.raises(ValidationError):
        pending_read(**changes)


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_creation_only_saves_pending_without_model_calls(retrieval_context, strategy):
    context = retrieval_context
    context.document([vector(1)])
    generation = install(context)
    response = create(context, question=" 如何恢复服务？\n", strategy=strategy)
    assert response.status_code == 201
    workflow = response.json()
    assert workflow["status"] == "pending" and workflow["next_step"] == "retrieve"
    assert workflow["version"] == workflow["attempt_count"] == 0
    assert workflow["question"] == "如何恢复服务？" and workflow["strategy"] == strategy
    assert workflow["checkpoint"] is workflow["result"] is workflow["error_code"] is None
    assert workflow["active_request_id"] is workflow["lease_expires_at"] is None
    assert not workflow["retryable"] and context.provider.calls == 0 and not generation.calls
    assert read(context, workflow).json() == workflow
    assert saved(context, workflow).status == "pending"


def test_create_same_request_and_normalized_content_replays_current_resource(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    generation = install(context)
    request_id = uuid4()
    first = create(context, request_id=request_id, question=" 问题 ").json()
    assert create(context, request_id=request_id, question="问题").json() == first
    completed = execute(context, first).json()["workflow"]
    repeated = create(context, request_id=request_id, question="问题")
    assert repeated.status_code in (200, 201) and repeated.json() == completed
    with Session(context.engine) as session:
        assert len(session.scalars(select(AnswerWorkflow)).all()) == 1
    assert context.provider.calls == len(generation.calls) == 1


@pytest.mark.parametrize("changes", [{"question": "另一问"}, {"strategy": "hybrid"}])
def test_create_same_request_different_content_is_conflict(retrieval_context, changes):
    context = retrieval_context
    generation = install(context)
    request_id = uuid4()
    workflow = create(context, request_id=request_id).json()
    response = create(context, request_id=request_id, **changes)
    assert response.status_code == 409 and read(context, workflow).json() == workflow
    assert context.provider.calls == 0 and not generation.calls


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_execute_persists_checkpoint_answer_and_receipt(retrieval_context, strategy):
    context = retrieval_context
    document_id, identifiers = context.document([vector(1)])
    generation = install(context)
    workflow = create(context, strategy=strategy).json()
    request_id = uuid4()
    response = execute(context, workflow, request_id=request_id)
    assert response.status_code == 200
    receipt = response.json()
    result = receipt["workflow"]
    assert receipt["request_id"] == str(request_id) and receipt["attempt_number"] == 1
    assert receipt["status"] == result["status"] == "completed"
    assert result["next_step"] == "done" and result["version"] == 3 and result["attempt_count"] == 1
    assert result["active_request_id"] is result["lease_expires_at"] is None
    assert result["checkpoint"]["hits"][0]["chunk_id"] == str(identifiers[0])
    citation = result["result"]["citations"][0]
    assert citation["document_id"] == str(document_id) and citation["chunk_id"] == str(identifiers[0])
    assert not result["result"]["abstained"] and citation["quote"] == generation.calls[0][1][0].content
    assert context.provider.calls == len(generation.calls) == 1
    assert read(context, workflow).json() == result
    with Session(context.engine) as session:
        attempts = session.scalars(select(AnswerWorkflowAttempt)).all()
        assert len(attempts) == 1 and attempts[0].status == "completed"
        assert attempts[0].response == receipt


@pytest.mark.parametrize("has_unready_chunks", [False, True])
def test_empty_checkpoint_is_completed_abstention_without_models(retrieval_context, has_unready_chunks):
    context = retrieval_context
    if has_unready_chunks:
        context.document([vector(1)], status="parsed")
    generation = install(context)
    workflow = create(context).json()
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "completed" and result["checkpoint"]["hits"] == []
    assert result["result"]["abstained"] and result["result"]["citations"] == []
    assert context.provider.calls == 0 and not generation.calls


def test_generation_timeout_survives_new_app_and_resumes_without_embedding(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    first_generation = install(context, ObservedGeneration(error=GenerationTimeoutError("私有信息")))
    workflow = create(context).json()
    request_id = uuid4()
    first_receipt = execute(context, workflow, request_id=request_id).json()
    failed = first_receipt["workflow"]
    assert first_receipt["status"] == failed["status"] == "failed"
    assert failed["error_code"] == "generation_timeout" and failed["retryable"]
    assert failed["next_step"] == "generate" and failed["version"] == 3
    assert failed["checkpoint"]["hits"] and failed["result"] is None
    assert "私有信息" not in str(first_receipt)
    assert context.provider.calls == len(first_generation.calls) == 1
    successful_generation = ObservedGeneration()
    with other_app(context, successful_generation) as new_client:
        assert new_client.get(url(context, workflow["workflow_id"])).json() == failed
        resumed = execute(context, failed, client=new_client)
        assert resumed.status_code == 200
        completed = resumed.json()["workflow"]
        assert completed["status"] == "completed" and completed["version"] == 5
        assert completed["attempt_count"] == 2 and completed["checkpoint"] == failed["checkpoint"]
        replay = execute(context, workflow, request_id=request_id, client=new_client)
        assert replay.status_code == 200 and replay.json() == first_receipt
    assert context.provider.calls == 1 and len(successful_generation.calls) == 1


@pytest.mark.parametrize("changes", [{"version": 1}, {"recover": True}])
def test_completed_request_reuse_requires_same_complete_payload(retrieval_context, changes):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    request_id = uuid4()
    receipt = execute(context, workflow, request_id=request_id).json()
    repeated = execute(context, workflow, request_id=request_id, **changes)
    assert repeated.status_code == 409
    assert read(context, workflow).json() == receipt["workflow"]
    assert not generation.calls and context.provider.calls == 0


@pytest.mark.parametrize("version", [1, 32])
def test_stale_version_cannot_start_attempt(retrieval_context, version):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    response = execute(context, workflow, version=version)
    assert response.status_code == 409 and read(context, workflow).json() == workflow
    with Session(context.engine) as session:
        assert session.scalars(select(AnswerWorkflowAttempt)).all() == []
    assert context.provider.calls == 0 and not generation.calls


@pytest.mark.parametrize("failure,code", [
    (EmbeddingTimeoutError("私有信息"), "embedding_timeout"),
    (EmbeddingError("私有信息"), "embedding_error"),
])
def test_retrieval_failure_saves_retryable_failed_attempt(retrieval_context, failure, code):
    context = retrieval_context
    context.document([vector(1)])
    context.provider.error = failure
    generation = install(context)
    workflow = create(context).json()
    response = execute(context, workflow)
    assert response.status_code == 200 and "私有信息" not in response.text
    failed = response.json()["workflow"]
    assert failed["status"] == "failed" and failed["error_code"] == code and failed["retryable"]
    assert failed["next_step"] == "retrieve" and failed["checkpoint"] is None and failed["version"] == 2
    context.provider.error = None
    assert execute(context, failed).json()["workflow"]["status"] == "completed"
    assert context.provider.calls == 2 and len(generation.calls) == 1


@pytest.mark.parametrize("failure,code", [
    (GenerationConfigurationError("私有信息"), "generation_configuration"),
    (GenerationTimeoutError("私有信息"), "generation_timeout"),
    (GenerationError("私有信息"), "generation_error"),
    (RuntimeError("私有信息"), "service_error"),
])
def test_generation_failure_saves_fixed_code_not_fake_abstention(retrieval_context, failure, code):
    context = retrieval_context
    context.document([vector(1)])
    generation = install(context, ObservedGeneration(error=failure))
    workflow = create(context).json()
    response = execute(context, workflow)
    assert response.status_code == 200 and "私有信息" not in response.text
    result = response.json()["workflow"]
    assert result["status"] == "failed" and result["error_code"] == code and result["retryable"]
    assert result["result"] is None and result["next_step"] == "generate"
    assert context.provider.calls == len(generation.calls) == 1


def failed_checkpoint(context):
    document_id, identifiers = context.document([vector(1)])
    generation = install(context, ObservedGeneration(error=GenerationTimeoutError("受控超时")))
    workflow = create(context).json()
    failed = execute(context, workflow).json()["workflow"]
    assert failed["error_code"] == "generation_timeout"
    generation.error = None
    return failed, generation, document_id, identifiers


@pytest.mark.parametrize("change", ["content", "document_name", "offsets", "status", "embedding", "scope"])
def test_resume_revalidates_database_original_before_generation(retrieval_context, change):
    context = retrieval_context
    failed, generation, document_id, identifiers = failed_checkpoint(context)
    other_base = context.knowledge_base() if change == "scope" else None
    with Session(context.engine) as session, session.begin():
        chunk = session.get(Chunk, identifiers[0])
        document = session.get(Document, document_id)
        if change == "content":
            chunk.content = "已更新原文。"
            chunk.end_char = chunk.start_char + len(chunk.content)
        elif change == "document_name":
            document.original_filename = "更新名称.txt"
        elif change == "offsets":
            chunk.start_char += 1
            chunk.end_char += 1
        elif change == "status":
            document.status = "parsed"
        elif change == "embedding":
            chunk.embedding = None
        else:
            document.knowledge_base_id = other_base
    response = execute(context, failed)
    assert response.status_code == 200
    invalid = response.json()["workflow"]
    assert invalid["status"] == "failed" and invalid["error_code"] == "evidence_changed"
    assert not invalid["retryable"] and invalid["checkpoint"] is invalid["result"] is None
    assert invalid["next_step"] == "retrieve"
    assert execute(context, invalid).status_code == 409
    assert context.provider.calls == len(generation.calls) == 1


@pytest.mark.parametrize("change", ["schema", "scope", "duplicate", "forged_original", "unknown_field"])
def test_tampered_checkpoint_is_fixed_nonretryable_failure(retrieval_context, change):
    context = retrieval_context
    failed, generation, _, _ = failed_checkpoint(context)
    with Session(context.engine) as session, session.begin():
        record = session.get(AnswerWorkflow, UUID(failed["workflow_id"]))
        checkpoint = dict(record.checkpoint)
        if change == "schema":
            checkpoint["schema_version"] = True
        elif change == "scope":
            checkpoint["knowledge_base_id"] = str(uuid4())
        elif change == "duplicate":
            checkpoint["hits"] = checkpoint["hits"] * 2
        elif change == "unknown_field":
            checkpoint["unknown"] = "不应接受"
        else:
            source = dict(checkpoint["hits"][0])
            source["content"] = "伪造原文。"
            source["end_char"] = source["start_char"] + len(source["content"])
            checkpoint["hits"] = [source]
        record.checkpoint = checkpoint
        if change == "schema":
            # Python 中 True == 1，必须显式标记 JSONB 修改才能真实保存故障样本。
            flag_modified(record, "checkpoint")
    if change == "schema":
        with Session(context.engine) as session:
            persisted = session.get(AnswerWorkflow, UUID(failed["workflow_id"])).checkpoint
            assert type(persisted["schema_version"]) is bool
    response = execute(context, failed)
    assert response.status_code == 200
    result = response.json()["workflow"]
    expected = "evidence_changed" if change == "forged_original" else "checkpoint_invalid"
    assert result["status"] == "failed" and result["error_code"] == expected
    assert not result["retryable"] and result["checkpoint"] is None
    assert context.provider.calls == len(generation.calls) == 1


def test_original_changed_during_generation_cannot_be_saved_as_success(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])

    def update_original(question, sources):
        set_contents(context, identifiers, ["模型生成期间已更新原文。"])

    generation = install(context, ObservedGeneration(on_call=update_original))
    workflow = create(context).json()
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "failed" and result["error_code"] == "evidence_changed"
    assert result["result"] is result["checkpoint"] is None and not result["retryable"]
    assert context.provider.calls == len(generation.calls) == 1


def test_generation_invalid_quote_is_failed_with_checkpoint_retained(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    draft = GeneratedAnswer(answer="错误答案", abstained=False,
                            citations=[{"source_id": 1, "quote": "原文不存在的引用"}])
    generation = install(context, ObservedGeneration(draft=draft))
    workflow = create(context).json()
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "failed" and result["error_code"] == "generation_error"
    assert result["result"] is None and result["checkpoint"]["hits"]
    assert context.provider.calls == len(generation.calls) == 1


def test_retry_limit_stops_new_attempt_but_keeps_old_receipt_replay(retrieval_context):
    context = retrieval_context
    workflow, generation, _, _ = failed_checkpoint(context)
    generation.error = GenerationTimeoutError("受控超时")
    last_request = None
    last_before = None
    last_receipt = None
    for _ in range(MAX_WORKFLOW_ATTEMPTS - 1):
        last_request, last_before = uuid4(), workflow
        response = execute(context, workflow, request_id=last_request)
        assert response.status_code == 200
        last_receipt = response.json()
        workflow = last_receipt["workflow"]
    assert workflow["attempt_count"] == MAX_WORKFLOW_ATTEMPTS and not workflow["retryable"]
    assert execute(context, workflow).status_code == 409
    assert execute(context, last_before, request_id=last_request).json() == last_receipt
    assert context.provider.calls == 1 and len(generation.calls) == MAX_WORKFLOW_ATTEMPTS


@pytest.mark.parametrize("scope", ["workspace", "knowledge_base", "workflow"])
def test_wrong_resource_scope_returns_notfound_before_models(retrieval_context, scope):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    options = ({"workspace_id": context.workspace()} if scope == "workspace" else
               {"knowledge_base_id": context.knowledge_base()} if scope == "knowledge_base" else {})
    target = str(uuid4()) if scope == "workflow" else workflow["workflow_id"]
    route = url(context, target, **options)
    assert context.client.get(route).status_code == 404
    assert context.client.post(f"{route}/execute", json={
        "request_id": str(uuid4()), "expected_version": 0,
    }).status_code == 404
    assert context.provider.calls == 0 and not generation.calls


@pytest.mark.parametrize("changes", [
    {"rerank": True}, {"top_k": 10}, {"search_backend": "mcp"},
    {"question": " "}, {"question": 1}, {"strategy": "unknown"},
])
def test_api_rejects_unsupported_create_options_before_models(retrieval_context, changes):
    context = retrieval_context
    generation = install(context)
    response = context.client.post(url(context), json={
        "request_id": str(uuid4()), "question": "问题",
    } | changes)
    assert response.status_code == 422 and context.provider.calls == 0 and not generation.calls


def test_competing_execute_during_generation_cannot_claim_running_workflow(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    workflow = create(context).json()
    competing = []

    def compete(question, sources):
        current = read(context, workflow).json()
        assert current["status"] == "running" and current["next_step"] == "generate"
        assert current["version"] == 2 and current["active_request_id"]
        competing.append(execute(context, current))

    generation = install(context, ObservedGeneration(on_call=compete))
    result = execute(context, workflow)
    assert result.status_code == 200 and result.json()["workflow"]["status"] == "completed"
    assert len(competing) == 1 and competing[0].status_code == 409
    assert context.provider.calls == len(generation.calls) == 1


def test_expired_lease_needs_explicit_new_request_and_fences_old_worker(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    workflow = create(context).json()
    old_request = uuid4()
    recovered = []
    generation = ObservedGeneration()

    def reclaim(question, sources):
        generation.on_call = None
        with Session(context.engine) as session, session.begin():
            record = session.get(AnswerWorkflow, UUID(workflow["workflow_id"]))
            record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        expired = read(context, workflow).json()
        assert expired["status"] == "running"
        assert execute(context, expired).status_code == 409
        assert execute(context, expired, request_id=old_request, recover=True).status_code == 409
        recovered.append(execute(context, expired, recover=True))

    generation.on_call = reclaim
    install(context, generation)
    old_response = execute(context, workflow, request_id=old_request)
    assert old_response.status_code == 409
    assert len(recovered) == 1 and recovered[0].status_code == 200
    current = read(context, workflow).json()
    assert current == recovered[0].json()["workflow"] and current["status"] == "completed"
    assert current["attempt_count"] == 2 and current["version"] == 5
    old_receipt = execute(context, workflow, request_id=old_request)
    assert old_receipt.status_code == 200 and old_receipt.json()["status"] == "interrupted"
    assert old_receipt.json()["workflow"]["error_code"] == "execution_interrupted"
    assert context.provider.calls == 1 and len(generation.calls) == 2


def test_model_calls_do_not_hold_host_transaction(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    opened = []

    def observed_session():
        with Session(context.engine, expire_on_commit=False) as session:
            opened.append(session)
            yield session

    context.client.app.dependency_overrides[get_session] = observed_session
    workflow = create(context).json()

    def inspect(*args):
        assert opened and not opened[-1].in_transaction()

    context.provider.on_call = inspect
    generation = install(context, ObservedGeneration(on_call=inspect))
    response = execute(context, workflow)
    assert response.status_code == 200 and response.json()["workflow"]["status"] == "completed"
    assert context.provider.calls == len(generation.calls) == 1


@pytest.mark.parametrize("configured,expected", [(50000, 12000), (12000, 12000), (80, 80)])
def test_creation_freezes_capped_context_budget(retrieval_context, monkeypatch, configured, expected):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    set_contents(context, identifiers, ["完整原文。"])
    generation = install(context)
    monkeypatch.setattr(service, "Settings", lambda: SimpleNamespace(generation_max_context_characters=configured))
    workflow = create(context).json()
    assert workflow["max_context_characters"] == expected
    monkeypatch.setattr(service, "Settings", lambda: SimpleNamespace(generation_max_context_characters=1))
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "completed" and result["max_context_characters"] == expected
    assert len(generation.calls) == 1


def test_checkpoint_saves_only_full_blocks_selected_by_budget(retrieval_context, monkeypatch):
    context = retrieval_context
    _, identifiers = context.document([vector(1), vector(0.9, 0.1)])
    set_contents(context, identifiers, ["甲" * 30, "乙" * 10])
    generation = install(context)
    monkeypatch.setattr(service, "Settings", lambda: SimpleNamespace(generation_max_context_characters=10))
    workflow = create(context).json()
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "completed"
    assert [source["chunk_id"] for source in result["checkpoint"]["hits"]] == [str(identifiers[1])]
    assert [source.content for source in generation.calls[0][1]] == ["乙" * 10]


def test_no_complete_block_fits_budget_is_nonretryable_failure(retrieval_context, monkeypatch):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    set_contents(context, identifiers, ["甲" * 11])
    generation = install(context)
    monkeypatch.setattr(service, "Settings", lambda: SimpleNamespace(generation_max_context_characters=10))
    workflow = create(context).json()
    result = execute(context, workflow).json()["workflow"]
    assert result["status"] == "failed" and result["error_code"] == "context_limit"
    assert not result["retryable"] and result["checkpoint"] is result["result"] is None
    assert not generation.calls and context.provider.calls == 1


def test_creation_request_id_is_scoped_to_knowledge_base(retrieval_context):
    context = retrieval_context
    request_id = uuid4()
    first = create(context, request_id=request_id).json()
    another = context.knowledge_base()
    response = context.client.post(url(context, knowledge_base_id=another), json={
        "request_id": str(request_id), "question": "另一个库的问题",
    })
    assert response.status_code == 201
    second = response.json()
    assert first["workflow_id"] != second["workflow_id"]
    assert second["knowledge_base_id"] == str(another)


def test_execute_request_id_is_scoped_to_workflow(retrieval_context):
    context = retrieval_context
    generation = install(context)
    first, second = create(context).json(), create(context, question="另一问").json()
    request_id = uuid4()
    one = execute(context, first, request_id=request_id)
    two = execute(context, second, request_id=request_id)
    assert one.status_code == two.status_code == 200
    assert one.json()["workflow"]["workflow_id"] != two.json()["workflow"]["workflow_id"]
    assert not generation.calls and context.provider.calls == 0


def test_uncaught_worker_crash_leaves_durable_checkpoint_for_explicit_recovery(retrieval_context):
    """用 BaseException 模拟执行者消失，不声称真实 OS 杀进程或外部恰好一次。"""
    context = retrieval_context
    context.document([vector(1)])
    workflow = create(context).json()
    request_id = uuid4()

    class WorkerDisappeared(BaseException):
        pass

    class CrashingGeneration:
        def generate_answer(self, question, sources):
            raise WorkerDisappeared()

    with Session(context.engine, expire_on_commit=False) as session, pytest.raises(WorkerDisappeared):
        service.execute_workflow(session, context.provider, CrashingGeneration(),
                                 context.workspace_id, context.knowledge_base_id,
                                 UUID(workflow["workflow_id"]),
                                 WorkflowExecute(request_id=request_id, expected_version=0))
    durable = read(context, workflow).json()
    assert durable["status"] == "running" and durable["next_step"] == "generate"
    assert durable["version"] == 2 and durable["checkpoint"]["hits"]
    assert durable["active_request_id"] == str(request_id)
    assert execute(context, durable, recover=True).status_code == 409
    assert execute(context, workflow, request_id=request_id).status_code == 409
    with Session(context.engine) as session, session.begin():
        record = session.get(AnswerWorkflow, UUID(workflow["workflow_id"]))
        record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = read(context, workflow).json()
    generation = ObservedGeneration()
    with other_app(context, generation) as new_client:
        recovered = execute(context, expired, recover=True, client=new_client)
        assert recovered.status_code == 200 and recovered.json()["workflow"]["status"] == "completed"
        old_receipt = execute(context, workflow, request_id=request_id, client=new_client)
        assert old_receipt.status_code == 200 and old_receipt.json()["status"] == "interrupted"
    assert context.provider.calls == len(generation.calls) == 1


@pytest.mark.parametrize("changes", [
    {"expected_version": True}, {"expected_version": "0"}, {"expected_version": -1},
    {"recover_interrupted": 1}, {"unknown": "不允许"}, {"request_id": "bad"},
])
def test_api_execute_input_rejection_does_not_claim_attempt(retrieval_context, changes):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    response = context.client.post(f"{url(context, workflow['workflow_id'])}/execute", json={
        "request_id": str(uuid4()), "expected_version": 0,
    } | changes)
    assert response.status_code == 422 and read(context, workflow).json() == workflow
    with Session(context.engine) as session:
        assert session.scalars(select(AnswerWorkflowAttempt)).all() == []
    assert context.provider.calls == 0 and not generation.calls


def test_recovery_flag_is_not_a_general_override_for_pending_workflow(retrieval_context):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    assert execute(context, workflow, recover=True).status_code == 409
    assert read(context, workflow).json() == workflow
    assert context.provider.calls == 0 and not generation.calls


def test_completed_workflow_rejects_new_execution_without_repeating_models(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    generation = install(context)
    workflow = create(context).json()
    completed = execute(context, workflow).json()["workflow"]
    assert execute(context, completed).status_code == 409
    assert read(context, workflow).json() == completed
    assert context.provider.calls == len(generation.calls) == 1


def test_fixed_workflow_stores_at_most_five_retrieved_blocks(retrieval_context):
    context = retrieval_context
    _, identifiers = context.document([vector(1)] * 8)
    generation = install(context)
    workflow = create(context).json()
    completed = execute(context, workflow).json()["workflow"]
    originals = completed["checkpoint"]["hits"]
    assert len(originals) == len(generation.calls[0][1]) == 5
    assert {source["chunk_id"] for source in originals}.issubset({str(item) for item in identifiers})
    assert context.provider.calls == len(generation.calls) == 1


def valid_terminal_read(status="failed"):
    checkpoint = valid_checkpoint(hits=[])
    is_failed = status == "failed"
    return pending_read(
        workspace_id=checkpoint.workspace_id, knowledge_base_id=checkpoint.knowledge_base_id,
        status=status, next_step="generate" if is_failed else "done", version=3, attempt_count=1,
        checkpoint=checkpoint, error_code="generation_timeout" if is_failed else None,
        retryable=is_failed,
        result=None if is_failed else WorkflowAnswer(answer="证据不足。", abstained=True, citations=[]),
    )


@pytest.mark.parametrize("status,workflow_status", [
    ("failed", "failed"), ("interrupted", "failed"), ("completed", "completed"),
])
def test_execution_receipt_contract_accepts_consistent_terminal_states(status, workflow_status):
    result = WorkflowExecutionRead(request_id=uuid4(), attempt_number=1, status=status,
                                   workflow=valid_terminal_read(workflow_status))
    assert result.workflow.attempt_count == result.attempt_number


@pytest.mark.parametrize("status,workflow_status,number", [
    ("completed", "failed", 1), ("failed", "completed", 1), ("interrupted", "completed", 1),
    ("completed", "completed", 2), ("failed", "failed", 2), ("interrupted", "failed", 2),
])
def test_execution_receipt_contract_rejects_status_or_attempt_mismatch(status, workflow_status, number):
    with pytest.raises(ValidationError):
        WorkflowExecutionRead(request_id=uuid4(), attempt_number=number, status=status,
                              workflow=valid_terminal_read(workflow_status))


@pytest.mark.parametrize("change", [
    "request_id", "attempt_number", "workflow_id", "workspace_id", "knowledge_base_id",
    "status", "inner_status", "unknown",
])
def test_corrupted_persisted_execution_receipt_is_not_replayed(retrieval_context, change):
    context = retrieval_context
    generation = install(context)
    workflow = create(context).json()
    request_id = uuid4()
    receipt = execute(context, workflow, request_id=request_id).json()
    with Session(context.engine) as session, session.begin():
        attempt = session.scalar(select(AnswerWorkflowAttempt).where(
            AnswerWorkflowAttempt.request_id == request_id,
        ))
        payload = deepcopy(attempt.response)
        if change == "request_id":
            payload["request_id"] = str(uuid4())
        elif change == "attempt_number":
            payload["attempt_number"] = payload["workflow"]["attempt_count"] = 2
        elif change == "workflow_id":
            payload["workflow"]["workflow_id"] = str(uuid4())
        elif change in {"workspace_id", "knowledge_base_id"}:
            wrong_scope = str(uuid4())
            payload["workflow"][change] = wrong_scope
            payload["workflow"]["checkpoint"][change] = wrong_scope
        elif change == "status":
            payload["status"] = "failed"
        elif change == "inner_status":
            payload["workflow"].update(
                status="failed", next_step="generate", result=None,
                error_code="generation_timeout", retryable=True,
            )
        else:
            payload["unknown"] = "私有内容-不得暴露"
        attempt.response = payload
    replay = execute(context, workflow, request_id=request_id)
    assert replay.status_code == 409 and "私有内容" not in replay.text
    assert read(context, workflow).json() == receipt["workflow"]
    assert context.provider.calls == 0 and not generation.calls


def test_expired_running_with_corrupted_checkpoint_saves_failed_recovery_receipts(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    workflow = create(context).json()
    old_request = uuid4()

    class WorkerDisappeared(BaseException):
        pass

    class CrashingGeneration:
        def generate_answer(self, question, sources):
            raise WorkerDisappeared()

    with Session(context.engine, expire_on_commit=False) as session, pytest.raises(WorkerDisappeared):
        service.execute_workflow(session, context.provider, CrashingGeneration(),
                                 context.workspace_id, context.knowledge_base_id,
                                 UUID(workflow["workflow_id"]),
                                 WorkflowExecute(request_id=old_request, expected_version=0))
    running = read(context, workflow).json()
    with Session(context.engine) as session, session.begin():
        record = session.get(AnswerWorkflow, UUID(workflow["workflow_id"]))
        record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        checkpoint = deepcopy(record.checkpoint)
        checkpoint["schema_version"] = True
        record.checkpoint = checkpoint
        flag_modified(record, "checkpoint")
    with Session(context.engine) as session:
        persisted = session.get(AnswerWorkflow, UUID(workflow["workflow_id"])).checkpoint
        assert type(persisted["schema_version"]) is bool
    # 损坏内容不会作为合法视图公开，但显式恢复仍能写入固定失败回执。
    assert read(context, workflow).status_code == 409
    generation = install(context)
    new_request = uuid4()
    response = execute(context, running, request_id=new_request, recover=True)
    assert response.status_code == 200
    failed = response.json()["workflow"]
    assert response.json()["status"] == failed["status"] == "failed"
    assert failed["error_code"] == "checkpoint_invalid" and not failed["retryable"]
    assert failed["next_step"] == "retrieve" and failed["checkpoint"] is None
    assert failed["version"] == 5 and failed["attempt_count"] == 2
    old = execute(context, workflow, request_id=old_request)
    assert old.status_code == 200 and old.json()["status"] == "interrupted"
    assert old.json()["workflow"]["error_code"] == "checkpoint_invalid"
    assert execute(context, running, request_id=new_request, recover=True).json() == response.json()
    assert execute(context, failed).status_code == 409
    assert context.provider.calls == 1 and not generation.calls


def test_selected_evidence_checkpoint_is_not_a_whole_knowledge_base_version(retrieval_context):
    context = retrieval_context
    failed, generation, _, _ = failed_checkpoint(context)
    _, new_identifiers = context.document([vector(1)], name="后来新增的文档.txt")
    set_contents(context, new_identifiers, ["此内容在第一次检索以后才加入。"])
    response = execute(context, failed)
    assert response.status_code == 200
    completed = response.json()["workflow"]
    assert completed["status"] == "completed" and completed["checkpoint"] == failed["checkpoint"]
    assert str(new_identifiers[0]) not in {item["chunk_id"] for item in completed["checkpoint"]["hits"]}
    assert context.provider.calls == 1 and len(generation.calls) == 2
    assert generation.calls[0][1] == generation.calls[1][1]


def test_expired_lease_after_row_lock_wait_uses_current_database_clock(retrieval_context):
    """租约缩为 3 秒并持行锁，防止 now() 的事务开始时间错误允许旧 worker 写回。"""
    context = retrieval_context
    workflow = create(context).json()
    workflow_id = UUID(workflow["workflow_id"])
    with Session(context.engine, expire_on_commit=False) as session:
        state = service._claim(
            session, context.workspace_id, context.knowledge_base_id, workflow_id,
            WorkflowExecute(request_id=uuid4(), expected_version=0),
        )
    reached_lock = Event()

    def observe_statement(connection, cursor, statement, parameters, execution_context, executemany):
        if (current_thread().name.startswith("workflow-fence")
                and "answer_workflows" in statement and "FOR UPDATE" in statement):
            reached_lock.set()

    def old_worker():
        with Session(context.engine, expire_on_commit=False) as session:
            return service._finish(session, state, error_code="generation_timeout")

    event.listen(context.engine, "before_cursor_execute", observe_statement)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-fence") as executor:
            with Session(context.engine) as holder, holder.begin():
                row = holder.scalar(select(AnswerWorkflow).where(AnswerWorkflow.id == workflow_id).with_for_update())
                deadline = holder.scalar(select(func.clock_timestamp() + text("interval '3 seconds'")))
                row.lease_expires_at = deadline
                holder.flush()
                future = executor.submit(old_worker)
                assert reached_lock.wait(timeout=2), "旧 worker 未及时进入行锁等待"
                before_expiry = holder.scalar(select(func.clock_timestamp()))
                assert before_expiry < deadline
                holder.execute(select(func.pg_sleep((deadline - before_expiry).total_seconds() + 0.05)))
                assert holder.scalar(select(func.clock_timestamp())) >= deadline
            with pytest.raises(WorkflowConflictError):
                future.result(timeout=5)
    finally:
        event.remove(context.engine, "before_cursor_execute", observe_statement)
    durable = read(context, workflow).json()
    assert durable["status"] == "running" and durable["version"] == 1
    assert durable["error_code"] is durable["result"] is None
    with Session(context.engine) as session:
        attempt = session.scalar(select(AnswerWorkflowAttempt))
        assert attempt.status == "running" and attempt.response is None
    assert context.provider.calls == 0


@pytest.mark.parametrize("budget", [1, 5])
def test_read_contract_rejects_checkpoint_exceeding_its_frozen_budget(budget):
    checkpoint = valid_checkpoint(hits=[WorkflowEvidence.from_hit(hit("甲" * 6))])
    with pytest.raises(ValidationError):
        pending_read(
            workspace_id=checkpoint.workspace_id, knowledge_base_id=checkpoint.knowledge_base_id,
            status="failed", next_step="generate", version=3, attempt_count=1,
            checkpoint=checkpoint, error_code="generation_timeout", retryable=True,
            max_context_characters=budget,
        )


def test_corrupted_frozen_budget_cannot_silently_drop_saved_originals(retrieval_context):
    context = retrieval_context
    failed, generation, _, _ = failed_checkpoint(context)
    with Session(context.engine) as session, session.begin():
        record = session.get(AnswerWorkflow, UUID(failed["workflow_id"]))
        record.max_context_characters = 1
    assert read(context, failed).status_code == 409
    response = execute(context, failed)
    assert response.status_code == 200
    terminal = response.json()["workflow"]
    assert terminal["status"] == "failed" and terminal["error_code"] == "checkpoint_invalid"
    assert terminal["checkpoint"] is None and not terminal["retryable"]
    assert context.provider.calls == len(generation.calls) == 1


@pytest.mark.parametrize("step", ["checkpoint", "finalize"])
def test_evidence_lock_wait_past_deadline_cannot_write_checkpoint_or_answer(retrieval_context, step):
    """原文共享锁等待跨过短租约，核验之后仍须重新检查旧 worker 的执行权。"""
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    workflow = create(context).json()
    workflow_id = UUID(workflow["workflow_id"])
    checkpoint = WorkflowCheckpoint(
        workspace_id=context.workspace_id, knowledge_base_id=context.knowledge_base_id,
        strategy="vector", hits=[WorkflowEvidence.from_hit(item) for item in context.search().results],
    )
    with Session(context.engine, expire_on_commit=False) as session:
        state = service._claim(
            session, context.workspace_id, context.knowledge_base_id, workflow_id,
            WorkflowExecute(request_id=uuid4(), expected_version=0),
        )
        if step == "finalize":
            state = service._save_checkpoint(session, state, checkpoint)
    with Session(context.engine) as session, session.begin():
        row = session.get(AnswerWorkflow, workflow_id)
        deadline = session.scalar(select(func.clock_timestamp() + text("interval '3 seconds'")))
        row.lease_expires_at = deadline
    reached_original = Event()

    def observe_statement(connection, cursor, statement, parameters, execution_context, executemany):
        if (current_thread().name.startswith("workflow-evidence")
                and "chunks" in statement and "FOR SHARE" in statement):
            reached_original.set()

    def old_worker():
        with Session(context.engine, expire_on_commit=False) as session:
            if step == "checkpoint":
                return service._save_checkpoint(session, state, checkpoint)
            return service._finish(session, state, result=WorkflowAnswer(
                answer="证据不足。", abstained=True, citations=[],
            ))

    event.listen(context.engine, "before_cursor_execute", observe_statement)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-evidence") as executor:
            with Session(context.engine) as holder, holder.begin():
                holder.scalar(select(Chunk).where(Chunk.id == identifiers[0]).with_for_update())
                future = executor.submit(old_worker)
                assert reached_original.wait(timeout=2), "旧 worker 未及时进入原文锁等待"
                before_expiry = holder.scalar(select(func.clock_timestamp()))
                assert before_expiry < deadline
                holder.execute(select(func.pg_sleep((deadline - before_expiry).total_seconds() + 0.05)))
                assert holder.scalar(select(func.clock_timestamp())) >= deadline
            with pytest.raises(WorkflowConflictError):
                future.result(timeout=5)
    finally:
        event.remove(context.engine, "before_cursor_execute", observe_statement)
    durable = read(context, workflow).json()
    assert durable["status"] == "running" and durable["version"] == state.version
    assert durable["result"] is None
    assert (durable["checkpoint"] is None) == (step == "checkpoint")
    with Session(context.engine) as session:
        attempt = session.scalar(select(AnswerWorkflowAttempt))
        assert attempt.status == "running" and attempt.response is None
    assert context.provider.calls == 1


def test_database_commit_failure_is_unknown_running_state_not_fake_model_error(retrieval_context, monkeypatch):
    """注入 SQLAlchemyError，不切断网络，也不把无法确认提交结果当作已知失败。"""
    context = retrieval_context
    context.document([vector(1)])
    generation = install(context)
    workflow = create(context).json()
    request_id = uuid4()
    original_save = service._save_checkpoint

    def fail_commit(session, state, checkpoint):
        raise SQLAlchemyError("私有数据库信息-禁止暴露")

    monkeypatch.setattr(service, "_save_checkpoint", fail_commit)
    response = execute(context, workflow, request_id=request_id)
    assert response.status_code == 503 and "私有数据库信息" not in response.text
    running = read(context, workflow).json()
    assert running["status"] == "running" and running["version"] == 1
    assert running["checkpoint"] is running["result"] is running["error_code"] is None
    assert execute(context, workflow, request_id=request_id).status_code == 409
    assert context.provider.calls == 1 and not generation.calls
    with Session(context.engine) as session:
        attempt = session.scalar(select(AnswerWorkflowAttempt))
        assert attempt.status == "running" and attempt.response is None
    monkeypatch.setattr(service, "_save_checkpoint", original_save)
    with Session(context.engine) as session, session.begin():
        record = session.get(AnswerWorkflow, UUID(workflow["workflow_id"]))
        record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = read(context, workflow).json()
    recovered = execute(context, expired, recover=True)
    assert recovered.status_code == 200 and recovered.json()["workflow"]["status"] == "completed"
    assert execute(context, workflow, request_id=request_id).json()["status"] == "interrupted"
    # 第一次没有提交检索检查点，恢复必须重做该步骤，不声称避免全部重复调用。
    assert context.provider.calls == 2 and len(generation.calls) == 1
