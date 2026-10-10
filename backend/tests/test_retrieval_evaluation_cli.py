"""只用公开合成语料及 Mock 检查检索评测 CLI，不读取本机配置。"""

from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import UUID

from pydantic import SecretStr
import pytest

from app.evaluation.retrieval import prepare_cases


WORKSPACE_ID = "00000000-0000-0000-0000-000000000011"
KNOWLEDGE_BASE_ID = "00000000-0000-0000-0000-000000000012"
DOCUMENT_A = "00000000-0000-0000-0000-000000000013"
DOCUMENT_B = "00000000-0000-0000-0000-000000000014"
OTHER_DOCUMENT = "00000000-0000-0000-0000-000000000015"
GOLD = "恢复时先暂停导入，再重启服务。"
PRIVATE = "PRIVATE-RAW-CONTENT-MUST-NOT-BE-REPORTED"


def case_row(case_id="positive", *, answerable=True):
    return {
        "id": case_id, "question": "如何恢复服务？", "answerable": answerable,
        "required_evidence": [{"document_name": "a.md", "text": GOLD}] if answerable else [],
    }


def settings_for(root: Path):
    # 故意不提供任何 generation 字段，检索评测只需要 embedding。
    return SimpleNamespace(
        app_env="local", database_url="postgresql+psycopg://test:placeholder@localhost/eaap",
        upload_root=root / "uploads", embedding_base_url="https://embedding.example.test/v1",
        embedding_api_key=SecretStr("controlled-placeholder"), embedding_model="text-embedding-v4",
        embedding_dimensions=1024, embedding_batch_size=10,
        embedding_timeout_seconds=30, embedding_total_timeout_seconds=120,
    )


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("controlled_retrieval_cli", scripts / "evaluate_retrieval.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    # 即使某个测试误走其他分支，也不能创建真实客户端或读取 Settings。
    for name in ("Settings", "create_engine", "TestClient", "create_app", "request"):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError("unexpected external operation")))
    return module


