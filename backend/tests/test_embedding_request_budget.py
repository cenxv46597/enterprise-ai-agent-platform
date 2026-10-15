"""请求级向量预算只能收紧服务端上限；不加载应用配置、数据库或真实模型。"""

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import documents
from app.db.session import get_session
from app.knowledge.embedding_repository import EmbeddingLimitError, EmbeddingResult


WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID = (UUID(int=value) for value in (11, 21, 31))
URL = (
    f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases/{KNOWLEDGE_BASE_ID}"
    f"/documents/{DOCUMENT_ID}/embeddings"
)


class MemorySession:
    def __init__(self) -> None:
        self.scope_reads = 0
        self.rollbacks = 0

    def scalar(self, statement: object) -> SimpleNamespace:
        self.scope_reads += 1
        return SimpleNamespace(id=DOCUMENT_ID)

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def budget_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    state = SimpleNamespace(
        server_cap=80, session=MemorySession(), provider=object(), calls=[], error=None,
    )
    monkeypatch.setattr(documents, "Settings", lambda: SimpleNamespace(embedding_max_chunks=state.server_cap))

    def controlled_embed(session, provider, document_id, *, max_chunks):
        assert session is state.session and provider is state.provider
        assert document_id == DOCUMENT_ID
        state.calls.append(max_chunks)
        if state.error is not None:
            raise state.error
        return EmbeddingResult(DOCUMENT_ID, "ready", "controlled-embedding", 1024, 2, False)

    monkeypatch.setattr(documents, "embed_document_chunks", controlled_embed)
    # 单独挂载路由，避免 app.main.create_app() 实例化真实 Settings。
    # 这里专测 HTTP 预算契约，不证明身份认证、成员权限或真实服务持久化。
    app = FastAPI()
    app.include_router(documents.router)
    app.dependency_overrides[get_session] = lambda: state.session
    app.dependency_overrides[documents.get_embedding_provider] = lambda: state.provider
    with TestClient(app) as client:
        state.client = client
        yield state


@pytest.mark.parametrize(
    "requested_budget,expected_limit",
    [(None, 80), (5, 5), (1000, 80)],
    ids=["legacy-no-body", "lower-budget", "cannot-raise-server-cap"],
)
def test_effective_budget_preserves_legacy_and_only_reduces_server_limit(
    budget_api, requested_budget, expected_limit,
) -> None:
    options = {} if requested_budget is None else {"json": {"max_chunks": requested_budget}}
    response = budget_api.client.post(URL, **options)
    assert response.status_code == 200
    assert budget_api.calls == [expected_limit]
    assert budget_api.session.scope_reads == 1 and budget_api.session.rollbacks == 1
    assert response.json() == {
        "document_id": str(DOCUMENT_ID), "status": "ready", "model": "controlled-embedding",
        "dimensions": 1024, "chunk_count": 2, "reused": False,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"max_chunks": 0},
        {"max_chunks": True},
        {"max_chunks": 1.5},
        {"max_chunks": "5"},
        {"max_chunks": 1001},
        {"max_chunks": 5, "extra": "not-allowed"},
        {},
    ],
    ids=["zero", "bool", "float", "string", "over-limit", "extra-field", "missing-field"],
)
def test_invalid_request_budget_is_rejected_before_scope_or_embedding_service(budget_api, payload) -> None:
    response = budget_api.client.post(URL, json=payload)
    assert response.status_code == 422
    assert budget_api.calls == []
    assert budget_api.session.scope_reads == 0 and budget_api.session.rollbacks == 0


def test_service_embedding_limit_error_still_maps_to_safe_422(budget_api) -> None:
    budget_api.error = EmbeddingLimitError("controlled-private-domain-diagnostic")
    response = budget_api.client.post(URL, json={"max_chunks": 5})
    assert response.status_code == 422
    assert response.json() == {"detail": "文档超过当前同步向量生成的块数上限"}
    assert "controlled-private-domain-diagnostic" not in response.text
    assert budget_api.calls == [5]
    assert budget_api.session.scope_reads == 1 and budget_api.session.rollbacks == 1
