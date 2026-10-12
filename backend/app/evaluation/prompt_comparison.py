"""固定公开生成输入的提示词实验；执行与引用检查不代替语义审核。"""

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from time import monotonic
from typing import Literal, Protocol

from pydantic import ValidationError

from app.providers.compatible_chat import JsonChatError, JsonChatResult, JsonChatTimeoutError
from app.providers.generation import GeneratedAnswer
from app.providers.generation_snapshot import GenerationSnapshotError, SnapshotInput, capture_generation_request, digest
from app.providers.review import TokenUsage


CANDIDATE_SUFFIX = """
输出前逐项核对：每个事实是否有给定资料支持，必要条件是否完整，操作顺序是否保留。
“执行完检查”不等于“检查通过”；不要改变原文中的前提、限制、时间或禁止事项。
整理成便于理解的步骤；证据不足时仍按原 JSON 契约弃答。"""
SNAPSHOT_VERSION = "generation-request-snapshot-v1"


class PromptComparisonError(ValueError):
    """只使用固定错误代码，不携带输入或服务响应。"""


@dataclass(frozen=True, slots=True)
class ComparisonCase:
    case_id: str
    expected_abstained: bool
    snapshot: dict


class RequestReplayClient(Protocol):
    def complete_request(self, request_body: dict) -> JsonChatResult: ...


def prepare_case(case_id: str, question: str, expected_abstained: bool, snapshot: dict, public_text: str) -> ComparisonCase:
    """重算输入与摘要，拒绝缺失或发生变化的快照。摘要不证明历史真实性。"""
    try:
        if not isinstance(case_id, str) or not 1 <= len(case_id) <= 80 or type(expected_abstained) is not bool:
            raise PromptComparisonError("case_identity")
        if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
            raise PromptComparisonError("snapshot_version")
        verified = capture_generation_request(snapshot["request_body"], public_text, question)
        for key in ("request_body_sha256", "input_sha256", "system_prompt_sha256", "public_text_sha256", "source_count", "context_characters"):
            if type(snapshot.get(key)) is not type(verified[key]) or snapshot.get(key) != verified[key]:
                raise PromptComparisonError("snapshot_changed")
        return ComparisonCase(case_id, expected_abstained, deepcopy(snapshot))
    except PromptComparisonError:
        raise
    except (GenerationSnapshotError, KeyError, TypeError, ValueError, RecursionError):
        raise PromptComparisonError("snapshot_contract") from None


def fixed_request_digest(body: dict) -> str:
    fixed = deepcopy(body)
    fixed["messages"][0]["content"] = ""
    return digest(fixed)


def build_trials(cases: Sequence[ComparisonCase]) -> list[dict]:
    """保留原始 user 消息字符串；两版只改变 system.content。"""
    if not 1 <= len(cases) <= 10 or len({case.case_id for case in cases}) != len(cases):
        raise PromptComparisonError("case_set")
    rows = []
    for case in cases:
        original = case.snapshot["request_body"]
        if digest(original) != case.snapshot["request_body_sha256"]:
            raise PromptComparisonError("snapshot_changed")
        baseline = original["messages"][0]["content"]
        for variant_id, prompt in (("baseline", baseline), ("candidate", baseline + CANDIDATE_SUFFIX)):
            body = deepcopy(original)
            body["messages"][0]["content"] = prompt
            if len(prompt) > 200000 or fixed_request_digest(body) != fixed_request_digest(original):
                raise PromptComparisonError("control_changed")
            rows.append({
                "case_id": case.case_id, "variant_id": variant_id,
                "input_sha256": case.snapshot["input_sha256"],
                "source_request_sha256": case.snapshot["request_body_sha256"],
                "fixed_request_sha256": fixed_request_digest(body),
                "system_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "request_body_sha256": digest(body), "request_body": body,
                "expected_abstained": case.expected_abstained,
                "execution": {"status": "待执行"}, "response": None,
                "citations_valid": None, "abstention_matches_expected": None,
                "semantic_review": "待审核", "quality_pass": None,
            })
    return rows


def validate_draft(content: dict, body: dict) -> GeneratedAnswer:
    """引文须存在于指定的本次资料，不能借用完整文档中未提供的文字。"""
    try:
        answer = GeneratedAnswer.model_validate(content)
        data = SnapshotInput.model_validate(json.loads(body["messages"][1]["content"]))
        sources = {source.source_id: source.content for source in data.sources}
        seen = set()
        for citation in answer.citations:
            pair = (citation.source_id, citation.quote)
            if citation.source_id not in sources or citation.quote not in sources[citation.source_id] or pair in seen:
                raise PromptComparisonError("invalid_citation")
            seen.add(pair)
        return answer
    except PromptComparisonError:
        raise
    except (ValidationError, ValueError, TypeError, KeyError, RecursionError):
        raise PromptComparisonError("invalid_answer_contract") from None


