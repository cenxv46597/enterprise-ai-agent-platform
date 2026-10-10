"""配置从环境变量读取，错误配置通过接口安全报告。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from app.api import documents as document_api
from app.core.config import REPOSITORY_ROOT, Settings
from app.db.session import get_session
from app.main import create_app


def test_integer_limits_can_be_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EAAP_EMBEDDING_BATCH_SIZE", "10")
    monkeypatch.setenv("EAAP_EMBEDDING_MAX_CHUNKS", "200")
    monkeypatch.setenv("EAAP_EMBEDDING_DIMENSIONS", "1024")
    settings = Settings(_env_file=None)
    assert settings.embedding_batch_size == 10
    assert settings.embedding_max_chunks == 200
    assert settings.embedding_dimensions == 1024


def test_example_configuration_can_be_loaded() -> None:
    settings = Settings(_env_file=REPOSITORY_ROOT / ".env.example")
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 10
    assert settings.embedding_max_chunks == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_base_url": "http://example.test/v1"},
        {"embedding_api_key": SecretStr("  ")},
        {"embedding_model": "  "},
        {"embedding_base_url": "https://{WorkspaceId}.example.test/compatible-mode/v1"},
    ],
)
def test_invalid_provider_configuration_returns_safe_503(
    overrides: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "embedding_base_url": "https://example.test/v1",
        "embedding_api_key": SecretStr("private-test-key"),
        **overrides,
    }
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr(document_api, "Settings", lambda: settings)
    app = create_app()
    # 错误在 provider 依赖中返回，不需要真实数据库。
    app.dependency_overrides[get_session] = lambda: None
    url = (
        f"/api/v1/workspaces/{uuid4()}/knowledge-bases/{uuid4()}"
        f"/documents/{uuid4()}/embeddings"
    )
    with TestClient(app) as client:
        response = client.post(url)
    assert response.status_code == 503
    assert response.json()["detail"] == "向量服务配置无效，请检查本机配置"
    assert "private-test-key" not in response.text
    assert "example.test" not in response.text
