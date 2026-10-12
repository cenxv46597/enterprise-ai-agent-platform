"""默认规划固定公开输入的两版提示词实验；模拟与真实执行显式分开。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from app.evaluation.prompt_comparison import (
    CANDIDATE_SUFFIX, ComparisonCase, PromptComparisonError, SNAPSHOT_VERSION,
    build_trials, prepare_case, run_comparison,
)
from app.evaluation.answers import RubricCheck
from app.providers.compatible_chat import CompatibleJsonChatClient, JsonChatResult, JsonChatTimeoutError
from app.providers.generation_snapshot import SnapshotInput, digest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPOSITORY_ROOT / "docs/evaluation"
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples/evaluation"
SOURCE_REPORT = EXAMPLE_ROOT / "generation-request-controlled.json"
DATASET_PATH = EXAMPLE_ROOT / "rag-smoke-cases.json"
CORPUS_PATH = REPOSITORY_ROOT / "examples/knowledge/demo-product.md"
RUBRIC_PATH = EXAMPLE_ROOT / "answer-review-rubric.json"
REGRESSION_CHECKLIST_PATH = EXAMPLE_ROOT / "prompt-regression-checklist.json"


def read_json(path: Path) -> dict:
    if path.stat().st_size > 2_000_000:
        raise PromptComparisonError("input_size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromptComparisonError("input_object")
    return value


def regression_acceptance(dataset: dict, public_text: str) -> list[dict]:
    """复用已有语义标准，防止全题回归遗漏必要条件或更换题集。"""
    checklist, rubric = read_json(REGRESSION_CHECKLIST_PATH), read_json(RUBRIC_PATH)
    public_digest = hashlib.sha256(public_text.encode("utf-8")).hexdigest()
    if (
        checklist.get("version") != "prompt-regression-checklist-v1"
        or checklist.get("dataset_canonical_sha256") != digest(dataset)
        or checklist.get("public_text_sha256") != public_digest
        or checklist.get("rubric_canonical_sha256") != digest(rubric)
        or rubric.get("source_dataset_canonical_sha256") != digest(dataset)
        or rubric.get("corpus_text_sha256") != public_digest
    ):
        raise PromptComparisonError("regression_checklist_identity")
    try:
        selected = {row["case_id"]: row for row in checklist["cases"]}
        definitions = {row["case_id"]: row for row in rubric["cases"]}
        fixed_ids = {case["id"] for case in dataset["cases"]}
        if (
            len(selected) != len(checklist["cases"])
            or len(definitions) != len(rubric["cases"])
            or set(selected) != fixed_ids or set(definitions) != fixed_ids
        ):
            raise PromptComparisonError("regression_case_set")
        result = []
        for case in dataset["cases"]:
            checks = [RubricCheck.model_validate(row) for row in definitions[case["id"]]["checks"]]
            critical_ids = selected[case["id"]]["critical_check_ids"]
            if (
                not checks or not isinstance(critical_ids, list)
                or any(not isinstance(value, str) for value in critical_ids)
                or len(set(critical_ids)) != len(critical_ids)
                or len({check.id for check in checks}) != len(checks)
                or set(critical_ids) != {check.id for check in checks}
                or any(check.evidence not in public_text for check in checks)
            ):
                raise PromptComparisonError("regression_check_set")
            result.append({
                "case_id": case["id"], "question": case["question"],
                "expected_abstained": case["expected_abstained"],
                "checks": [
                    {**check.model_dump(exclude={"evidence"}),
                     "evidence_sha256": hashlib.sha256(check.evidence.encode("utf-8")).hexdigest()}
                    for check in checks
                ],
            })
        return result
    except PromptComparisonError:
        raise
    except (KeyError, ValueError, TypeError):
        raise PromptComparisonError("regression_checklist_contract") from None


def prepare(
    source_path: Path, case_ids: list[str], max_calls: int, *,
    all_cases: bool = False, allow_missing: bool = False,
) -> tuple[list[ComparisonCase], dict]:
    """不读配置或创建网络客户端；先核对固定语料、题目与调用预算。"""
    source, dataset = read_json(source_path), read_json(DATASET_PATH)
    public_text = CORPUS_PATH.read_text(encoding="utf-8-sig")
    if (
        source.get("generation_capture_version") != SNAPSHOT_VERSION
        or source.get("dataset_version") != dataset["dataset_version"]
        or source.get("corpus_sha256") != hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()
    ):
        raise PromptComparisonError("source_identity")
    controlled = source.get("controlled_fixture") is True
    if not controlled and source.get("execution") != "TestClient 调用真实路由、本机数据库与供应商 API":
        raise PromptComparisonError("source_kind")
    fixed_cases = {case["id"]: case for case in dataset["cases"]}
    if len(fixed_cases) != len(dataset["cases"]):
        raise PromptComparisonError("dataset_cases")
    if all_cases and case_ids:
        raise PromptComparisonError("selected_case_set")
    selected = list(fixed_cases) if all_cases else case_ids or ["restart_recovery_steps"]
    if len(set(selected)) != len(selected) or not set(selected) <= set(fixed_cases):
        raise PromptComparisonError("selected_case_set")
    if type(max_calls) is not int or not 1 <= max_calls <= 20 or len(selected) * 2 > max_calls:
        raise PromptComparisonError("call_budget")
    acceptance = regression_acceptance(dataset, public_text) if all_cases else None
    captured = source.get("prepared_generation_requests")
    if not isinstance(captured, list) or len(captured) > 100 or (not captured and not allow_missing):
        raise PromptComparisonError("missing_snapshots")
    cases, missing = [], []
    for case_id in selected:
        matching = [row for row in captured if isinstance(row, dict) and row.get("case_id") == case_id]
        if not matching and allow_missing:
            missing.append(case_id)
            continue
        if len(matching) != 1:
            raise PromptComparisonError("snapshot_selection_requires_one_per_case")
        case = fixed_cases[case_id]
        cases.append(prepare_case(case_id, case["question"], case["expected_abstained"], matching[0].get("snapshot"), public_text))
    # 两版请求也在预检时建立，防止已发出一版后才发现另一版输入无效。
    if cases:
        build_trials(cases)
    return cases, {
        "source_canonical_sha256": digest(source),
        "dataset_canonical_sha256": digest(dataset),
        "source_kind": "受控构造；不是历史真实请求" if controlled else "真实联调脚本记录；摘要不证明历史真实性",
        "controlled_fixture": controlled, "planned_requests": len(selected) * 2,
        "case_ids": selected, "available_case_ids": [case.case_id for case in cases],
        "missing_case_ids": missing, "ready_for_generation": not missing,
        "real_run_allowed": not controlled and not missing,
        "variants": ["baseline", "candidate"],
        **({"regression_acceptance": acceptance} if acceptance is not None else {}),
    }


def configured_clients(cases: list[ComparisonCase]) -> dict:
    from app.core.config import Settings
    settings = Settings()
    if not settings.generation_base_url or not settings.generation_api_key or not settings.generation_model:
        raise PromptComparisonError("generation_configuration_missing")
    clients = {}
    for case in cases:
        body = case.snapshot["request_body"]
        if settings.generation_model.strip() != body["model"]:
            raise PromptComparisonError("request_model_changed")
        clients[case.case_id] = CompatibleJsonChatClient(
            settings.generation_base_url, settings.generation_api_key, body["model"],
            settings.generation_timeout_seconds, settings.generation_total_timeout_seconds,
            max_tokens=body["max_tokens"],
        )
    return clients


class ControlledClient:
    """固定返回脚本内容，演示引用真实但条件可被改写；绝非实际模型效果。"""

    def __init__(self, timeout_variant: str | None):
        self.timeout_variant = timeout_variant

    def complete_request(self, body: dict) -> JsonChatResult:
        variant = "candidate" if body["messages"][0]["content"].endswith(CANDIDATE_SUFFIX) else "baseline"
        if variant == self.timeout_variant:
            raise JsonChatTimeoutError("受控超时")
        data = SnapshotInput.model_validate(json.loads(body["messages"][1]["content"]))
        quote = data.sources[0].content[:1000].strip()
        answer = quote.replace("检查通过后", "检查完成后") if variant == "baseline" else quote
        return JsonChatResult({
            "answer": answer, "abstained": False,
            "citations": [{"source_id": data.sources[0].source_id, "quote": quote}],
        }, "controlled-mock-no-network", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="计划不读凭据；--mock 演练程序；--run 重放真实公开快照并产生模型请求。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--source-report", default=str(SOURCE_REPORT))
    parser.add_argument("--report", default=str(REPORT_ROOT / "prompt-comparison-local.json"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case-id", action="append", default=[])
    selection.add_argument("--all-cases", action="store_true", help="选择固定题集全部六题；两版需显式允许 12 次尝试")
    parser.add_argument("--max-calls", type=int, default=2, help="本轮最多允许多少次生成调用尝试，默认一题两版")
    parser.add_argument("--mock-timeout", choices=["baseline", "candidate"], help="仅模拟模式，在指定版注入一次超时")
    args = parser.parse_args()
    source_path, output_path = Path(args.source_report).resolve(), Path(args.report).resolve()
    if source_path.suffix != ".json" or not any(source_path.is_relative_to(root.resolve()) for root in (REPORT_ROOT, EXAMPLE_ROOT)):
        parser.error("来源须为 docs/evaluation 或 examples/evaluation 内的 JSON 文件")
    if output_path.suffix != ".json" or not output_path.is_relative_to(REPORT_ROOT.resolve()) or source_path == output_path:
        parser.error("输出须为 docs/evaluation 内的新 JSON 文件，不可覆盖来源")
    if args.mock_timeout and not args.mock:
        parser.error("--mock-timeout 仅用于 --mock")
    try:
        if not args.plan and output_path.exists():
            raise PromptComparisonError("output_exists_use_new_path")
        cases, provenance = prepare(
            source_path, args.case_id, args.max_calls,
            all_cases=args.all_cases, allow_missing=args.plan,
        )
        if args.plan:
            print(json.dumps({"stage": "plan", **provenance, "new_model_calls": 0, "quality_pass_fraction": None}, ensure_ascii=True))
            return 0
        if args.run and provenance["controlled_fixture"]:
            raise PromptComparisonError("controlled_input_cannot_run_real_models")
        clients = configured_clients(cases) if args.run else {case.case_id: ControlledClient(args.mock_timeout) for case in cases}
        with output_path.open("x", encoding="utf-8") as handle:
            def checkpoint(report: dict):
                report["provenance"] = provenance
                handle.seek(0)
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.truncate()
                handle.flush()
            report = run_comparison(cases, clients, "真实模型" if args.run else "受控模拟", checkpoint=checkpoint)
        print(json.dumps({"stage": "complete", "execution_kind": report["execution_kind"], "summary": report["summary"]}, ensure_ascii=True))
        return 1 if report["summary"]["execution_failed_count"] else 0
    except Exception as error:
        failure = {"stage": "failed", "error_type": type(error).__name__}
        if isinstance(error, PromptComparisonError):
            failure["reason"] = str(error)
        print(json.dumps(failure, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