def summarize(report: dict) -> dict:
    rows = report["trials"]
    attempted = [row for row in rows if row["execution"]["status"] != "待执行"]
    valid = [row for row in rows if row["execution"]["status"] == "成功"]
    # 收到响应后草稿校验失败，也可能已经报告消耗，不能只统计有效草稿。
    known_usage = [row["execution"]["usage"] for row in rows if row["execution"].get("usage") is not None]
    paired = sum(all(row["execution"]["status"] == "成功" for row in rows if row["case_id"] == case_id) for case_id in report["case_ids"])
    return {
        "selected_case_count": len(report["case_ids"]), "planned_requests": len(rows),
        "attempted_requests": len(attempted), "valid_response_count": len(valid),
        "received_response_count": sum(row["execution"].get("response_received") is True for row in rows),
        "execution_failed_count": sum(row["execution"]["status"] == "失败" for row in rows),
        "complete_pair_count": paired, "pair_execution_coverage": paired / len(report["case_ids"]),
        "new_model_calls": len(attempted) if report["execution_kind"] == "真实模型" else 0,
        "by_variant": {variant: {
            "planned": sum(row["variant_id"] == variant for row in rows),
            "attempted": sum(row["variant_id"] == variant for row in attempted),
            "valid": sum(row["variant_id"] == variant for row in valid),
            "failed": sum(row["variant_id"] == variant and row["execution"]["status"] == "失败" for row in rows),
        } for variant in ("baseline", "candidate")},
        "known_usage_totals": {key: sum(item[key] for item in known_usage) for key in ("prompt_tokens", "completion_tokens", "total_tokens")} if known_usage else None,
        "usage_reported_trials": len(known_usage), "cost": None,
        "quality_pass_fraction": None, "winner": None,
    }


def run_comparison(
    cases: Sequence[ComparisonCase], clients: dict[str, RequestReplayClient],
    execution_kind: Literal["真实模型", "受控模拟"], *, checkpoint: Callable[[dict], None] | None = None,
) -> dict:
    """逐请求记录；任一版失败继续，绝不自动重试或选择赢家。"""
    if execution_kind not in ("真实模型", "受控模拟") or set(clients) != {case.case_id for case in cases}:
        raise PromptComparisonError("execution_plan")
    report = {
        "version": "prompt-comparison-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_kind": execution_kind, "run_state": "运行中", "automatic_retries": 0,
        "scope": "固定公开输入，只改变系统提示词；引用与执行结果不等于答案质量",
        "case_ids": [case.case_id for case in cases], "trials": build_trials(cases),
        "winner_note": "语义审核未完成，不自动选择提示词；同一模型和参数不保证结果完全确定",
    }

    def refresh():
        report["summary"] = summarize(report)
        if checkpoint is not None:
            # 文件保存失败须中止，不能吞成一次模型故障后继续发送。
            checkpoint(report)

    refresh()
    for row in report["trials"]:
        row["execution"] = {"status": "执行中", "remote_execution": "未知"}
        refresh()
        started = monotonic()
        received = {}
        try:
            result = clients[row["case_id"]].complete_request(deepcopy(row["request_body"]))
            if not isinstance(result, JsonChatResult) or not isinstance(result.model, str) or not 1 <= len(result.model.strip()) <= 200:
                raise PromptComparisonError("response_metadata")
            usage, usage_state = None, "服务未报告"
            if result.usage is not None:
                try:
                    usage = TokenUsage.model_validate(result.usage).model_dump()
                    usage_state = "已报告"
                except ValidationError:
                    usage_state = "返回用量格式无效"
            received = {
                "response_received": True, "remote_execution": "已收到模型响应",
                "response_model": result.model.strip(), "usage": usage, "usage_state": usage_state,
            }
            answer = validate_draft(result.content, row["request_body"])
            row.update(response=answer.model_dump(), citations_valid=True,
                       abstention_matches_expected=answer.abstained == row["expected_abstained"])
            row["execution"] = {
                "status": "成功", **received, "seconds": round(monotonic() - started, 4),
            }
        except Exception as error:
            category = "timeout" if isinstance(error, JsonChatTimeoutError) else "invalid_response" if isinstance(error, PromptComparisonError) else "provider_error" if isinstance(error, JsonChatError) else "unexpected_error"
            row["execution"] = {
                "status": "失败", "error_category": category, "remote_execution": "未知",
                **received,
                "seconds": round(monotonic() - started, 4),
            }
        refresh()
    report["run_state"] = "已完成"
    refresh()
    return report
