"""离线批处理的调用边界、失败语义、持久记录和分母测试。"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_answer_evaluation_cli import bundle, cli, write_json
from app.evaluation.answers import CaseReview, canonical_sha256
from app.providers.review import ReviewResult, ReviewTimeoutError, TokenUsage


@pytest.fixture
def review_cli(cli, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("controlled_model_review_cli", scripts / "review_answers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "replay", cli)
    return module


def run(module, monkeypatch, bundle, *args):
    monkeypatch.setattr(sys, "argv", ["review_answers.py", "--source-report", str(bundle.source_path), "--report", str(bundle.output_path), *args])
    return module.main()


def result(task, *, verdict="通过", kind="AI辅助", usage=True):
    decision = {"verdict": verdict, "reason": "答案保留检查通过后才恢复导入的前提。", "answer_excerpt": None, "evidence_excerpt": None}
    if verdict == "不通过":
        decision.update(reason="受控测试模拟评审不通过；不是实际模型判定。", answer_excerpt=task.response.answer, evidence_excerpt=task.public_text)
    if verdict == "待审核":
        decision.update(reason=None)
    review = CaseReview(
        case_id=task.case_id, answer_sha256=task.answer_sha256,
        reviewer_kind=kind if verdict != "待审核" else None,
        reviewer_name="受控模拟评审" if verdict != "待审核" else None,
        checks={item.id: decision for item in task.checks},
    )
    return ReviewResult(review, "controlled-model", TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15) if usage else None)


def use_provider(module, monkeypatch, behavior):
    class FakeProvider:
        def review_answer(self, task):
            return behavior(task)
    monkeypatch.setattr(module, "configured_provider", lambda: (FakeProvider(), "controlled-model"))


def expand(bundle, count):
    first_case, first_attempt, first_checks = bundle.dataset["cases"][0], bundle.source["attempts"][0], bundle.rubric["cases"][0]
    bundle.dataset["cases"], bundle.source["attempts"], bundle.rubric["cases"] = [], [], []
    for index in range(count):
        case_id = f"recovery_{index}"
        case, attempt, checks = deepcopy(first_case), deepcopy(first_attempt), deepcopy(first_checks)
        case["id"], attempt["id"], checks["case_id"] = case_id, case_id, case_id
        bundle.dataset["cases"].append(case)
        bundle.source["attempts"].append(attempt)
        bundle.rubric["cases"].append(checks)
    bundle.rubric["source_report_canonical_sha256"] = canonical_sha256(bundle.source)
    bundle.rubric["source_dataset_canonical_sha256"] = canonical_sha256(bundle.dataset)
    for path, value in [(bundle.source_path, bundle.source), (bundle.dataset_path, bundle.dataset), (bundle.rubric_path, bundle.rubric)]:
        write_json(path, value)


def test_plan_never_reads_settings_calls_models_or_creates_output(review_cli, bundle, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("计划阶段不得读取凭据或调用服务")
    monkeypatch.setattr(review_cli, "configured_provider", forbidden)
    monkeypatch.setattr("app.core.config.Settings", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    assert run(review_cli, monkeypatch, bundle, "--plan") == 0
    assert not bundle.output_path.exists()
    assert json.loads(capsys.readouterr().out)["new_model_calls"] == 0


def test_missing_review_configuration_does_not_fall_back_to_generation_key(review_cli, bundle, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("app.core.config.Settings", lambda: SimpleNamespace(
        review_base_url=None, review_api_key=None, review_model=None,
        generation_base_url="https://generation.example", generation_api_key="PRIVATE-KEY", generation_model="configured-model",
    ))
    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: pytest.fail("缺少独立配置时不得发请求"))
    assert run(review_cli, monkeypatch, bundle, "--run") == 2
    assert not bundle.output_path.exists()


def test_changed_source_is_rejected_before_model_calls(review_cli, bundle, monkeypatch):
    bundle.source["attempts"][0]["response"]["answer"] = "PRIVATE-CONTENT"
    write_json(bundle.source_path, bundle.source)
    monkeypatch.setattr(review_cli, "configured_provider", lambda: pytest.fail("来源不匹配时不得调用服务"))
    assert run(review_cli, monkeypatch, bundle, "--run") == 2
    assert not bundle.output_path.exists()


@pytest.mark.parametrize("argument", ["--report", "--source-report"])
def test_paths_outside_public_report_directory_are_rejected(review_cli, bundle, monkeypatch, argument):
    with pytest.raises(SystemExit) as caught:
        run(review_cli, monkeypatch, bundle, "--run", argument, str(bundle.root / "outside.json"))
    assert caught.value.code == 2
    assert not (bundle.root / "outside.json").exists()


def test_successful_model_review_preserves_source_and_never_becomes_human_grade(review_cli, bundle, monkeypatch, cli):
    original = bundle.source_path.read_bytes()
    use_provider(review_cli, monkeypatch, result)
    assert run(review_cli, monkeypatch, bundle, "--run") == 0
    report = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert bundle.source_path.read_bytes() == original
    assert report["summary"]["human_review_pass_fraction"] is None
    assert report["model_review_summary"]["ai_pass_cases"] == 1
    assert report["model_review_summary"]["known_usage_totals"]["total_tokens"] == 15
    assert report["model_review_summary"]["cost"] is None
    assert cli.build_report(bundle.source_path, bundle.output_path)["summary"]["human_reviewed_cases"] == 0


def test_one_timeout_does_not_fail_quality_or_stop_remaining_cases(review_cli, bundle, monkeypatch):
    expand(bundle, 3)
    calls = []
    def behavior(task):
        calls.append(task.case_id)
        if task.case_id == "recovery_1":
            raise ReviewTimeoutError("PRIVATE-ERROR-MUST-NOT-APPEAR")
        return result(task, usage=task.case_id == "recovery_0")
    use_provider(review_cli, monkeypatch, behavior)
    assert run(review_cli, monkeypatch, bundle, "--run") == 1
    report = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert calls == ["recovery_0", "recovery_1", "recovery_2"]
    summary = report["model_review_summary"]
    assert summary["execution_failed_cases"] == 1
    assert summary["ai_completed_cases"] == 2
    assert summary["ai_review_pass_fraction"] == 1
    assert summary["usage_reported_cases"] == 1
    assert report["results"][1]["ai_pass"] is None
    assert report["results"][1]["review"]["reviewer_kind"] is None
    assert "PRIVATE-ERROR" not in bundle.output_path.read_text(encoding="utf-8")


def test_model_quality_fail_is_a_successful_execution_not_a_timeout(review_cli, bundle, monkeypatch):
    use_provider(review_cli, monkeypatch, lambda task: result(task, verdict="不通过"))
    assert run(review_cli, monkeypatch, bundle, "--run") == 0
    report = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert report["model_review_summary"]["ai_fail_cases"] == 1
    assert report["model_review_summary"]["execution_failed_cases"] == 0
    assert report["results"][0]["human_pass"] is None


@pytest.mark.parametrize("change", ["human", "hash", "excerpt"])
def test_batch_revalidates_provider_identity_binding_and_excerpts(review_cli, bundle, monkeypatch, change):
    def behavior(task):
        returned = result(task)
        review = returned.review
        if change == "human":
            review = review.model_copy(update={"reviewer_kind": "人工"})
        elif change == "hash":
            review = review.model_copy(update={"answer_sha256": "0" * 64})
        else:
            checks = deepcopy(review.model_dump()["checks"])
            checks["healthcheck_gate"].update(verdict="不通过", answer_excerpt="捏造答案", evidence_excerpt=task.public_text)
            review = CaseReview.model_validate({**review.model_dump(), "checks": checks})
        return ReviewResult(review, returned.response_model, returned.usage)
    use_provider(review_cli, monkeypatch, behavior)
    assert run(review_cli, monkeypatch, bundle, "--run") == 1
    report = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert report["summary"]["human_reviewed_cases"] == 0
    assert report["results"][0]["ai_pass"] is None


def test_pending_decision_does_not_enter_model_pass_fraction(review_cli, bundle, monkeypatch):
    use_provider(review_cli, monkeypatch, lambda task: result(task, verdict="待审核", usage=False))
    assert run(review_cli, monkeypatch, bundle, "--run") == 0
    report = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert report["model_review_summary"]["valid_response_cases"] == 1
    assert report["model_review_summary"]["ai_completed_cases"] == 0
    assert report["model_review_summary"]["ai_review_pass_fraction"] is None
    assert report["model_review_summary"]["known_usage_totals"] is None


@pytest.mark.parametrize("args", [("--max-cases", "0"), ("--case-id", "unknown"), ("--case-id", "recovery", "--case-id", "recovery")])
def test_invalid_selection_or_budget_precedes_provider_configuration(review_cli, bundle, monkeypatch, args):
    monkeypatch.setattr(review_cli, "configured_provider", lambda: pytest.fail("无效计划不得读配置"))
    assert run(review_cli, monkeypatch, bundle, "--run", *args) == 2
    assert not bundle.output_path.exists()


def test_budget_rejection_and_case_selection_limit_actual_calls(review_cli, bundle, monkeypatch):
    expand(bundle, 3)
    calls = []
    use_provider(review_cli, monkeypatch, lambda task: calls.append(task.case_id) or result(task))
    assert run(review_cli, monkeypatch, bundle, "--run", "--max-cases", "1") == 2
    assert calls == []
    assert run(review_cli, monkeypatch, bundle, "--run", "--max-cases", "1", "--case-id", "recovery_2") == 0
    assert calls == ["recovery_2"]


def test_existing_output_is_not_overwritten_or_charged(review_cli, bundle, monkeypatch):
    bundle.output_path.write_text("keep-existing", encoding="utf-8")
    monkeypatch.setattr(review_cli, "configured_provider", lambda: pytest.fail("已有输出不得调用服务"))
    assert run(review_cli, monkeypatch, bundle, "--run") == 2
    assert bundle.output_path.read_text(encoding="utf-8") == "keep-existing"


def test_checkpoint_preserves_completed_case_when_next_call_is_interrupted(review_cli, bundle, monkeypatch):
    expand(bundle, 2)
    def behavior(task):
        saved = json.loads(bundle.output_path.read_text(encoding="utf-8"))
        assert saved["model_review"]["run_state"] == "运行中"
        if task.case_id == "recovery_1":
            assert saved["results"][0]["ai_pass"] is True
            assert saved["results"][1]["ai_review_execution"]["status"] == "执行中"
            assert saved["new_model_calls"] == 2
            raise KeyboardInterrupt()
        return result(task)
    use_provider(review_cli, monkeypatch, behavior)
    with pytest.raises(KeyboardInterrupt):
        run(review_cli, monkeypatch, bundle, "--run")
    saved = json.loads(bundle.output_path.read_text(encoding="utf-8"))
    assert saved["model_review"]["run_state"] == "运行中"
    assert saved["results"][0]["ai_pass"] is True
    assert saved["results"][1]["ai_pass"] is None
