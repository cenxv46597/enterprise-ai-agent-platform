"""离线审核脚本的来源身份、不可覆盖与安全错误测试。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.evaluation.answers import canonical_sha256


PUBLIC = "健康检查通过后，恢复文件导入。"
DOCUMENT_ID = "00000000-0000-0000-0000-000000000001"
CHUNK_ID = "00000000-0000-0000-0000-000000000002"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / "scripts/evaluate_answers.py"
    spec = importlib.util.spec_from_file_location("controlled_answer_evaluation_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    report_root = tmp_path / "docs/evaluation"
    report_root.mkdir(parents=True)
    corpus = tmp_path / "public.md"
    corpus.write_text(PUBLIC, encoding="utf-8")
    dataset_path, rubric_path = tmp_path / "dataset.json", tmp_path / "rubric.json"
    source_path, output_path = report_root / "source.json", report_root / "result.json"
    case = {"id": "recovery", "question": "何时恢复导入？", "expected_abstained": False}
    dataset = {"dataset_version": "controlled-v1", "cases": [case]}
    source = {
        "dataset_version": "controlled-v1", "document_id": DOCUMENT_ID, "chunk_count": 1,
        "attempts": [{**case, "response": {
            "answer": PUBLIC, "abstained": False, "citations": [{
                "chunk_id": CHUNK_ID, "document_id": DOCUMENT_ID, "document_name": "public.md",
                "chunk_index": 1, "quote": PUBLIC, "start_char": 0, "end_char": len(PUBLIC),
            }],
        }, "semantic_review": "旧 AI 备注不能自动转为人工通过"}],
    }
    rubric = {
        "version": "answer-review-v1", "source_report_canonical_sha256": canonical_sha256(source),
        "source_dataset_canonical_sha256": canonical_sha256(dataset),
        "corpus_text_sha256": hashlib.sha256(PUBLIC.encode()).hexdigest(),
        "chunking": {"version": "character-v1", "chunk_size": 500, "overlap": 50},
        "cases": [{"case_id": "recovery", "checks": [{
            "id": "healthcheck_gate", "category": "必要条件", "description": "通过才恢复", "evidence": PUBLIC,
        }]}],
    }
    for path, value in [(source_path, source), (dataset_path, dataset), (rubric_path, rubric)]:
        write_json(path, value)
    for name, value in {
        "REPOSITORY_ROOT": tmp_path, "REPORT_ROOT": report_root, "CORPUS_PATH": corpus,
        "DATASET_PATH": dataset_path, "RUBRIC_PATH": rubric_path,
        "SOURCE_REPORT": source_path, "OUTPUT_REPORT": output_path,
    }.items():
        monkeypatch.setattr(cli, name, value)
    return SimpleNamespace(
        root=tmp_path, source_path=source_path, output_path=output_path, rubric_path=rubric_path,
        dataset_path=dataset_path, corpus_path=corpus, source=source, rubric=rubric, dataset=dataset,
    )


def run(cli, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["evaluate_answers.py", *args])
    return cli.main()


def human_review(report):
    saved = deepcopy(report)
    review = saved["results"][0]["review"]
    review.update(reviewer_kind="人工", reviewer_name="受控测试审核者")
    review["checks"]["healthcheck_gate"].update(verdict="通过", reason="明确说明检查通过后才恢复。")
    # 修改输入中旧的汇总数，不允许脚本信任它。
    saved["summary"]["human_pass_cases"] = 999
    return saved


def test_plan_never_calls_database_or_model_or_writes_report(cli, bundle, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("不得连接数据库或模型")
    monkeypatch.setattr("psycopg.connect", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr("app.core.config.Settings", forbidden)
    assert run(cli, monkeypatch, "--plan") == 0
    assert not bundle.output_path.exists()
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["awaiting_human_review_cases"] == 1
    assert summary["human_review_pass_fraction"] is None


def test_write_report_preserves_source_and_ignores_legacy_ai_verdict(cli, bundle, monkeypatch):
    original = bundle.source_path.read_bytes()
    assert run(cli, monkeypatch, "--write-report") == 0
    result = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert bundle.source_path.read_bytes() == original
    assert result["results"][0]["human_pass"] is None
    assert result["new_model_calls"] == 0


def test_completed_human_review_recomputes_summary(cli, bundle, monkeypatch):
    reviewed_path = bundle.source_path.parent / "reviewed.json"
    write_json(reviewed_path, human_review(cli.build_report(bundle.source_path)))
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(reviewed_path)) == 0
    result = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert result["summary"]["human_pass_cases"] == 1
    assert result["summary"]["human_review_pass_fraction"] == 1


def test_canonical_identity_survives_git_line_endings_and_json_formatting(cli, bundle):
    first = cli.build_report(bundle.source_path)
    reformatted = json.dumps(bundle.source, ensure_ascii=False, sort_keys=True, indent=4).replace("\n", "\r\n")
    bundle.source_path.write_bytes(reformatted.encode())
    assert cli.build_report(bundle.source_path)["source_report_canonical_sha256"] == first["source_report_canonical_sha256"]


@pytest.mark.parametrize("target", ["source", "dataset", "corpus"])
def test_modified_public_inputs_are_rejected_without_echoing_content(cli, bundle, monkeypatch, capsys, target):
    marker = "PRIVATE-CONTENT-MUST-NOT-BE-REPORTED"
    if target == "source":
        bundle.source["attempts"][0]["response"]["answer"] = marker
        write_json(bundle.source_path, bundle.source)
    elif target == "dataset":
        bundle.dataset["cases"][0]["question"] = marker
        write_json(bundle.dataset_path, bundle.dataset)
    else:
        bundle.corpus_path.write_text(marker, encoding="utf-8")
    assert run(cli, monkeypatch, "--write-report") == 2
    assert not bundle.output_path.exists()
    output = capsys.readouterr().out
    assert marker not in output
    assert "source_identity" in output


@pytest.mark.parametrize("field", ["source_report_canonical_sha256", "rubric_canonical_sha256"])
def test_reviews_from_another_source_or_rubric_cannot_be_reused(cli, bundle, monkeypatch, field):
    saved = human_review(cli.build_report(bundle.source_path))
    saved[field] = "0" * 64
    path = bundle.source_path.parent / "stale.json"
    write_json(path, saved)
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(path)) == 2
    assert not bundle.output_path.exists()


def test_changed_answer_hash_in_review_is_rejected(cli, bundle, monkeypatch):
    saved = human_review(cli.build_report(bundle.source_path))
    saved["results"][0]["review"]["answer_sha256"] = "0" * 64
    path = bundle.source_path.parent / "stale.json"
    write_json(path, saved)
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(path)) == 2


@pytest.mark.parametrize("target", ["answer", "rubric"])
def test_reviewed_display_must_match_the_bound_answer_and_checks(cli, bundle, monkeypatch, target):
    saved = human_review(cli.build_report(bundle.source_path))
    if target == "answer":
        saved["results"][0]["response"]["answer"] = "看过的是另一个答案"
    else:
        saved["results"][0]["rubric_checks"][0]["description"] = "看过的是另一个标准"
    path = bundle.source_path.parent / "different-display.json"
    write_json(path, saved)
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(path)) == 2
    assert not bundle.output_path.exists()


def test_existing_output_is_never_overwritten(cli, bundle, monkeypatch):
    bundle.output_path.write_text("keep-original", encoding="utf-8")
    assert run(cli, monkeypatch, "--write-report") == 2
    assert bundle.output_path.read_text(encoding="utf-8") == "keep-original"


@pytest.mark.parametrize("argument", ["--source-report", "--reviews", "--report"])
def test_paths_outside_report_root_are_rejected(cli, bundle, monkeypatch, argument):
    with pytest.raises(SystemExit) as error:
        run(cli, monkeypatch, "--write-report", argument, str(bundle.root / "outside.json"))
    assert error.value.code == 2
    assert not (bundle.root / "outside.json").exists()


def test_output_cannot_replace_source(cli, bundle, monkeypatch):
    with pytest.raises(SystemExit):
        run(cli, monkeypatch, "--write-report", "--report", str(bundle.source_path))


def test_output_cannot_replace_review_input(cli, bundle, monkeypatch):
    reviews_path = bundle.source_path.parent / "reviews.json"
    with pytest.raises(SystemExit):
        run(cli, monkeypatch, "--write-report", "--report", str(reviews_path), "--reviews", str(reviews_path))


def test_incomplete_review_case_set_is_rejected(cli, bundle, monkeypatch):
    saved = cli.build_report(bundle.source_path)
    saved["results"] = []
    path = bundle.source_path.parent / "incomplete.json"
    write_json(path, saved)
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(path)) == 2


def test_invalid_review_errors_do_not_echo_private_reason(cli, bundle, monkeypatch, capsys):
    saved = human_review(cli.build_report(bundle.source_path))
    review = saved["results"][0]["review"]
    review["reviewer_kind"] = "PRIVATE-UNKNOWN-REVIEWER"
    path = bundle.source_path.parent / "invalid.json"
    write_json(path, saved)
    assert run(cli, monkeypatch, "--write-report", "--reviews", str(path)) == 2
    output = capsys.readouterr().out
    assert "PRIVATE-UNKNOWN-REVIEWER" not in output
    assert "ValidationError" in output


def test_published_six_case_snapshot_and_rubric_form_pending_review(cli):
    report = cli.build_report(cli.SOURCE_REPORT)
    assert report["summary"]["case_count"] == 6
    assert report["summary"]["citation_span_pass_cases"] == 4
    assert report["summary"]["citation_span_not_applicable_cases"] == 2
    assert report["summary"]["abstention_matches_expected_cases"] == 6
    assert report["summary"]["human_reviewed_cases"] == 0
    assert report["summary"]["human_review_pass_fraction"] is None
    assert sum(len(row["review"]["checks"]) for row in report["results"]) == 16
