"""提示词实验命令行的凭据边界、固定输入、预算和安全报告测试。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.providers.compatible_chat import JsonChatResult, JsonChatTimeoutError
from app.providers.generation_snapshot import capture_generation_request, digest


PUBLIC_TEXT = "暂停导入，重启服务；健康检查通过后，才能恢复导入。"
QUESTION = "服务重启后如何恢复导入？"
CASE_ID = "restart_recovery_steps"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def comparison_cli():
    script = Path(__file__).resolve().parents[1] / "scripts/compare_prompts.py"
    spec = importlib.util.spec_from_file_location("controlled_prompt_comparison_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def comparison_bundle(comparison_cli, tmp_path, monkeypatch):
    report_root, example_root = tmp_path / "reports", tmp_path / "examples"
    report_root.mkdir()
    example_root.mkdir()
    corpus_path = example_root / "public.md"
    corpus_path.write_text(PUBLIC_TEXT, encoding="utf-8")
    body = {
        "model": "controlled-model",
        "messages": [
            {"role": "system", "content": "只根据资料回答，返回规定的 JSON 格式。"},
            {"role": "user", "content": json.dumps({"question": QUESTION, "sources": [
                {"source_id": 1, "content": PUBLIC_TEXT},
            ]}, ensure_ascii=False)},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
    }
    snapshot = capture_generation_request(body, PUBLIC_TEXT, QUESTION)
    dataset = {"dataset_version": "controlled-public-v1", "cases": [
        {"id": CASE_ID, "question": QUESTION, "expected_abstained": False},
    ]}
    source = {
        "generation_capture_version": comparison_cli.SNAPSHOT_VERSION,
        "dataset_version": dataset["dataset_version"],
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "controlled_fixture": True,
        "prepared_generation_requests": [{"case_id": CASE_ID, "snapshot": snapshot}],
    }
    source_path, dataset_path = example_root / "source.json", example_root / "cases.json"
    output_path = report_root / "comparison.json"
    write_json(source_path, source)
    write_json(dataset_path, dataset)
    for name, value in {
        "REPORT_ROOT": report_root, "EXAMPLE_ROOT": example_root,
        "SOURCE_REPORT": source_path, "DATASET_PATH": dataset_path, "CORPUS_PATH": corpus_path,
    }.items():
        monkeypatch.setattr(comparison_cli, name, value)
    return SimpleNamespace(
        root=tmp_path, report_root=report_root, example_root=example_root,
        source_path=source_path, dataset_path=dataset_path, corpus_path=corpus_path,
        output_path=output_path, source=source, dataset=dataset, snapshot=snapshot, body=body,
    )


def run(module, monkeypatch, bundle, *arguments):
    monkeypatch.setattr(sys, "argv", [
        "compare_prompts.py", "--source-report", str(bundle.source_path),
        "--report", str(bundle.output_path), *arguments,
    ])
    return module.main()


def forbid_configuration(module, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("预检、计划或模拟不得读取凭据、创建客户端或发送 HTTP 请求")
    monkeypatch.setattr(module, "configured_clients", forbidden)
    monkeypatch.setattr("app.core.config.Settings", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)


def make_real_source(bundle):
    # 仅改变测试来源标记；不会把它变为真实历史数据，也不会调用真实模型。
    bundle.source["controlled_fixture"] = False
    bundle.source["execution"] = "TestClient 调用真实路由、本机数据库与供应商 API"
    write_json(bundle.source_path, bundle.source)


def configure_fake_clients(module, monkeypatch, *, model="controlled-model", timeout_variant=None):
    calls, created = [], []
    settings = SimpleNamespace(
        generation_base_url="https://controlled.example/v1",
        generation_api_key=SecretStr("VIRTUAL-KEY-NOT-A-REAL-CREDENTIAL"),
        generation_model=model, generation_timeout_seconds=3,
        generation_total_timeout_seconds=6, generation_max_tokens=999,
    )
    monkeypatch.setattr("app.core.config.Settings", lambda: settings)

    class FakeClient:
        def __init__(self, base_url, api_key, requested_model, timeout, total_timeout, *, max_tokens):
            created.append({"model": requested_model, "max_tokens": max_tokens})

        def complete_request(self, body):
            calls.append(deepcopy(body))
            variant = "candidate" if body["messages"][0]["content"].endswith(module.CANDIDATE_SUFFIX) else "baseline"
            if variant == timeout_variant:
                raise JsonChatTimeoutError("PRIVATE-ERROR-MUST-NOT-APPEAR")
            data = json.loads(body["messages"][1]["content"])
            return JsonChatResult({
                "answer": PUBLIC_TEXT, "abstained": False,
                "citations": [{"source_id": 1, "quote": data["sources"][0]["content"]}],
            }, "controlled-returned-model", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    monkeypatch.setattr(module, "CompatibleJsonChatClient", FakeClient)
    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: pytest.fail("此测试不得发送真实 HTTP 请求"))
    return calls, created


def test_plan_never_reads_settings_sends_requests_or_creates_report(comparison_cli, comparison_bundle, monkeypatch, capsys):
    forbid_configuration(comparison_cli, monkeypatch)
    original = comparison_bundle.source_path.read_bytes()
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--plan") == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["planned_requests"] == 2
    assert printed["new_model_calls"] == 0
    assert printed["quality_pass_fraction"] is None
    assert printed["case_ids"] == [CASE_ID]
    assert printed["controlled_fixture"] is True
    assert not comparison_bundle.output_path.exists()
    assert comparison_bundle.source_path.read_bytes() == original


def test_plan_can_inspect_existing_output_without_modifying_it(comparison_cli, comparison_bundle, monkeypatch):
    comparison_bundle.output_path.write_text("old report", encoding="utf-8")
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--plan") == 0
    assert comparison_bundle.output_path.read_text(encoding="utf-8") == "old report"


def test_mock_keeps_evidence_and_parameters_fixed_without_quality_claim(comparison_cli, comparison_bundle, monkeypatch, capsys):
    forbid_configuration(comparison_cli, monkeypatch)
    original = comparison_bundle.source_path.read_bytes()
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--mock") == 0
    report = json.loads(comparison_bundle.output_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["attempted_requests"] == summary["valid_response_count"] == 2
    assert summary["new_model_calls"] == 0
    assert summary["complete_pair_count"] == summary["pair_execution_coverage"] == 1
    assert summary["quality_pass_fraction"] is summary["winner"] is None
    assert summary["known_usage_totals"] is summary["cost"] is None
    assert report["execution_kind"] == "受控模拟"
    assert report["automatic_retries"] == 0
    baseline, candidate = report["trials"]
    assert baseline["response"]["answer"] != candidate["response"]["answer"]
    assert baseline["fixed_request_sha256"] == candidate["fixed_request_sha256"]
    assert baseline["input_sha256"] == candidate["input_sha256"] == comparison_bundle.snapshot["input_sha256"]
    assert baseline["request_body"]["messages"][1] == candidate["request_body"]["messages"][1] == comparison_bundle.body["messages"][1]
    assert baseline["system_prompt_sha256"] != candidate["system_prompt_sha256"]
    for row in report["trials"]:
        assert row["citations_valid"] is True
        assert row["quality_pass"] is None
        assert row["semantic_review"] == "待审核"
        assert row["request_body"]["max_tokens"] == 500
    assert comparison_bundle.source_path.read_bytes() == original
    assert json.loads(capsys.readouterr().out)["execution_kind"] == "受控模拟"


@pytest.mark.parametrize("variant", ["baseline", "candidate"])
def test_mock_timeout_is_incomplete_pair_not_quality_failure(comparison_cli, comparison_bundle, monkeypatch, variant):
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--mock", "--mock-timeout", variant) == 1
    report = json.loads(comparison_bundle.output_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["planned_requests"] == summary["attempted_requests"] == 2
    assert summary["valid_response_count"] == summary["execution_failed_count"] == 1
    assert summary["complete_pair_count"] == summary["pair_execution_coverage"] == 0
    assert summary["by_variant"][variant]["failed"] == 1
    assert summary["new_model_calls"] == 0
    assert summary["quality_pass_fraction"] is summary["winner"] is None
    failed = next(row for row in report["trials"] if row["variant_id"] == variant)
    assert failed["execution"]["error_category"] == "timeout"
    assert failed["execution"]["remote_execution"] == "未知"
    assert failed["response"] is failed["quality_pass"] is failed["citations_valid"] is None


def test_model_exception_never_leaks_raw_content_or_triggers_retry(comparison_cli, comparison_bundle, monkeypatch, capsys):
    calls = []
    class FailingControlledClient:
        def __init__(self, timeout):
            pass
        def complete_request(self, body):
            calls.append(body)
            raise RuntimeError("PRIVATE-KEY-AND-RESPONSE-MUST-NOT-APPEAR")
    monkeypatch.setattr(comparison_cli, "ControlledClient", FailingControlledClient)
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--mock") == 1
    text = comparison_bundle.output_path.read_text(encoding="utf-8")
    assert len(calls) == 2
    assert "PRIVATE-KEY" not in text + capsys.readouterr().out
    report = json.loads(text)
    assert report["summary"]["execution_failed_count"] == 2
    assert all(row["execution"]["error_category"] == "unexpected_error" for row in report["trials"])


def test_controlled_source_rejected_before_real_configuration(comparison_cli, comparison_bundle, monkeypatch, capsys):
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "controlled_input_cannot_run_real_models"
    assert not comparison_bundle.output_path.exists()


def test_real_mode_uses_fake_client_exactly_twice_with_snapshot_output_limit(comparison_cli, comparison_bundle, monkeypatch):
    make_real_source(comparison_bundle)
    calls, created = configure_fake_clients(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 0
    assert len(calls) == 2
    assert created == [{"model": "controlled-model", "max_tokens": 500}]
    assert calls[0] == comparison_bundle.body
    expected_candidate = deepcopy(comparison_bundle.body)
    expected_candidate["messages"][0]["content"] += comparison_cli.CANDIDATE_SUFFIX
    assert calls[1] == expected_candidate
    report = json.loads(comparison_bundle.output_path.read_text(encoding="utf-8"))
    assert report["summary"]["new_model_calls"] == 2
    assert report["summary"]["known_usage_totals"]["total_tokens"] == 30
    assert report["summary"]["usage_reported_trials"] == 2
    assert report["summary"]["quality_pass_fraction"] is None
    assert "VIRTUAL-KEY" not in comparison_bundle.output_path.read_text(encoding="utf-8")


def test_real_mode_timeout_continues_other_variant_without_retry(comparison_cli, comparison_bundle, monkeypatch, capsys):
    make_real_source(comparison_bundle)
    calls, _ = configure_fake_clients(comparison_cli, monkeypatch, timeout_variant="baseline")
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 1
    assert len(calls) == 2
    text = comparison_bundle.output_path.read_text(encoding="utf-8")
    assert "PRIVATE-ERROR" not in text + capsys.readouterr().out
    report = json.loads(text)
    assert report["summary"]["new_model_calls"] == 2
    assert report["summary"]["valid_response_count"] == 1
    assert report["summary"]["pair_execution_coverage"] == 0
    assert report["trials"][0]["execution"]["remote_execution"] == "未知"
    assert report["trials"][1]["execution"]["status"] == "成功"


def test_changed_configured_model_stops_before_client_creation(comparison_cli, comparison_bundle, monkeypatch, capsys):
    make_real_source(comparison_bundle)
    calls, created = configure_fake_clients(comparison_cli, monkeypatch, model="different-model")
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 2
    assert not calls and not created
    assert not comparison_bundle.output_path.exists()
    assert json.loads(capsys.readouterr().out)["reason"] == "request_model_changed"


@pytest.mark.parametrize("budget", ["0", "1", "21", "-1"])
def test_invalid_call_budget_stops_before_configuration(comparison_cli, comparison_bundle, monkeypatch, budget):
    make_real_source(comparison_bundle)
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run", "--max-calls", budget) == 2
    assert not comparison_bundle.output_path.exists()


@pytest.mark.parametrize("selected", [["unknown-case"], [CASE_ID, CASE_ID]])
def test_unknown_or_duplicate_case_is_rejected_before_configuration(comparison_cli, comparison_bundle, monkeypatch, selected):
    make_real_source(comparison_bundle)
    forbid_configuration(comparison_cli, monkeypatch)
    arguments = [item for case_id in selected for item in ("--case-id", case_id)]
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run", *arguments) == 2
    assert not comparison_bundle.output_path.exists()


@pytest.mark.parametrize("change", [
    "request_body", "request_hash", "input_hash", "prompt_hash", "public_hash", "source_count",
    "context_characters", "question", "public_text", "corpus_hash", "dataset_version",
    "duplicate_capture", "missing_capture", "duplicate_dataset", "non_public_source", "source_kind",
])
def test_changed_inputs_and_invalid_source_are_rejected_before_configuration(comparison_cli, comparison_bundle, monkeypatch, change, capsys):
    make_real_source(comparison_bundle)
    snapshot = comparison_bundle.source["prepared_generation_requests"][0]["snapshot"]
    if change == "request_body":
        snapshot["request_body"]["max_tokens"] += 1
    elif change in {"request_hash", "input_hash", "prompt_hash", "public_hash"}:
        field = {"request_hash": "request_body_sha256", "input_hash": "input_sha256", "prompt_hash": "system_prompt_sha256", "public_hash": "public_text_sha256"}[change]
        snapshot[field] = "0" * 64
    elif change in {"source_count", "context_characters"}:
        snapshot[change] += 1
    elif change == "question":
        comparison_bundle.dataset["cases"][0]["question"] = "不同的公开问题"
    elif change == "public_text":
        comparison_bundle.corpus_path.write_text("不同的公开原文", encoding="utf-8")
    elif change == "corpus_hash":
        comparison_bundle.source["corpus_sha256"] = "0" * 64
    elif change == "dataset_version":
        comparison_bundle.source["dataset_version"] = "different-version"
    elif change == "duplicate_capture":
        comparison_bundle.source["prepared_generation_requests"].append(deepcopy(comparison_bundle.source["prepared_generation_requests"][0]))
    elif change == "missing_capture":
        comparison_bundle.source["prepared_generation_requests"] = []
    elif change == "duplicate_dataset":
        comparison_bundle.dataset["cases"].append(deepcopy(comparison_bundle.dataset["cases"][0]))
    elif change == "non_public_source":
        data = json.loads(snapshot["request_body"]["messages"][1]["content"])
        data["sources"][0]["content"] = "PRIVATE-SOURCE-MUST-NOT-APPEAR"
        snapshot["request_body"]["messages"][1]["content"] = json.dumps(data)
        snapshot["request_body_sha256"] = digest(snapshot["request_body"])
    else:
        comparison_bundle.source["execution"] = "untrusted-source-marker"
    write_json(comparison_bundle.source_path, comparison_bundle.source)
    write_json(comparison_bundle.dataset_path, comparison_bundle.dataset)
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 2
    assert not comparison_bundle.output_path.exists()
    assert "PRIVATE-SOURCE" not in capsys.readouterr().out


@pytest.mark.parametrize("argument,location", [
    ("--source-report", "outside"), ("--report", "outside"),
    ("--source-report", "extension"), ("--report", "extension"),
    ("--report", "source"),
])
def test_path_guards_reject_outside_wrong_extension_and_source_overwrite(comparison_cli, comparison_bundle, monkeypatch, argument, location):
    selected_path = comparison_bundle.root / "outside.json" if location == "outside" else comparison_bundle.source_path if location == "source" else comparison_bundle.report_root / "report.txt"
    original = comparison_bundle.source_path.read_bytes()
    forbid_configuration(comparison_cli, monkeypatch)
    with pytest.raises(SystemExit) as caught:
        run(comparison_cli, monkeypatch, comparison_bundle, "--mock", argument, str(selected_path))
    assert caught.value.code == 2
    assert comparison_bundle.source_path.read_bytes() == original
    assert not comparison_bundle.output_path.exists()


def test_existing_report_is_not_overwritten_or_followed_by_requests(comparison_cli, comparison_bundle, monkeypatch, capsys):
    comparison_bundle.output_path.write_text("old report", encoding="utf-8")
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--mock") == 2
    assert comparison_bundle.output_path.read_text(encoding="utf-8") == "old report"
    assert json.loads(capsys.readouterr().out)["reason"] == "output_exists_use_new_path"


def test_exclusive_open_prevents_output_race_before_any_model_request(comparison_cli, comparison_bundle, monkeypatch):
    make_real_source(comparison_bundle)
    calls = []
    def clients(cases):
        comparison_bundle.output_path.write_text("created concurrently", encoding="utf-8")
        return {case.case_id: SimpleNamespace(complete_request=lambda body: calls.append(body)) for case in cases}
    monkeypatch.setattr(comparison_cli, "configured_clients", clients)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--run") == 2
    assert not calls
    assert comparison_bundle.output_path.read_text(encoding="utf-8") == "created concurrently"


@pytest.mark.parametrize("mode", ["--plan", "--run"])
def test_mock_fault_option_is_rejected_outside_mock_mode(comparison_cli, comparison_bundle, monkeypatch, mode):
    forbid_configuration(comparison_cli, monkeypatch)
    with pytest.raises(SystemExit) as caught:
        run(comparison_cli, monkeypatch, comparison_bundle, mode, "--mock-timeout", "baseline")
    assert caught.value.code == 2
    assert not comparison_bundle.output_path.exists()


@pytest.mark.parametrize("content", ["not JSON PRIVATE-SOURCE", '"PRIVATE-SOURCE"'])
def test_invalid_source_json_is_reported_without_echoing_its_content(comparison_cli, comparison_bundle, monkeypatch, capsys, content):
    comparison_bundle.source_path.write_text(content, encoding="utf-8")
    forbid_configuration(comparison_cli, monkeypatch)
    assert run(comparison_cli, monkeypatch, comparison_bundle, "--plan") == 2
    assert "PRIVATE-SOURCE" not in capsys.readouterr().out
    assert not comparison_bundle.output_path.exists()
