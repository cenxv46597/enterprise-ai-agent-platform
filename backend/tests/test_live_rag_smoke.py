"""用受控配置和模拟响应验证真实联调 CLI 的数据边界。"""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import UUID

from pydantic import SecretStr
import httpx
import pytest


WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
KNOWLEDGE_BASE_ID = "00000000-0000-0000-0000-000000000002"
DOCUMENT_ID = "00000000-0000-0000-0000-000000000003"
OTHER_DOCUMENT_ID = "00000000-0000-0000-0000-000000000004"
CORPUS_TEXT = "公开演示说明：先暂停导入，再重启服务。"
PRIVATE_TEXT = "PRIVATE-CONTENT-MUST-NOT-BE-REPORTED"


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch):
    # app.main 在导入时创建应用。这里替换入口，避免导入时读取本机 .env。
    main_stub = ModuleType("app.main")
    main_stub.create_app = Mock(return_value=object())
    path = Path(__file__).resolve().parents[1] / "scripts/live_rag_smoke.py"
    spec = importlib.util.spec_from_file_location("controlled_live_rag_smoke", path)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as context:
        context.setitem(sys.modules, "app.main", main_stub)
        spec.loader.exec_module(module)
    return module


def local_settings(upload_root: Path, database_url: str = "postgresql+psycopg://test:placeholder@localhost/eaap"):
    return SimpleNamespace(
        app_env="local",
        database_url=database_url,
        upload_root=upload_root,
        embedding_model="text-embedding-v4",
        embedding_dimensions=1024,
        embedding_api_key=SecretStr("controlled-embedding-placeholder"),
        generation_model="deepseek-flash",
        generation_base_url="https://api.deepseek.com",
        generation_api_key=SecretStr("controlled-generation-placeholder"),
    )


def fixed_case(case_id: str = "public_recovery") -> dict:
    return {
        "id": case_id,
        "question": "如何恢复服务？",
        "expected_abstained": False,
        "expected_source_texts": [CORPUS_TEXT],
        "expected_answer_terms": ["暂停导入", "重启服务"],
    }


def public_search() -> dict:
    return {"results": [{
        "document_id": DOCUMENT_ID,
        "chunk_index": 1,
        "content": CORPUS_TEXT,
        "start_char": 0,
        "end_char": len(CORPUS_TEXT),
        "distance": 0.125,
    }]}


def public_answer() -> dict:
    return {
        "answer": "先暂停导入，再重启服务。",
        "abstained": False,
        "citations": [{
            "document_id": DOCUMENT_ID,
            "quote": CORPUS_TEXT,
            "start_char": 0,
            "end_char": len(CORPUS_TEXT),
        }],
    }


@pytest.fixture
def cli_context(smoke, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    corpus_path = tmp_path / "demo-product.md"
    corpus_path.write_text(CORPUS_TEXT, encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"dataset_version": "controlled-v1", "cases": [fixed_case()]}), encoding="utf-8")
    report_root = tmp_path / "reports"
    report_path = report_root / "smoke.json"
    monkeypatch.setattr(smoke, "CORPUS_PATH", corpus_path)
    monkeypatch.setattr(smoke, "CASES_PATH", cases_path)
    monkeypatch.setattr(smoke, "REPORT_ROOT", report_root)
    monkeypatch.setattr(smoke, "Settings", lambda: local_settings(tmp_path / "uploads"))
    monkeypatch.setattr(smoke, "check_local_configuration", Mock())
    monkeypatch.setattr(smoke, "ensure_demo_only", Mock())
    monkeypatch.setattr(smoke, "create_app", Mock(return_value=object()))
    monkeypatch.setattr(smoke, "TestClient", Mock(return_value=MagicMock()))

    def prepare(client, report, output_path, corpus, resume):
        report.update(
            workspace_id=WORKSPACE_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            document_id=DOCUMENT_ID,
            chunk_count=1,
            embedding={"reused": True},
        )
        smoke.save_report(output_path, report)
        return "/controlled-demo"

    monkeypatch.setattr(smoke, "prepare_demo", prepare)
    monkeypatch.setattr(sys, "argv", ["live_rag_smoke.py", "--run", "--report", str(report_path)])
    return SimpleNamespace(cases_path=cases_path, report_path=report_path)


