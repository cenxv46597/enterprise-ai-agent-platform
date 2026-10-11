"""离线脚本必须核对快照身份，且不得修改原始检索结果或输出原始异常。"""

import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


PRIVATE = "PRIVATE-CONTENT-MUST-NOT-BE-REPORTED"


@pytest.fixture
def cli(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("controlled_context_cli", scripts / "evaluate_context.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    source_bytes = module.SOURCE_REPORT.read_bytes()
    reports = tmp_path / "docs/evaluation"
    reports.mkdir(parents=True)
    source = reports / "retrieval.json"
    source.write_bytes(source_bytes)
    monkeypatch.setattr(module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(module, "REPORT_ROOT", reports)
    monkeypatch.setattr(module, "SOURCE_REPORT", source)
    monkeypatch.setattr(module, "OUTPUT_REPORT", reports / "context.json")
    # 公共 loader 只读固定公开文件；一旦误调用外部依赖立刻失败。
    helper = sys.modules["evaluate_retrieval"]
    for name in ("Settings", "create_engine", "TestClient", "create_app", "request"):
        monkeypatch.setattr(helper, name, Mock(side_effect=AssertionError("unexpected external operation")))
    return module


def arguments(cli, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["evaluate_context.py", *args])


def test_plan_validates_snapshot_without_writing_or_calling_external_services(cli, monkeypatch, capsys):
    original = cli.SOURCE_REPORT.read_bytes()
    arguments(cli, monkeypatch, "--plan")
    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_count"] == 36 and output["case_count"] == 6
    assert output["new_model_calls"] == 0 and output["generation_called"] is False
    assert not cli.OUTPUT_REPORT.exists() and cli.SOURCE_REPORT.read_bytes() == original
    helper = sys.modules["evaluate_retrieval"]
    for name in ("Settings", "create_engine", "TestClient", "create_app", "request"):
        getattr(helper, name).assert_not_called()


def test_written_report_excludes_negative_scores_and_keeps_snapshot_unchanged(cli, monkeypatch, capsys):
    original = cli.SOURCE_REPORT.read_bytes()
    arguments(cli, monkeypatch, "--write-report")
    assert cli.main() == 0
    assert cli.SOURCE_REPORT.read_bytes() == original
    report = json.loads(cli.OUTPUT_REPORT.read_text(encoding="utf-8"))
    assert len(report["results"]) == 36 and len(report["summary"]) == 6
    for row in report["summary"]:
        assert row["answerable_cases"] == 4 and row["required_evidence_count"] == 5
        assert row["negative_cases_excluded_from_evidence_score"] == 2
        if row["max_context_characters"] == 12000:
            assert row["context_covered_evidence_count"] == 5 and row["lost_evidence_count"] == 0
    for row in report["results"]:
        if not row["context_evidence"]["answerable"]:
            assert row["context_evidence"]["evidence_hit_fraction"] is None
    assert report["new_model_calls"] == 0 and json.loads(capsys.readouterr().out)["stage"] == "complete"


@pytest.mark.parametrize("change", ["identity", "documents", "incomplete", "stale_score", "private_hit"])
def test_changed_snapshot_is_rejected_without_output_or_raw_details(cli, monkeypatch, capsys, change):
    report = json.loads(cli.SOURCE_REPORT.read_text(encoding="utf-8"))
    if change == "identity":
        report["identity"]["dataset_sha256"] = "changed"
    elif change == "documents":
        report["documents"].pop(next(iter(report["documents"])))
    elif change == "incomplete":
        report["attempts"].pop()
    elif change == "stale_score":
        report["attempts"][0]["evaluations"][0]["covered_evidence_count"] = 0
    else:
        report["attempts"][0]["results"][-1]["content"] = PRIVATE
    cli.SOURCE_REPORT.write_text(json.dumps(report), encoding="utf-8")
    original = cli.SOURCE_REPORT.read_bytes()
    arguments(cli, monkeypatch, "--write-report")
    assert cli.main() == 2
    output = capsys.readouterr()
    assert not cli.OUTPUT_REPORT.exists() and cli.SOURCE_REPORT.read_bytes() == original
    assert PRIVATE not in output.out + output.err


@pytest.mark.parametrize("invalid", ["outside", "same_source", "zero_budget", "too_large"])
def test_invalid_paths_or_budgets_stop_before_loading_snapshot(cli, monkeypatch, invalid):
    options = {
        "outside": ["--report", str(cli.REPOSITORY_ROOT / "outside.json")],
        "same_source": ["--report", str(cli.SOURCE_REPORT)],
        "zero_budget": ["--budgets", "0"],
        "too_large": ["--budgets", "50001"],
    }
    loader = Mock(side_effect=AssertionError("must stop before loading"))
    monkeypatch.setattr(cli, "load_snapshot", loader)
    arguments(cli, monkeypatch, "--write-report", *options[invalid])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    loader.assert_not_called()


def test_existing_output_is_preserved(cli, monkeypatch, capsys):
    cli.OUTPUT_REPORT.write_text("existing report", encoding="utf-8")
    arguments(cli, monkeypatch, "--write-report")
    assert cli.main() == 2
    assert cli.OUTPUT_REPORT.read_text(encoding="utf-8") == "existing report"
    assert json.loads(capsys.readouterr().out)["reason"] == "output_exists_use_new_path"


def test_repeated_budget_options_do_not_duplicate_experiments(cli, monkeypatch, capsys):
    arguments(cli, monkeypatch, "--plan", "--budgets", "600", "600", "12000", "--top-k", "5", "5")
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["evaluation_count"] == 12


def test_snapshot_identity_is_portable_across_git_newline_normalization(cli):
    first = cli.load_snapshot(cli.SOURCE_REPORT)
    report = json.loads(cli.SOURCE_REPORT.read_text(encoding="utf-8"))
    cli.SOURCE_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=4), encoding="utf-8", newline="\n")
    second = cli.load_snapshot(cli.SOURCE_REPORT)
    assert first.sha256 == second.sha256
    assert first.hits_by_case == second.hits_by_case


def test_failed_write_does_not_print_exception_body(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "save_report", Mock(side_effect=OSError(PRIVATE)))
    arguments(cli, monkeypatch, "--write-report")
    assert cli.main() == 2
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert json.loads(output.out) == {"stage": "failed", "error_type": "OSError"}
