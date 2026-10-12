"""显式运行离线模型评审；计划模式不读取配置、不调用服务。"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

import evaluate_answers as replay
from app.evaluation.answers import AnswerEvaluationError, CaseReview, evaluate_answer, summarize_answers, canonical_sha256
from app.knowledge.chunking import split_text
from app.providers.compatible_review import CompatibleReviewProvider, REVIEW_PROMPT_SHA256, REVIEW_PROMPT_VERSION
from app.providers.review import ReviewConfigurationError, ReviewError, ReviewProvider, ReviewResult, ReviewTask, ReviewTimeoutError, TokenUsage


OUTPUT_REPORT = replay.REPORT_ROOT / "model-review-2026-10-11.json"
EVIDENCE_SCOPE = "对照固定公开完整文档复核；原报告未保存实际生成上下文，不能证明答案只使用当时选中的证据"


def prepare(source_path: Path, case_ids: list[str], max_cases: int) -> tuple[dict, dict[str, ReviewTask], dict]:
    """所有来源、任务和调用预算先验证，通过后才允许实例化服务。"""
    baseline = replay.build_report(source_path)
    public_text = replay.CORPUS_PATH.read_text(encoding="utf-8")
    rubric, source = replay.read_json(replay.RUBRIC_PATH), replay.read_json(source_path)
    if (
        canonical_sha256(rubric) != baseline["rubric_canonical_sha256"]
        or canonical_sha256(source) != baseline["source_report_canonical_sha256"]
        or hashlib.sha256(public_text.encode("utf-8")).hexdigest() != rubric["corpus_text_sha256"]
    ):
        raise replay.AnswerReplayError("source_changed_during_preparation")
    available = {row["case_id"] for row in baseline["results"]}
    selected = case_ids or [row["case_id"] for row in baseline["results"]]
    if len(set(selected)) != len(selected) or not set(selected) <= available:
        raise replay.AnswerReplayError("selected_case_set")
    if type(max_cases) is not int or not 1 <= max_cases <= 20 or len(selected) > max_cases:
        raise replay.AnswerReplayError("call_budget")
    tasks = {
        row["case_id"]: ReviewTask(
            case_id=row["case_id"], answer_sha256=row["review"]["answer_sha256"],
            question=row["question"], response=row["response"], public_text=public_text,
            checks=row["rubric_checks"],
        )
        for row in baseline["results"] if row["case_id"] in selected
    }
    chunks = split_text(
        UUID(source["document_id"]), public_text,
        chunk_size=rubric["chunking"]["chunk_size"], overlap=rubric["chunking"]["overlap"],
    )
    context = {
        "public_text": public_text, "document_id": source["document_id"],
        "document_name": replay.CORPUS_PATH.name,
        "chunk_ranges": {chunk.chunk_index: (chunk.start_char, chunk.end_char) for chunk in chunks},
    }
    return baseline, tasks, context


def configured_provider() -> tuple[ReviewProvider, str]:
    # 导入与实例化只发生在显式 --run；不默默借用生成密钥或模型。
    from app.core.config import Settings
    try:
        settings = Settings()
        if not settings.review_base_url or not settings.review_api_key or not settings.review_model:
            raise ReviewConfigurationError("评审服务尚未配置。")
        return CompatibleReviewProvider(
            settings.review_base_url, settings.review_api_key, settings.review_model,
            settings.review_timeout_seconds, settings.review_total_timeout_seconds, settings.review_max_tokens,
        ), settings.review_model.strip()
    except ValidationError:
        raise ReviewConfigurationError("评审配置校验失败。") from None


def batch_summary(report: dict) -> dict:
    executions = [row["ai_review_execution"] for row in report["results"]]
    known = [item["usage"] for item in executions if item.get("usage") is not None]
    completed = [row for row in report["results"] if row["ai_pass"] is not None]
    return {
        "selected_case_count": report["model_review"]["planned_calls"],
        "provider_calls_attempted": sum(item["status"] in ("执行中", "成功", "失败") for item in executions),
        "valid_response_cases": sum(item["status"] == "成功" for item in executions),
        "execution_failed_cases": sum(item["status"] == "失败" for item in executions),
        "ai_completed_cases": len(completed),
        "ai_review_coverage": len(completed) / report["model_review"]["planned_calls"],
        "ai_pass_cases": sum(row["ai_pass"] is True for row in completed),
        "ai_fail_cases": sum(row["ai_pass"] is False for row in completed),
        "ai_review_pass_fraction": sum(row["ai_pass"] is True for row in completed) / len(completed) if completed else None,
        "usage_reported_cases": len(known),
        "known_usage_totals": {key: sum(item[key] for item in known) for key in ("prompt_tokens", "completion_tokens", "total_tokens")} if known else None,
        "cost": None,
    }


def run_batch(
    baseline: dict, tasks: dict[str, ReviewTask], context: dict, provider: ReviewProvider,
    request_model: str, checkpoint=None,
) -> dict:
    """单题失败继续后续题；每题保存已知结果，不复用人工成绩。"""
    report = deepcopy(baseline)
    report["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["execution"] = "离线模型辅助评审；未重新检索或生成回答"
    report["model_review"] = {
        "run_state": "运行中", "request_model": request_model,
        "request_parameters": getattr(provider, "request_parameters", None),
        "prompt_version": REVIEW_PROMPT_VERSION, "prompt_sha256": REVIEW_PROMPT_SHA256,
        "corpus_text_sha256": hashlib.sha256(context["public_text"].encode("utf-8")).hexdigest(),
        "evidence_scope": EVIDENCE_SCOPE, "planned_calls": len(tasks),
        "automatic_retries": 0, "cost_note": "只记录服务返回的 token 用量，未配置计价规则，费用不推算",
    }
    for row in report["results"]:
        row["ai_pass"] = None
        row["ai_review_execution"] = {"status": "待执行" if row["case_id"] in tasks else "未选择"}

    def refresh() -> None:
        report["summary"] = summarize_answers(report["results"])
        report["model_review_summary"] = batch_summary(report)
        report["new_model_calls"] = report["model_review_summary"]["provider_calls_attempted"]
        if checkpoint is not None:
            checkpoint(report)

    refresh()
    for index, row in enumerate(report["results"]):
        task = tasks.get(row["case_id"])
        if task is None:
            continue
        started = monotonic()
        row["ai_review_execution"] = {"status": "执行中", "task_sha256": canonical_sha256(task.model_dump())}
        refresh()
        try:
            result = provider.review_answer(task)
            if not isinstance(result, ReviewResult) or not isinstance(result.response_model, str) or not 1 <= len(result.response_model.strip()) <= 200:
                raise ReviewError("评审返回类型无效。")
            review = CaseReview.model_validate(result.review.model_dump(), strict=True)
            if review.reviewer_kind not in (None, "AI辅助"):
                raise ReviewError("模型不能提交人工审核身份。")
            usage = TokenUsage.model_validate(result.usage.model_dump()) if result.usage is not None else None
            verified = evaluate_answer(
                case_id=task.case_id, question=task.question,
                expected_abstained=row["expected_abstained"], response=row["response"],
                checks=task.checks, review=review, **context,
            )
            complete = all(item.verdict != "待审核" for item in review.checks.values())
            verified["ai_pass"] = verified["abstention_matches_expected"] and all(item.verdict == "通过" for item in review.checks.values()) if complete else None
            verified["ai_review_execution"] = {
                "status": "成功", "response_model": result.response_model.strip(),
                "task_sha256": canonical_sha256(task.model_dump()),
                "usage": usage.model_dump() if usage is not None else None,
                "seconds": round(monotonic() - started, 4),
            }
            report["results"][index] = verified
        except Exception as error:
            if isinstance(error, ReviewTimeoutError):
                category = "timeout"
            elif isinstance(error, (AnswerEvaluationError, ValidationError, AttributeError, TypeError)):
                category = "invalid_review"
            elif isinstance(error, ReviewError):
                category = "provider_error"
            else:
                category = "unexpected_error"
            row["ai_review_execution"] = {
                "status": "失败", "error_category": category,
                "task_sha256": canonical_sha256(task.model_dump()), "seconds": round(monotonic() - started, 4),
            }
        refresh()
    report["model_review"]["run_state"] = "已完成"
    refresh()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="默认只规划公开题集；--run 才读取独立评审配置并产生付费请求。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--source-report", default=str(replay.SOURCE_REPORT))
    parser.add_argument("--report", default=str(OUTPUT_REPORT))
    parser.add_argument("--case-id", action="append", default=[], help="只评审指定题目，可重复此选项")
    parser.add_argument("--max-cases", type=int, default=6, help="本次最多允许多少次 provider 调用，默认 6")
    args = parser.parse_args()
    source_path, output_path = Path(args.source_report).resolve(), Path(args.report).resolve()
    if any(path.suffix != ".json" or not path.is_relative_to(replay.REPORT_ROOT.resolve()) for path in (source_path, output_path)):
        parser.error("输入和输出须为 docs/evaluation 内的 .json 文件")
    if source_path == output_path:
        parser.error("输出不能覆盖来源报告")
    try:
        if args.run and output_path.exists():
            raise replay.AnswerReplayError("output_exists_use_new_path")
        baseline, tasks, context = prepare(source_path, args.case_id, args.max_cases)
        if args.plan:
            print(json.dumps({
                "stage": "plan", "planned_calls": len(tasks), "case_ids": list(tasks),
                "new_model_calls": 0, "prompt_version": REVIEW_PROMPT_VERSION,
                "evidence_scope": EVIDENCE_SCOPE, "human_review_pass_fraction": None,
            }, ensure_ascii=True))
            return 0
        provider, request_model = configured_provider()
        # 在发请求前排他占用输出文件；只更新本进程新建的报告。
        with output_path.open("x", encoding="utf-8") as handle:
            def checkpoint(report: dict) -> None:
                handle.seek(0)
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.truncate()
                handle.flush()
            report = run_batch(baseline, tasks, context, provider, request_model, checkpoint)
        print(json.dumps({"stage": "complete", "model_review_summary": report["model_review_summary"], "human_review_pass_fraction": report["summary"]["human_review_pass_fraction"]}, ensure_ascii=True))
        return 1 if report["model_review_summary"]["execution_failed_cases"] else 0
    except Exception as error:
        failure = {"stage": "failed", "error_type": type(error).__name__}
        if isinstance(error, replay.AnswerReplayError):
            failure["reason"] = str(error)
        print(json.dumps(failure, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
