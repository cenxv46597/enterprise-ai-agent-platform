"""六题提示词回归的预算、快照缺口和验收清单边界。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.providers.generation_snapshot import capture_generation_request, digest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def regression_cli():
    script = REPOSITORY_ROOT / "backend/scripts/compare_prompts.py"
    spec = importlib.util.spec_from_file_location("six_case_prompt_comparison_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def regression_bundle(regression_cli, tmp_path, monkeypatch):
    example_root, report_root = tmp_path / "examples", tmp_path / "reports"
    example_root.mkdir()
    report_root.mkdir()
    dataset = json.loads((REPOSITORY_ROOT / "examples/evaluation/rag-smoke-cases.json").read_text(encoding="utf-8"))
    rubric = json.loads((REPOSITORY_ROOT / "examples/evaluation/answer-review-rubric.json").read_text(encoding="utf-8"))
    public_text = (REPOSITORY_ROOT / "examples/knowledge/demo-product.md").read_text(encoding="utf-8-sig")
    corpus_path = example_root / "public.md"
    corpus_path.write_text(public_text, encoding="utf-8")
    captured = []
    for case in dataset["cases"]:
        body = {
            "model": "controlled-model", "stream": False,
            "response_format": {"type": "json_object"}, "max_tokens": 500,
            "messages": [
                {"role": "system", "content": "只根据公开资料回答，返回规定的 JSON。"},
                {"role": "user", "content": json.dumps({
                    "question": case["question"],
                    "sources": [{"source_id": 1, "content": public_text}],
                }, ensure_ascii=False)},
            ],
        }
        captured.append({
            "case_id": case["id"],
            "snapshot": capture_generation_request(body, public_text, case["question"]),
        })
    source = {
        "generation_capture_version": regression_cli.SNAPSHOT_VERSION,
        "dataset_version": dataset["dataset_version"], "controlled_fixture": True,
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "prepared_generation_requests": captured,
    }
    checklist = {
        "version": "prompt-regression-checklist-v1",
        "dataset_canonical_sha256": digest(dataset),
        "public_text_sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
        "rubric_canonical_sha256": digest(rubric),
        "cases": [
            {"case_id": case["case_id"], "critical_check_ids": [check["id"] for check in case["checks"]]}
            for case in rubric["cases"]
        ],
    }
    source_path, dataset_path = example_root / "source.json", example_root / "cases.json"
    rubric_path, checklist_path = example_root / "rubric.json", example_root / "checklist.json"
    for path, data in ((source_path, source), (dataset_path, dataset), (rubric_path, rubric), (checklist_path, checklist)):
        write_json(path, data)
    for name, value in {
        "REPORT_ROOT": report_root, "EXAMPLE_ROOT": example_root,
        "SOURCE_REPORT": source_path, "DATASET_PATH": dataset_path, "CORPUS_PATH": corpus_path,
        "RUBRIC_PATH": rubric_path, "REGRESSION_CHECKLIST_PATH": checklist_path,
    }.items():
        monkeypatch.setattr(regression_cli, name, value)
    return SimpleNamespace(
        source=source, dataset=dataset, rubric=rubric, checklist=checklist,
        source_path=source_path, dataset_path=dataset_path, corpus_path=corpus_path,
        rubric_path=rubric_path, checklist_path=checklist_path,
        output_path=report_root / "comparison.json", report_root=report_root,
    )


def run(module, monkeypatch, bundle, *arguments):
    monkeypatch.setattr(sys, "argv", [
        "compare_prompts.py", "--source-report", str(bundle.source_path),
        "--report", str(bundle.output_path), *arguments,
    ])
    return module.main()


def forbid_configuration(module, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("六题预检和模拟不得读取凭据或发送 HTTP 请求")
    monkeypatch.setattr(module, "configured_clients", forbidden)
    monkeypatch.setattr("app.core.config.Settings", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)


def test_all_cases_requires_explicit_twelve_request_budget(regression_cli, regression_bundle, monkeypatch, capsys):
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--plan") == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "call_budget"
    assert not regression_bundle.output_path.exists()
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--plan") == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["planned_requests"] == 12
    assert plan["case_ids"] == plan["available_case_ids"] == [case["id"] for case in regression_bundle.dataset["cases"]]
    assert plan["missing_case_ids"] == []
    assert plan["ready_for_generation"] is True
    assert plan["new_model_calls"] == 0
    assert plan["quality_pass_fraction"] is None
    assert not regression_bundle.output_path.exists()


def test_all_case_plan_keeps_selected_budget_and_exposes_missing_snapshots(regression_cli, regression_bundle, monkeypatch, capsys):
    regression_bundle.source["prepared_generation_requests"] = regression_bundle.source["prepared_generation_requests"][:1]
    write_json(regression_bundle.source_path, regression_bundle.source)
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--plan") == 0
    plan = json.loads(capsys.readouterr().out)
    ids = [case["id"] for case in regression_bundle.dataset["cases"]]
    assert plan["case_ids"] == ids
    assert plan["available_case_ids"] == ids[:1]
    assert plan["missing_case_ids"] == ids[1:]
    assert plan["planned_requests"] == 12
    assert plan["ready_for_generation"] is False
    assert plan["new_model_calls"] == 0
    assert not regression_bundle.output_path.exists()


def test_plan_with_no_snapshots_reports_all_six_missing(regression_cli, regression_bundle, monkeypatch, capsys):
    regression_bundle.source["prepared_generation_requests"] = []
    write_json(regression_bundle.source_path, regression_bundle.source)
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--plan") == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["available_case_ids"] == []
    assert plan["missing_case_ids"] == [case["id"] for case in regression_bundle.dataset["cases"]]
    assert plan["planned_requests"] == 12
    assert plan["ready_for_generation"] is False


@pytest.mark.parametrize("mode", ["--mock", "--run"])
def test_incomplete_snapshots_stop_before_configuration_and_output(regression_cli, regression_bundle, monkeypatch, mode, capsys):
    regression_bundle.source["controlled_fixture"] = False
    regression_bundle.source["execution"] = "TestClient 调用真实路由、本机数据库与供应商 API"
    regression_bundle.source["prepared_generation_requests"].pop()
    write_json(regression_bundle.source_path, regression_bundle.source)
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", mode) == 2
    assert json.loads(capsys.readouterr().out)["stage"] == "failed"
    assert not regression_bundle.output_path.exists()


def test_missing_is_allowed_for_plan_but_duplicate_snapshot_is_ambiguous(regression_cli, regression_bundle, monkeypatch):
    regression_bundle.source["prepared_generation_requests"].append(deepcopy(regression_bundle.source["prepared_generation_requests"][0]))
    write_json(regression_bundle.source_path, regression_bundle.source)
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--plan") == 2
    assert not regression_bundle.output_path.exists()


@pytest.mark.parametrize("change", ["dataset_binding", "public_binding", "rubric_binding", "unknown_check", "missing_case", "rubric_content"])
def test_stale_or_incomplete_acceptance_stops_before_execution(regression_cli, regression_bundle, monkeypatch, change):
    if change == "dataset_binding":
        regression_bundle.checklist["dataset_canonical_sha256"] = "0" * 64
    elif change == "public_binding":
        regression_bundle.checklist["public_text_sha256"] = "0" * 64
    elif change == "rubric_binding":
        regression_bundle.checklist["rubric_canonical_sha256"] = "0" * 64
    elif change == "unknown_check":
        regression_bundle.checklist["cases"][0]["critical_check_ids"][0] = "removed_check"
    elif change == "missing_case":
        regression_bundle.checklist["cases"].pop()
    else:
        regression_bundle.rubric["cases"][0]["checks"][0]["description"] += "修改"
        write_json(regression_bundle.rubric_path, regression_bundle.rubric)
    write_json(regression_bundle.checklist_path, regression_bundle.checklist)
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--mock") == 2
    assert not regression_bundle.output_path.exists()


def test_acceptance_reuses_the_six_original_questions_and_sixteen_checks(regression_cli, regression_bundle, monkeypatch, capsys):
    forbid_configuration(regression_cli, monkeypatch)
    assert run(regression_cli, monkeypatch, regression_bundle, "--all-cases", "--max-calls", "12", "--plan") == 0
    acceptance = json.loads(capsys.readouterr().out)["regression_acceptance"]
    assert len(acceptance) == 6
    assert sum(len(row["checks"]) for row in acceptance) == 16
    original = {case["id"]: case for case in regression_bundle.dataset["cases"]}
    rubric = {case["case_id"]: case for case in regression_bundle.rubric["cases"]}
    for row in acceptance:
        assert row["question"] == original[row["case_id"]]["question"]
        assert row["expected_abstained"] is original[row["case_id"]]["expected_abstained"]
        assert row["checks"] == [
            {
                **{key: value for key, value in check.items() if key != "evidence"},
                "evidence_sha256": hashlib.sha256(check["evidence"].encode("utf-8")).hexdigest(),
            }
            for check in rubric[row["case_id"]]["checks"]
        ]


def test_all_cases_and_explicit_case_id_are_mutually_exclusive(regression_cli, regression_bundle, monkeypatch):
    forbid_configuration(regression_cli, monkeypatch)
    with pytest.raises(SystemExit) as caught:
        run(regression_cli, monkeypatch, regression_bundle, "--plan", "--all-cases", "--case-id", "restart_recovery_steps", "--max-calls", "12")
    assert caught.value.code == 2
    assert not regression_bundle.output_path.exists()


def test_repository_controlled_six_case_mock_has_twelve_responses_and_unknown_quality(regression_cli, tmp_path, monkeypatch, capsys):
    report_root = tmp_path / "reports"
    report_root.mkdir()
    monkeypatch.setattr(regression_cli, "REPORT_ROOT", report_root)
    source_path = REPOSITORY_ROOT / "examples/evaluation/generation-requests-controlled-six.json"
    output_path = report_root / "six-case-mock.json"
    original = source_path.read_bytes()
    forbid_configuration(regression_cli, monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "compare_prompts.py", "--mock", "--all-cases", "--max-calls", "12",
        "--source-report", str(source_path), "--report", str(output_path),
    ])
    assert regression_cli.main() == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["planned_requests"] == summary["attempted_requests"] == summary["valid_response_count"] == 12
    assert summary["execution_failed_count"] == 0
    assert summary["complete_pair_count"] == 6
    assert summary["pair_execution_coverage"] == 1
    assert summary["new_model_calls"] == 0
    assert summary["quality_pass_fraction"] is summary["winner"] is None
    assert len(report["provenance"]["regression_acceptance"]) == 6
    assert report["execution_kind"] == "受控模拟"
    assert all(row["semantic_review"] == "待审核" and row["quality_pass"] is None for row in report["trials"])
    assert {row["case_id"] for row in report["trials"]} == set(report["case_ids"])
    assert source_path.read_bytes() == original
    assert json.loads(capsys.readouterr().out)["summary"]["new_model_calls"] == 0