@pytest.fixture
def dataset_context(cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    knowledge = tmp_path / "examples/knowledge"
    knowledge.mkdir(parents=True)
    text_a = GOLD + "\n" + "公开演示干扰资料。" * 500
    text_b = "公开相邻主题说明。" * 500
    (knowledge / "a.md").write_bytes(text_a.replace("\n", "\r\n").encode("utf-8"))
    (knowledge / "b.md").write_bytes(text_b.encode("utf-8"))
    dataset = {
        "dataset_version": "controlled-retrieval-v1",
        "documents": [
            {"path": "examples/knowledge/a.md", "document_name": "a.md"},
            {"path": "examples/knowledge/b.md", "document_name": "b.md"},
        ],
        "cases": [case_row(), case_row("negative", answerable=False)],
    }
    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cli, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(cli, "KNOWLEDGE_ROOT", knowledge)
    monkeypatch.setattr(cli, "DATASET_PATH", dataset_path)
    return SimpleNamespace(root=tmp_path, knowledge=knowledge, path=dataset_path, dataset=dataset, text_a=text_a)


@pytest.fixture
def run_context(cli, dataset_context, monkeypatch: pytest.MonkeyPatch):
    dataset, fixtures, identity = cli.load_dataset(dataset_context.path)
    sources = {
        document_id: {"name": fixture.name, "text": fixture.content}
        for document_id, fixture in zip((DOCUMENT_A, DOCUMENT_B), fixtures, strict=True)
    }
    report_root = dataset_context.root / "reports"
    report_path = report_root / "retrieval.json"
    settings = settings_for(dataset_context.root)
    monkeypatch.setattr(cli, "REPORT_ROOT", report_root)
    monkeypatch.setattr(cli, "Settings", Mock(return_value=settings))
    monkeypatch.setattr(cli, "check_local_configuration", Mock())
    monkeypatch.setattr(cli, "embedding_profile_digest", Mock(return_value="controlled-profile-digest"))
    monkeypatch.setattr(cli, "create_app", Mock(return_value=object()))
    monkeypatch.setattr(cli, "TestClient", Mock(return_value=MagicMock()))
    monkeypatch.setattr(cli, "verify_document_set", Mock())
    monkeypatch.setattr(cli, "validate_resume", Mock())

    def prepare(client, supplied_settings, report, supplied_fixtures, path):
        report.update(workspace_id=WORKSPACE_ID, knowledge_base_id=KNOWLEDGE_BASE_ID)
        report["documents"] = {
            fixture.name: {"document_id": document_id, "sha256": fixture.sha256, "chunk_count": fixture.chunk_count}
            for document_id, fixture in zip((DOCUMENT_A, DOCUMENT_B), fixtures, strict=True)
        }
        cli.save_report(path, report)
        return sources

    monkeypatch.setattr(cli, "prepare_corpus", Mock(side_effect=prepare))
    monkeypatch.setattr(sys, "argv", ["evaluate_retrieval.py", "--run", "--report", str(report_path)])
    return SimpleNamespace(
        report_path=report_path, dataset=dataset, fixtures=fixtures, identity=identity,
        sources=sources, settings=settings, dataset_context=dataset_context,
    )


def public_hit(sources, document_id=DOCUMENT_A, *, evidence=False):
    source = sources[document_id]
    start = source["text"].index(GOLD) if evidence else 0
    end = start + len(GOLD) if evidence else min(16, len(source["text"]))
    return {
        "document_id": document_id, "document_name": source["name"], "chunk_index": 1,
        "content": source["text"][start:end], "start_char": start, "end_char": end, "distance": 0.125,
    }


def test_plan_is_local_and_never_reads_settings_or_creates_clients(cli, run_context, monkeypatch, capsys):
    settings = Mock(side_effect=AssertionError("plan must not read Settings"))
    monkeypatch.setattr(cli, "Settings", settings)
    monkeypatch.setattr(sys, "argv", ["evaluate_retrieval.py", "--plan", "--report", str(run_context.report_path)])

    assert cli.main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["stage"] == "plan" and 20 <= output["candidate_count"] <= 50
    assert output["generation_called"] is False and output["case_count"] == 2
    for method in (settings, cli.create_engine, cli.create_app, cli.TestClient, cli.request, cli.check_local_configuration):
        method.assert_not_called()
    assert not run_context.report_path.exists()


def test_configuration_guard_allows_missing_generation_fields_when_not_required(cli, dataset_context, monkeypatch):
    helper = sys.modules[cli.check_local_configuration.__module__]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.scalar.side_effect = ["20261010_0004", True]
    monkeypatch.setattr(helper, "REPOSITORY_ROOT", dataset_context.root)
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(helper, "create_engine", create_engine)

    cli.check_local_configuration(settings_for(dataset_context.root), require_generation=False)

    create_engine.assert_called_once()
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("invalid", ["absolute", "outside", "wrong_name", "duplicate_name"])
def test_fixture_paths_and_duplicate_names_are_rejected(cli, dataset_context, invalid):
    dataset = dataset_context.dataset
    if invalid == "absolute":
        dataset["documents"][1]["path"] = str(dataset_context.knowledge / "b.md")
    elif invalid == "outside":
        dataset["documents"][1] = {"path": "../private.md", "document_name": "private.md"}
    elif invalid == "wrong_name":
        dataset["documents"][1]["document_name"] = "wrong.md"
    else:
        dataset["documents"][1] = dict(dataset["documents"][0])
    dataset_context.path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(cli.SmokeFailure) as error:
        cli.load_dataset(dataset_context.path)

    assert error.value.stage == "dataset_document_path"
    cli.Settings.assert_not_called()
    cli.create_engine.assert_not_called()


@pytest.mark.parametrize("large", [False, True])
def test_candidate_pool_must_fit_fixed_evaluation_budget(cli, dataset_context, large):
    text = GOLD + ("公开说明。" * 5000 if large else "简短公开说明。")
    for name in ("a.md", "b.md"):
        (dataset_context.knowledge / name).write_bytes(text.encode("utf-8"))

    with pytest.raises(cli.SmokeFailure) as error:
        cli.load_dataset(dataset_context.path)

    assert error.value.stage == "candidate_count"


def test_fixture_identity_hashes_original_bytes_and_normalizes_public_newlines(cli, dataset_context):
    dataset, fixtures, identity = cli.load_dataset(dataset_context.path)
    fixture = fixtures[0]
    assert fixture.content == dataset_context.text_a and "\r" not in fixture.content
    assert fixture.sha256 == hashlib.sha256(fixture.data).hexdigest()
    assert identity["dataset_sha256"] == hashlib.sha256(dataset_context.path.read_bytes()).hexdigest()
    assert identity["corpus_sha256"]["a.md"] == fixture.sha256
    assert dataset["dataset_version"] == identity["dataset_version"]


@pytest.mark.parametrize("invalid", ["outside_report_root", "wrong_extension", "plan_resume"])
def test_report_target_and_mode_guard_stops_before_configuration(cli, run_context, monkeypatch, invalid):
    target = run_context.report_path
    arguments = ["--run"]
    if invalid == "outside_report_root":
        target = run_context.dataset_context.root / "outside.json"
    elif invalid == "wrong_extension":
        target = target.with_suffix(".md")
    else:
        arguments = ["--plan", "--resume"]
    monkeypatch.setattr(sys, "argv", ["evaluate_retrieval.py", *arguments, "--report", str(target)])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    cli.Settings.assert_not_called()
    cli.create_engine.assert_not_called()
    assert not target.exists()


@pytest.mark.parametrize("failure_stage", ["resume_identity", "resume_scope"])
def test_resume_validation_failure_preserves_original_report(cli, run_context, monkeypatch, capsys, failure_stage):
    identity = {**run_context.identity, "embedding_profile_sha256": "controlled-profile-digest"}
    if failure_stage == "resume_identity":
        identity["dataset_sha256"] = "changed-dataset"
    report = {"identity": identity, "run_id": "controlled", "documents": {}, "attempts": [{"case_id": "old-public-result"}]}
    run_context.report_path.parent.mkdir()
    original = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    run_context.report_path.write_text(original, encoding="utf-8")
    if failure_stage == "resume_scope":
        monkeypatch.setattr(cli, "validate_resume", Mock(side_effect=cli.SmokeFailure("resume_scope")))
    monkeypatch.setattr(sys, "argv", ["evaluate_retrieval.py", "--run", "--resume", "--report", str(run_context.report_path)])

    assert cli.main() == 2

    assert run_context.report_path.read_text(encoding="utf-8") == original
    assert json.loads(capsys.readouterr().out)["stage"] == failure_stage
    cli.prepare_corpus.assert_not_called()


def test_partial_resume_validates_existing_documents_then_uploads_only_missing_fixture(cli, dataset_context, monkeypatch, tmp_path):
    _, fixtures, _ = cli.load_dataset(dataset_context.path)
    settings = settings_for(dataset_context.root)
    report = {
        "run_id": "partial", "workspace_id": WORKSPACE_ID, "knowledge_base_id": KNOWLEDGE_BASE_ID,
        "documents": {"a.md": {"document_id": DOCUMENT_A, "sha256": fixtures[0].sha256}},
    }
    events = []

    def verify(supplied_settings, supplied_report):
        events.append(("verify", tuple(sorted(supplied_report["documents"]))))

    def response(client, method, url, stage, **kwargs):
        events.append((stage, method))
        if stage == "resume_workspace":
            return {"id": WORKSPACE_ID, "name": "检索评测演示-partial"}, 0.01
        if stage == "resume_knowledge_base":
            return {"name": cli.BASE_NAME, "workspace_id": WORKSPACE_ID}, 0.01
        if stage == "upload_document":
            assert kwargs["files"]["file"][0] == "b.md"
            document_id, fixture = DOCUMENT_B, fixtures[1]
        else:
            document_id = url.rsplit("/", 1)[-1] if stage == "resume_document" else url.split("/documents/")[1].split("/")[0]
            fixture = fixtures[0] if document_id == DOCUMENT_A else fixtures[1]
        if stage in {"upload_document", "resume_document"}:
            return {"id": document_id, "original_filename": fixture.name, "sha256": fixture.sha256, "knowledge_base_id": KNOWLEDGE_BASE_ID, "status": "ready"}, 0.01
        if stage == "persist_chunks":
            return [asdict(chunk) | {"document_id": document_id} for chunk in cli.split_text(UUID(document_id), fixture.content, chunk_size=500, overlap=50)], 0.01
        if stage == "embed_document":
            return {"status": "ready", "chunk_count": fixture.chunk_count, "reused": document_id == DOCUMENT_A}, 0.01
        pytest.fail("partial resume must not create another workspace or knowledge base")

    monkeypatch.setattr(cli, "verify_document_set", Mock(side_effect=verify))
    monkeypatch.setattr(cli, "request", Mock(side_effect=response))
    monkeypatch.setattr(cli, "save_report", Mock())
    cli.validate_resume(object(), settings, report, fixtures)
    sources = cli.prepare_corpus(object(), settings, report, fixtures, tmp_path / "unused.json")

    upload_position = next(index for index, event in enumerate(events) if event[0] == "upload_document")
    assert ("verify", ("a.md",)) in events[:upload_position]
    assert [call.args[3] for call in cli.request.call_args_list].count("upload_document") == 1
    assert set(report["documents"]) == {"a.md", "b.md"} and set(sources) == {DOCUMENT_A, DOCUMENT_B}
    assert report["documents"]["a.md"]["embedding"]["reused"] is True


@pytest.mark.parametrize("actual", [[], [DOCUMENT_A, OTHER_DOCUMENT]])
def test_changed_document_set_is_rejected_and_engine_disposed(cli, dataset_context, monkeypatch, actual):
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.scalars.return_value = [UUID(value) for value in actual]
    monkeypatch.setattr(cli, "create_engine", Mock(return_value=engine))
    report = {"knowledge_base_id": KNOWLEDGE_BASE_ID, "documents": {"a.md": {"document_id": DOCUMENT_A}}}

    with pytest.raises(cli.SmokeFailure) as error:
        cli.verify_document_set(settings_for(dataset_context.root), report)

    assert error.value.stage == "evaluation_documents_changed"
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("changed", ["unknown_document", "private_content"])
def test_unverified_sixth_hit_is_never_saved_and_keeps_previous_success(cli, run_context, monkeypatch, capsys, changed):
    dataset = run_context.dataset_context.dataset
    dataset["cases"] = [case_row(case_id) for case_id in ("completed", "rejected", "must_not_run")]
    run_context.dataset_context.path.write_text(json.dumps(dataset), encoding="utf-8")
    distractor = public_hit(run_context.sources, DOCUMENT_B)
    bad = public_hit(run_context.sources, evidence=True)
    if changed == "unknown_document":
        bad.update(document_id=OTHER_DOCUMENT, document_name=PRIVATE)
    else:
        bad["content"] = PRIVATE

    def response(client, method, url, stage, **kwargs):
        assert url.endswith("/search") and kwargs["json"]["top_k"] == 10
        if stage == "search_completed":
            return {"results": [public_hit(run_context.sources, evidence=True)]}, 0.1
        if stage == "search_rejected":
            return {"results": [distractor] * 5 + [bad] + [distractor] * 4}, 0.2
        pytest.fail("failed case must stop later requests")

    monkeypatch.setattr(cli, "request", Mock(side_effect=response))
    assert cli.main() == 2

    raw = run_context.report_path.read_text(encoding="utf-8")
    report = json.loads(raw)
    output = capsys.readouterr()
    assert [row["case_id"] for row in report["attempts"]] == ["completed"]
    assert report["failure"]["error_type"] == "RetrievalEvaluationError"
    assert report["summary"]["completed_unique_cases"] == 1
    assert cli.request.call_count == 2
    assert PRIVATE not in raw + output.out + output.err
    cli.check_local_configuration.assert_called_once_with(run_context.settings, require_generation=False)


def test_top_five_uses_prefix_of_single_top_ten_response(cli, run_context, monkeypatch):
    case = prepare_cases([case_row()], run_context.sources)[0]
    distractor = public_hit(run_context.sources, DOCUMENT_B)
    hits = [distractor] * 5 + [public_hit(run_context.sources, evidence=True)] + [distractor] * 4
    request = Mock(return_value=({"results": hits}, 0.42))
    monkeypatch.setattr(cli, "request", request)

    result = cli.run_case(object(), "/controlled", case, run_context.sources)

    request.assert_called_once()
    assert request.call_args.kwargs["json"] == {"query": case.question, "top_k": 10}
    assert result["search_seconds"] == 0.42 and result["request_top_k"] == 10
    at_five, at_ten = result["evaluations"]
    assert (at_five["returned_count"], at_five["covered_evidence_count"]) == (5, 0)
    assert (at_ten["returned_count"], at_ten["covered_evidence_count"]) == (10, 1)
    assert at_ten["evidence"][0]["first_complete_rank"] == 6
    assert result["generation_called"] is False


def test_negative_case_scores_stay_null_and_do_not_claim_abstention(cli, run_context, monkeypatch):
    case = prepare_cases([case_row("negative", answerable=False)], run_context.sources)[0]
    monkeypatch.setattr(cli, "request", Mock(return_value=({"results": [public_hit(run_context.sources, DOCUMENT_B)]}, 0.1)))

    result = cli.run_case(object(), "/controlled", case, run_context.sources)

    for evaluation in result["evaluations"]:
        assert evaluation["expected_evidence_count"] == evaluation["covered_evidence_count"] == 0
        assert evaluation["all_evidence_found"] is None and evaluation["evidence_hit_fraction"] is None
    assert result["generation_called"] is False
    assert "不评价弃答或忠实性" in result["answer_review"]


def test_summary_uses_latest_attempt_once_and_excludes_negative_scores(cli, run_context):
    cases = prepare_cases(run_context.dataset["cases"], run_context.sources)

    def attempt(case, hits):
        return {"case_id": case.case_id, "answerable": case.answerable, "evaluations": [cli.evaluate_case(case, hits, run_context.sources, top_k=k).to_dict() for k in (5, 10)]}

    positive, negative = cases
    earlier = attempt(positive, [])
    latest = attempt(positive, [public_hit(run_context.sources, evidence=True)])
    summary = cli.summarize([earlier, latest, attempt(negative, [])], run_context.dataset)

    assert summary["dataset_complete"] is True and summary["completed_unique_cases"] == 2
    assert summary["negative_cases_excluded_from_evidence_score"] == 1
    for result in summary["top_k"].values():
        assert result["completed_answerable_cases"] == result["required_evidence_count"] == 1
        assert result["covered_evidence_count"] == result["all_required_evidence_cases"] == 1
        assert result["evidence_hit_fraction"] == 1
    assert "不是完整相关性标注的 Recall@K" in summary["metric_scope"]


def test_failed_report_write_emits_safe_error_without_raw_details(cli, run_context, monkeypatch, capsys):
    save_report = Mock(side_effect=OSError(PRIVATE))
    monkeypatch.setattr(cli, "save_report", save_report)

    assert cli.main() == 2

    output = capsys.readouterr()
    assert json.loads(output.out) == {"stage": "failed", "error_type": "OSError", "report_write_failed": True}
    assert save_report.call_count == 2 and PRIVATE not in output.out + output.err