@pytest.mark.parametrize("query", ["host=remote.example", "dbname=other_database"])
def test_database_query_cannot_override_local_target(smoke, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    monkeypatch.setattr(smoke, "REPOSITORY_ROOT", tmp_path)
    create_engine = Mock(side_effect=AssertionError("database connection must not be attempted"))
    monkeypatch.setattr(smoke, "create_engine", create_engine)
    settings = local_settings(tmp_path / "uploads", f"postgresql+psycopg://test:placeholder@localhost/eaap?{query}")

    with pytest.raises(smoke.SmokeFailure) as error:
        smoke.check_local_configuration(settings)

    assert error.value.stage == "local_configuration"
    create_engine.assert_not_called()


def test_upload_root_outside_repository_stops_before_database_connection(smoke, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "REPOSITORY_ROOT", tmp_path / "repository")
    create_engine = Mock(side_effect=AssertionError("database connection must not be attempted"))
    monkeypatch.setattr(smoke, "create_engine", create_engine)

    with pytest.raises(smoke.SmokeFailure) as error:
        smoke.check_local_configuration(local_settings(tmp_path / "outside"))

    assert error.value.stage == "local_configuration"
    create_engine.assert_not_called()


@pytest.mark.parametrize("field,changes,expected", [
    ("content", {}, True),
    ("quote", {}, True),
    ("content", {"document_id": OTHER_DOCUMENT_ID}, False),
    ("quote", {"quote": PRIVATE_TEXT}, False),
    ("content", {"start_char": -1}, False),
    ("content", {"end_char": 0}, False),
    ("content", {"end_char": len(CORPUS_TEXT) + 1}, False),
    ("content", {"start_char": True}, False),
    ("content", {"start_char": "0"}, False),
])
def test_public_span_requires_exact_document_text_and_valid_offsets(smoke, field: str, changes: dict, expected: bool) -> None:
    item = {"document_id": DOCUMENT_ID, "start_char": 0, "end_char": len(CORPUS_TEXT), field: CORPUS_TEXT, **changes}
    assert smoke.public_span_matches(item, CORPUS_TEXT, DOCUMENT_ID, field) is expected


@pytest.mark.parametrize("foreign_document", [False, True])
def test_private_search_stops_before_answers_and_preserves_completed_attempt(
    smoke, cli_context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, foreign_document: bool,
) -> None:
    cases = [fixed_case(case_id) for case_id in ("completed", "rejected", "must_not_run")]
    cli_context.cases_path.write_text(json.dumps({"dataset_version": "controlled-v1", "cases": cases}), encoding="utf-8")
    private_search = public_search()
    if foreign_document:
        private_search["results"][0]["document_id"] = OTHER_DOCUMENT_ID
    else:
        private_search["results"][0]["content"] = PRIVATE_TEXT

    def response(client, method, url, stage, **kwargs):
        if stage == "search_completed":
            return public_search(), 0.01
        if stage == "answer_completed":
            return public_answer(), 0.02
        if stage == "search_rejected":
            return private_search, 0.01
        pytest.fail("rejected search must stop answers and all later cases")

    requests = Mock(side_effect=response)
    monkeypatch.setattr(smoke, "request", requests)

    assert smoke.main() == 2

    raw_report = cli_context.report_path.read_text(encoding="utf-8")
    report = json.loads(raw_report)
    output = capsys.readouterr()
    assert [attempt["id"] for attempt in report["attempts"]] == ["completed"]
    assert report["failure"]["stage"] == "search_sources_changed"
    assert [call.args[3] for call in requests.call_args_list] == ["search_completed", "answer_completed", "search_rejected"]
    assert PRIVATE_TEXT not in raw_report + output.out + output.err


@pytest.mark.parametrize("foreign_document", [False, True])
def test_private_answer_citation_is_rejected_before_report_response_is_saved(
    smoke, cli_context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, foreign_document: bool,
) -> None:
    private_answer = public_answer()
    private_answer["answer"] = PRIVATE_TEXT
    if foreign_document:
        private_answer["citations"][0]["document_id"] = OTHER_DOCUMENT_ID
    else:
        private_answer["citations"][0]["quote"] = PRIVATE_TEXT
    requests = Mock(side_effect=[(public_search(), 0.01), (private_answer, 0.02)])
    monkeypatch.setattr(smoke, "request", requests)

    assert smoke.main() == 2

    raw_report = cli_context.report_path.read_text(encoding="utf-8")
    report = json.loads(raw_report)
    output = capsys.readouterr()
    assert report["attempts"] == []
    assert report["failure"]["stage"] == "answer_sources_changed"
    assert requests.call_count == 2
    assert PRIVATE_TEXT not in raw_report + output.out + output.err


@pytest.mark.parametrize("document_ids,allowed", [([DOCUMENT_ID], True), ([DOCUMENT_ID, OTHER_DOCUMENT_ID], False)])
def test_demo_only_guard_rejects_additional_documents_and_disposes_engine(
    smoke, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document_ids: list[str], allowed: bool,
) -> None:
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.scalars.return_value = [UUID(value) for value in document_ids]
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(smoke, "create_engine", create_engine)
    monkeypatch.setattr(smoke, "Settings", lambda: local_settings(tmp_path / "uploads"))
    report = {"knowledge_base_id": KNOWLEDGE_BASE_ID, "document_id": DOCUMENT_ID}

    if allowed:
        smoke.ensure_demo_only(report)
    else:
        with pytest.raises(smoke.SmokeFailure) as error:
            smoke.ensure_demo_only(report)
        assert error.value.stage == "demo_documents_changed"

    engine.dispose.assert_called_once_with()


def test_resume_with_only_workspace_continues_without_creating_another_workspace(
    smoke, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    corpus = CORPUS_TEXT.encode("utf-8")
    report = {"run_id": "controlled-run", "workspace_id": WORKSPACE_ID, "corpus_sha256": hashlib.sha256(corpus).hexdigest()}
    responses = {
        "resume_workspace": {"id": WORKSPACE_ID, "name": "RAG真实联调演示-controlled-run"},
        "create_knowledge_base": {"id": KNOWLEDGE_BASE_ID, "name": "虚构产品技术说明"},
        "upload_document": {"id": DOCUMENT_ID, "sha256": report["corpus_sha256"], "original_filename": "demo-product.md", "status": "parsed"},
        "persist_chunks": [{"chunk_index": 1}],
        "embed_document": {"status": "ready", "reused": False},
    }
    requests = Mock(side_effect=lambda client, method, url, stage, **kwargs: (responses[stage], 0.01))
    save_report = Mock()
    ensure_demo_only = Mock()
    monkeypatch.setattr(smoke, "request", requests)
    monkeypatch.setattr(smoke, "save_report", save_report)
    monkeypatch.setattr(smoke, "ensure_demo_only", ensure_demo_only)

    result = smoke.prepare_demo(object(), report, tmp_path / "controlled.json", corpus, resume=True)

    assert result == f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases/{KNOWLEDGE_BASE_ID}"
    assert [call.args[3] for call in requests.call_args_list] == [
        "resume_workspace", "create_knowledge_base", "upload_document", "persist_chunks", "embed_document",
    ]
    assert requests.call_args_list[0].args[1:3] == ("GET", f"/api/v1/workspaces/{WORKSPACE_ID}")
    assert requests.call_args_list[1].args[1:3] == ("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases")
    assert report["workspace_id"] == WORKSPACE_ID and report["document_id"] == DOCUMENT_ID
    ensure_demo_only.assert_called_once_with(report)
    assert save_report.call_count == 4


def test_report_write_failure_returns_safe_json_without_original_exception(
    smoke, cli_context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    save_report = Mock(side_effect=OSError(PRIVATE_TEXT))
    monkeypatch.setattr(smoke, "save_report", save_report)

    assert smoke.main() == 2

    output = capsys.readouterr()
    assert json.loads(output.out) == {"stage": "failed", "error_type": "OSError", "report_write_failed": True}
    assert save_report.call_count == 2
    assert PRIVATE_TEXT not in output.out + output.err


def test_public_success_keeps_semantic_review_pending_even_with_all_term_hits(
    smoke, cli_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke, "request", Mock(side_effect=[(public_search(), 0.01), (public_answer(), 0.02)]))

    assert smoke.main() == 0

    report = json.loads(cli_context.report_path.read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert attempt["citation_spans_valid"] and attempt["abstention_matches_expected"]
    assert all(hint["present"] for hint in attempt["answer_term_hints"])
    assert attempt["semantic_review"] == "待人工核对；词语命中和引文存在不代表答案忠实"
    assert "不是检索质量基准" in report["scope"]


@pytest.mark.parametrize("abstained,has_snapshot", [(False, True), (True, True), (True, False)])
def test_capture_attaches_observed_body_without_inventing_requests(smoke, monkeypatch, abstained, has_snapshot):
    answer = public_answer() if not abstained else {"answer": "没有资料", "abstained": True, "citations": []}
    monkeypatch.setattr(smoke, "request", Mock(side_effect=[(public_search(), 0.01), (answer, 0.02)]))
    snapshots = [{"capture_stage": "请求发送前"}] if has_snapshot else []
    row = smoke.evaluate_case(object(), "/demo", fixed_case(), CORPUS_TEXT, DOCUMENT_ID, generation_snapshots=snapshots)
    if has_snapshot:
        assert row["generation_request_snapshot"]["answer_response_received"] is True
    else:
        assert row["generation_request_snapshot"] is None
        assert row["generation_capture_state"] == "未观察到生成请求"


def test_answer_without_observed_request_fails_capture(smoke, monkeypatch):
    monkeypatch.setattr(smoke, "request", Mock(side_effect=[(public_search(), 0.01), (public_answer(), 0.02)]))
    with pytest.raises(smoke.SmokeFailure, match="generation_snapshot_count"):
        smoke.evaluate_case(object(), "/demo", fixed_case(), CORPUS_TEXT, DOCUMENT_ID, generation_snapshots=[])


def test_capture_cannot_backfill_old_report(smoke, cli_context, monkeypatch):
    old = {
        "corpus_sha256": hashlib.sha256(CORPUS_TEXT.encode()).hexdigest(),
        "dataset_version": "controlled-v1", "attempts": [],
    }
    cli_context.report_path.parent.mkdir()
    cli_context.report_path.write_text(json.dumps(old), encoding="utf-8")
    original = cli_context.report_path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["smoke", "--run", "--resume", "--capture-generation", "--report", str(cli_context.report_path)])
    assert smoke.main() == 2
    assert cli_context.report_path.read_bytes() == original
    smoke.create_app.assert_not_called()


@pytest.mark.parametrize("write_fails", [False, True])
def test_opt_in_dependency_saves_public_body_before_transport(smoke, tmp_path, monkeypatch, write_fails):
    from app.api import answers as answer_api
    from app.core.config import Settings
    import app.providers.compatible_chat as chat
    from app.providers.generation import GenerationError, GenerationSource

    report, snapshots, calls = {}, [], []
    output = tmp_path / "snapshot.json"
    app = SimpleNamespace(dependency_overrides={})
    settings = Settings(
        _env_file=None, generation_base_url="https://generation.example/v1",
        generation_api_key=SecretStr("controlled-secret"), generation_model="controlled-model",
    )
    monkeypatch.setattr(answer_api, "Settings", lambda: settings)

    def handler(request):
        calls.append(request)
        saved = json.loads(output.read_text(encoding="utf-8"))
        snapshot = saved["prepared_generation_requests"][0]["snapshot"]
        assert snapshot["request_body"] == json.loads(request.content)
        assert "answer_response_received" not in snapshot
        assert "controlled-secret" not in output.read_text(encoding="utf-8")
        return httpx.Response(200, json={"model": "controlled-model", "choices": [{
            "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({
                "answer": "先暂停导入，再重启服务。", "abstained": False,
                "citations": [{"source_id": 1, "quote": CORPUS_TEXT}],
            })},
        }]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(chat.httpx, "Client", lambda **kwargs: client)
    if write_fails:
        monkeypatch.setattr(smoke, "save_report", Mock(side_effect=OSError(PRIVATE_TEXT)))
    smoke.install_generation_capture(app, report, output, CORPUS_TEXT, fixed_case(), snapshots)
    provider = app.dependency_overrides[answer_api.get_generation_provider]()
    if write_fails:
        with pytest.raises(GenerationError, match="快照保存失败"):
            provider.generate_answer(fixed_case()["question"], [GenerationSource(1, CORPUS_TEXT)])
        assert calls == []
    else:
        assert provider.generate_answer(fixed_case()["question"], [GenerationSource(1, CORPUS_TEXT)]).abstained is False
        assert len(calls) == len(snapshots) == 1
    assert client.is_closed
