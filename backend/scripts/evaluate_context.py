"""离线回放公开检索快照，评测不同正文预算下的上下文证据覆盖。"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

from app.evaluation.context import evaluate_context
from app.evaluation.retrieval import EvaluationCase, evaluate_case, prepare_cases
from app.knowledge.context import DEFAULT_CONTEXT_CHARACTERS, MAX_CONTEXT_CHARACTERS
from evaluate_retrieval import DATASET_PATH, load_dataset
from live_rag_smoke import emit, save_report


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPOSITORY_ROOT / "docs/evaluation"
SOURCE_REPORT = REPORT_ROOT / "retrieval-eval-2026-10-10.json"
OUTPUT_REPORT = REPORT_ROOT / "context-eval-2026-10-11.json"
POLICY = "按检索排名保留完整块；跳过超出剩余预算的块，不截断、不重排"


class ContextReplayError(ValueError):
    """只使用固定错误阶段，不包含快照正文或原始异常。"""


@dataclass(frozen=True)
class Snapshot:
    cases: tuple[EvaluationCase, ...]
    sources: dict[str, dict[str, str]]
    hits_by_case: dict[str, list[dict[str, Any]]]
    sha256: str
    identity: dict[str, Any]
    candidate_count: int


def load_snapshot(path: Path) -> Snapshot:
    dataset, fixtures, expected_identity = load_dataset(DATASET_PATH)
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ContextReplayError("snapshot_size")
    report = json.loads(raw)
    if not isinstance(report, dict) or "failure" in report:
        raise ContextReplayError("snapshot_incomplete")
    identity = report.get("identity")
    if not isinstance(identity, dict) or set(identity) != set(expected_identity) | {"embedding_profile_sha256"}:
        raise ContextReplayError("snapshot_identity")
    if any(identity[key] != value for key, value in expected_identity.items()):
        raise ContextReplayError("snapshot_identity")
    digest = identity["embedding_profile_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ContextReplayError("snapshot_identity")
    documents = report.get("documents")
    if not isinstance(documents, dict) or set(documents) != {fixture.name for fixture in fixtures}:
        raise ContextReplayError("snapshot_documents")
    sources = {}
    for fixture in fixtures:
        record = documents[fixture.name]
        document_id = str(UUID(record["document_id"]))
        if (
            document_id in sources or record["document_id"] != document_id
            or record["sha256"] != fixture.sha256 or record["chunk_count"] != fixture.chunk_count
        ):
            raise ContextReplayError("snapshot_documents")
        sources[document_id] = {"name": fixture.name, "text": fixture.content}
    candidate_count = sum(fixture.chunk_count for fixture in fixtures)
    if report.get("candidate_count") != candidate_count:
        raise ContextReplayError("snapshot_candidate_count")
    cases = prepare_cases(dataset["cases"], sources)
    by_id = {case.case_id: case for case in cases}
    attempts = report.get("attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 100:
        raise ContextReplayError("snapshot_attempts")
    latest = {}
    for attempt in attempts:
        if not isinstance(attempt, dict) or attempt.get("case_id") not in by_id:
            raise ContextReplayError("snapshot_case")
        case = by_id[attempt["case_id"]]
        hits = attempt.get("results")
        if (
            attempt.get("question") != case.question or attempt.get("answerable") is not case.answerable
            or attempt.get("generation_called") is not False or attempt.get("request_top_k") != 10
            or not isinstance(hits, list) or len(hits) != min(10, candidate_count)
        ):
            raise ContextReplayError("snapshot_case")
        # 全部历史候选先通过公开来源校验，不仅验证最后一次尝试。
        evaluations = [evaluate_case(case, hits, sources, top_k=k).to_dict() for k in (5, 10)]
        if evaluations != attempt.get("evaluations"):
            raise ContextReplayError("snapshot_score_changed")
        latest[case.case_id] = hits
    if set(latest) != set(by_id):
        raise ContextReplayError("snapshot_incomplete")
    # Git 会规范化换行；对规范 JSON 求摘要，使 Windows 与克隆后的快照可比。
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return Snapshot(cases, sources, latest, hashlib.sha256(canonical).hexdigest(), identity, candidate_count)


def build_report(snapshot: Snapshot, *, top_ks: list[int], budgets: list[int], source_path: Path) -> dict:
    results = []
    summary = []
    for top_k in top_ks:
        for budget in budgets:
            evaluations = [
                evaluate_context(case, snapshot.hits_by_case[case.case_id], snapshot.sources,
                                 top_k=top_k, max_context_characters=budget).to_dict()
                for case in snapshot.cases
            ]
            results.extend(evaluations)
            positive = [row for row in evaluations if row["retrieval_evidence"]["answerable"]]
            expected = sum(row["retrieval_evidence"]["expected_evidence_count"] for row in positive)
            retrieval_covered = sum(row["retrieval_evidence"]["covered_evidence_count"] for row in positive)
            context_covered = sum(row["context_evidence"]["covered_evidence_count"] for row in positive)
            summary.append({
                "top_k": top_k, "max_context_characters": budget,
                "answerable_cases": len(positive), "required_evidence_count": expected,
                "retrieval_covered_evidence_count": retrieval_covered,
                "context_covered_evidence_count": context_covered,
                "context_evidence_hit_fraction": context_covered / expected if expected else None,
                "all_context_evidence_cases": sum(row["context_evidence"]["all_evidence_found"] for row in positive),
                "lost_evidence_count": retrieval_covered - context_covered,
                "no_block_fits_cases": sum(row["selection_status"] == "no_block_fits" for row in evaluations),
                "negative_cases_excluded_from_evidence_score": len(evaluations) - len(positive),
            })
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution": "离线回放已保存的真实检索候选，复用业务上下文选择函数",
        "source_report": source_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "source_report_canonical_sha256": snapshot.sha256, "source_identity": snapshot.identity,
        "source_hash_scope": "按键排序的紧凑 UTF-8 JSON；忽略文件换行和缩进差异",
        "candidate_count": snapshot.candidate_count, "case_count": len(snapshot.cases),
        "policy": POLICY, "default_context_characters": DEFAULT_CONTEXT_CHARACTERS,
        "budget_scope": "仅文本块正文字符，不包含问题、提示词、JSON 开销或模型 token",
        "rank_scope": "selected_sources 保留原检索排名；上下文 first_complete_rank 为选中列表的位置",
        "scope": "仅黄金证据覆盖；没有调用模型，不评价真实答案忠实性或弃答，不测 token、费用和耗时",
        "new_model_calls": 0, "generation_called": False, "results": results, "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检查上下文证据覆盖，不调用数据库或模型服务。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="验证快照并显示实验规模，不写文件")
    mode.add_argument("--write-report", action="store_true", help="回放选择规则并写入新的公开报告")
    parser.add_argument("--retrieval-report", default=str(SOURCE_REPORT))
    parser.add_argument("--report", default=str(OUTPUT_REPORT))
    parser.add_argument("--budgets", nargs="+", type=int, default=[600, 2000, DEFAULT_CONTEXT_CHARACTERS])
    parser.add_argument("--top-k", nargs="+", type=int, choices=[5, 10], default=[5, 10])
    args = parser.parse_args()
    source_path, output_path = Path(args.retrieval_report).resolve(), Path(args.report).resolve()
    if any(path.suffix != ".json" or not path.is_relative_to(REPORT_ROOT.resolve()) for path in (source_path, output_path)):
        parser.error("输入和输出须为 docs/evaluation 内的 .json 文件")
    if source_path == output_path or any(not 1 <= budget <= MAX_CONTEXT_CHARACTERS for budget in args.budgets):
        parser.error("输出不能覆盖检索快照；正文预算须为 1 至 50000 的整数")
    budgets, top_ks = list(dict.fromkeys(args.budgets)), list(dict.fromkeys(args.top_k))
    if len(budgets) > 10:
        parser.error("一次最多比较 10 种预算")
    try:
        snapshot = load_snapshot(source_path)
        if args.plan:
            emit({"stage": "plan", "case_count": len(snapshot.cases), "candidate_count": snapshot.candidate_count,
                  "top_k": top_ks, "budgets": budgets, "evaluation_count": len(snapshot.cases) * len(top_ks) * len(budgets),
                  "new_model_calls": 0, "generation_called": False})
            return 0
        if output_path.exists():
            raise ContextReplayError("output_exists_use_new_path")
        report = build_report(snapshot, top_ks=top_ks, budgets=budgets, source_path=source_path)
        save_report(output_path, report)
        emit({"stage": "complete", "summary": report["summary"], "new_model_calls": 0})
        return 0
    except Exception as exc:
        failure = {"stage": "failed", "error_type": type(exc).__name__}
        if isinstance(exc, ContextReplayError):
            failure["reason"] = str(exc)
        emit(failure)
        return 2


if __name__ == "__main__":
    sys.exit(main())
