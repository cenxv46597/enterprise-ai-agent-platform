"""离线核对固定公开问答，并生成或复核逐项人工审核记录。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

from app.evaluation.answers import (
    AnswerEvaluationError,
    CaseReview,
    RubricCheck,
    canonical_sha256,
    evaluate_answer,
    summarize_answers,
)
from app.knowledge.chunking import CHUNKING_VERSION, split_text


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPOSITORY_ROOT / "docs/evaluation"
SOURCE_REPORT = REPORT_ROOT / "live-rag-smoke-2026-10-10.json"
OUTPUT_REPORT = REPORT_ROOT / "answer-review-2026-10-11.json"
DATASET_PATH = REPOSITORY_ROOT / "examples/evaluation/rag-smoke-cases.json"
CORPUS_PATH = REPOSITORY_ROOT / "examples/knowledge/demo-product.md"
RUBRIC_PATH = REPOSITORY_ROOT / "examples/evaluation/answer-review-rubric.json"


class AnswerReplayError(ValueError):
    """固定错误阶段，不输出原始输入或配置。"""


def read_json(path: Path) -> dict:
    if path.stat().st_size > 2_000_000:
        raise AnswerReplayError("input_size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnswerReplayError("input_object")
    return value


def build_report(source_path: Path, reviews_path: Path | None = None) -> dict:
    """来源报告绑定公开检查项的摘要，原响应与旧备注不被覆盖。"""
    rubric = read_json(RUBRIC_PATH)
    source = read_json(source_path)
    dataset = read_json(DATASET_PATH)
    public_text = CORPUS_PATH.read_text(encoding="utf-8")
    if (
        rubric.get("version") != "answer-review-v1"
        or canonical_sha256(source) != rubric.get("source_report_canonical_sha256")
        or canonical_sha256(dataset) != rubric.get("source_dataset_canonical_sha256")
        or hashlib.sha256(public_text.encode("utf-8")).hexdigest() != rubric.get("corpus_text_sha256")
        or source.get("dataset_version") != dataset.get("dataset_version")
        or "failure" in source
    ):
        raise AnswerReplayError("source_identity")
    chunking = rubric["chunking"]
    if chunking.get("version") != CHUNKING_VERSION:
        raise AnswerReplayError("chunking_version")
    document_id = source["document_id"]
    chunks = split_text(UUID(document_id), public_text, chunk_size=chunking["chunk_size"], overlap=chunking["overlap"])
    if type(source.get("chunk_count")) is not int or len(chunks) != source["chunk_count"]:
        raise AnswerReplayError("chunk_count")
    chunk_ranges = {item.chunk_index: (item.start_char, item.end_char) for item in chunks}
    cases = dataset["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise AnswerReplayError("dataset_cases")
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise AnswerReplayError("duplicate_case")
    checks_by_id = {}
    for row in rubric["cases"]:
        if row["case_id"] in checks_by_id:
            raise AnswerReplayError("duplicate_rubric_case")
        checks_by_id[row["case_id"]] = [RubricCheck.model_validate(item) for item in row["checks"]]
    if set(checks_by_id) != set(by_id):
        raise AnswerReplayError("rubric_case_set")
    attempts = source.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != len(cases):
        raise AnswerReplayError("source_cases")
    responses = {}
    for row in attempts:
        case_id = row["id"]
        if case_id in responses or case_id not in by_id:
            raise AnswerReplayError("source_cases")
        case = by_id[case_id]
        if row.get("question") != case["question"] or row.get("expected_abstained") is not case["expected_abstained"]:
            raise AnswerReplayError("source_case_identity")
        responses[case_id] = row["response"]
    reviews = {}
    rubric_hash = canonical_sha256(rubric)
    if reviews_path is not None:
        saved = read_json(reviews_path)
        if (
            saved.get("source_report_canonical_sha256") != rubric["source_report_canonical_sha256"]
            or saved.get("rubric_canonical_sha256") != rubric_hash
        ):
            raise AnswerReplayError("review_identity")
        rows = saved.get("results")
        if not isinstance(rows, list) or len(rows) != len(cases):
            raise AnswerReplayError("review_cases")
        for row in rows:
            review = CaseReview.model_validate(row["review"])
            if row.get("case_id") != review.case_id or review.case_id in reviews:
                raise AnswerReplayError("review_cases")
            case = by_id.get(review.case_id)
            if case is None:
                raise AnswerReplayError("review_cases")
            if (
                row.get("response") != responses[review.case_id]
                or row.get("question") != case["question"]
                or row.get("expected_abstained") is not case["expected_abstained"]
                or row.get("rubric_checks") != [item.model_dump() for item in checks_by_id[review.case_id]]
            ):
                raise AnswerReplayError("review_display_changed")
            reviews[review.case_id] = review
        if set(reviews) != set(by_id):
            raise AnswerReplayError("review_cases")
    results = [
        evaluate_answer(
            case_id=case["id"], question=case["question"], expected_abstained=case["expected_abstained"],
            response=responses[case["id"]], checks=checks_by_id[case["id"]], public_text=public_text,
            document_id=document_id, document_name=CORPUS_PATH.name, chunk_ranges=chunk_ranges,
            review=reviews.get(case["id"]),
        )
        for case in cases
    ]
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution": "离线复核已保存的公开问答；程序核对引用，语义判定由审核记录提供",
        "source_report": source_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "source_report_canonical_sha256": rubric["source_report_canonical_sha256"],
        "source_dataset_version": dataset["dataset_version"],
        "rubric_canonical_sha256": rubric_hash, "rubric_version": rubric["version"],
        "new_model_calls": 0,
        "scope": "原始单文档六题，不代表带干扰知识库的新生成成绩；AI辅助和未完成审核不计人工质量分",
        "citation_scope": "核对固定公开原文与重建块范围；来源摘要绑定旧报告，不访问数据库验证当前记录",
        "results": results, "summary": summarize_answers(results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="不读取 .env，不连接数据库，不调用模型；保留原报告并拒绝覆盖已有文件。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="验证来源与审核记录，只显示汇总")
    mode.add_argument("--write-report", action="store_true", help="写入新的逐题审核报告")
    parser.add_argument("--source-report", default=str(SOURCE_REPORT))
    parser.add_argument("--reviews", help="复制生成报告后逐项填写 review，将该副本作为输入")
    parser.add_argument("--report", default=str(OUTPUT_REPORT))
    args = parser.parse_args()
    source_path = Path(args.source_report).resolve()
    output_path = Path(args.report).resolve()
    reviews_path = Path(args.reviews).resolve() if args.reviews else None
    paths = [source_path, output_path] + ([reviews_path] if reviews_path else [])
    if any(path.suffix != ".json" or not path.is_relative_to(REPORT_ROOT.resolve()) for path in paths):
        parser.error("输入和输出须为 docs/evaluation 内的 .json 文件")
    if output_path in {source_path, reviews_path}:
        parser.error("输出不能覆盖来源或审核输入")
    try:
        if args.write_report and output_path.exists():
            raise AnswerReplayError("output_exists_use_new_path")
        report = build_report(source_path, reviews_path)
        if args.write_report:
            # 排他创建：即使检查后有其他进程创建同名文件，也不会覆盖它。
            with output_path.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        print(json.dumps({"stage": "complete" if args.write_report else "plan", "summary": report["summary"], "new_model_calls": 0}, ensure_ascii=True))
        return 0
    except Exception as error:
        failure = {"stage": "failed", "error_type": type(error).__name__}
        if isinstance(error, (AnswerReplayError, AnswerEvaluationError)):
            failure["reason"] = str(error)
        print(json.dumps(failure, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
