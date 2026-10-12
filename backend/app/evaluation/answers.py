"""离线答案审核：程序核对引用，人工逐项记录语义结论。"""

import hashlib
import json
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


Verdict = Literal["待审核", "通过", "不通过"]


class AnswerEvaluationError(ValueError):
    """固定的校验错误，不携带答案、原文或审核理由。"""


def canonical_sha256(value: Any) -> str:
    """忽略 JSON 缩进、键顺序和文件换行差异，绑定实际数据内容。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RubricCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    category: Literal["事实支持", "完整性", "必要条件", "操作顺序", "弃答依据"]
    description: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=2000)


class CheckReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    verdict: Verdict = "待审核"
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    answer_excerpt: str | None = Field(default=None, min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.verdict == "待审核":
            if any(value is not None for value in (self.reason, self.answer_excerpt, self.evidence_excerpt)):
                raise ValueError("待审核项不能包含已完成的判定")
        elif not self.reason or not self.reason.strip():
            raise ValueError("已审核项必须说明理由")
        elif self.verdict == "不通过" and (not self.answer_excerpt or not self.evidence_excerpt):
            raise ValueError("不通过项必须定位答案片段与对照原文")
        return self


class CaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(min_length=1, max_length=80)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_kind: Literal["人工", "AI辅助"] | None = None
    reviewer_name: str | None = Field(default=None, min_length=1, max_length=100)
    checks: dict[str, CheckReview] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_reviewer(self) -> Self:
        has_decision = any(item.verdict != "待审核" for item in self.checks.values())
        if has_decision and (
            self.reviewer_kind is None or not self.reviewer_name or not self.reviewer_name.strip()
        ):
            raise ValueError("已审核项必须记录审核者身份与名称")
        if not has_decision and (self.reviewer_kind is not None or self.reviewer_name is not None):
            raise ValueError("尚未审核的记录不填写审核者")
        return self


class RecordedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    chunk_id: str
    document_id: str
    document_name: str = Field(min_length=1, max_length=255)
    chunk_index: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=1000)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)


class RecordedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    answer: str = Field(min_length=1, max_length=4000)
    abstained: bool
    citations: list[RecordedCitation] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        # 这里是最终 API 响应，弃答含固定提示，区别于空答案的生成草稿。
        if not self.answer.strip() or self.abstained == bool(self.citations):
            raise ValueError("最终回答与引用契约不一致")
        return self


def evaluate_answer(
    *,
    case_id: str,
    question: str,
    expected_abstained: bool,
    response: dict,
    checks: list[RubricCheck],
    public_text: str,
    document_id: str,
    document_name: str,
    chunk_ranges: dict[int, tuple[int, int]],
    review: CaseReview | None = None,
) -> dict:
    """不推断语义结论；检查引用与人工记录是否绑定同一实际答案。"""
    if type(expected_abstained) is not bool or not case_id or not question.strip():
        raise AnswerEvaluationError("case_identity")
    if not checks or len({item.id for item in checks}) != len(checks):
        raise AnswerEvaluationError("rubric_checks")
    if any(item.evidence not in public_text for item in checks):
        raise AnswerEvaluationError("rubric_evidence")
    try:
        answer = RecordedAnswer.model_validate(response)
        seen = set()
        for citation in answer.citations:
            if any(str(UUID(value)) != value for value in (citation.chunk_id, citation.document_id)):
                raise AnswerEvaluationError("citation_identity")
            bounds = chunk_ranges.get(citation.chunk_index)
            if (
                citation.document_id != document_id or citation.document_name != document_name
                or bounds is None or not bounds[0] <= citation.start_char < citation.end_char <= bounds[1]
                or public_text[citation.start_char:citation.end_char] != citation.quote
            ):
                raise AnswerEvaluationError("citation_span")
            key = (citation.chunk_id, citation.quote)
            if key in seen:
                raise AnswerEvaluationError("duplicate_citation")
            seen.add(key)
    except AnswerEvaluationError:
        raise
    except (ValueError, TypeError):
        raise AnswerEvaluationError("response_contract") from None

    identity = canonical_sha256({
        "case_id": case_id, "question": question,
        "expected_abstained": expected_abstained, "response": response,
    })
    if review is None:
        review = CaseReview(
            case_id=case_id, answer_sha256=identity,
            checks={item.id: CheckReview() for item in checks},
        )
    if review.case_id != case_id or review.answer_sha256 != identity:
        raise AnswerEvaluationError("review_answer_changed")
    if set(review.checks) != {item.id for item in checks}:
        raise AnswerEvaluationError("review_check_set")
    for decision in review.checks.values():
        if decision.answer_excerpt is not None and decision.answer_excerpt not in answer.answer:
            raise AnswerEvaluationError("review_answer_excerpt")
        if decision.evidence_excerpt is not None and decision.evidence_excerpt not in public_text:
            raise AnswerEvaluationError("review_evidence_excerpt")
    complete = all(item.verdict != "待审核" for item in review.checks.values())
    state = "待审核"
    human_pass = None
    abstention_matches = answer.abstained == expected_abstained
    if review.reviewer_kind == "AI辅助":
        state = "AI辅助复核"
    elif review.reviewer_kind == "人工":
        state = "人工审核完成" if complete else "人工审核中"
        if complete:
            human_pass = abstention_matches and all(item.verdict == "通过" for item in review.checks.values())
    return {
        "case_id": case_id, "question": question, "expected_abstained": expected_abstained,
        "response": response, "citation_span_check": "不适用" if answer.abstained else "通过",
        "abstention_matches_expected": abstention_matches,
        "rubric_checks": [item.model_dump() for item in checks],
        "review": review.model_dump(), "semantic_review_state": state, "human_pass": human_pass,
    }


def summarize_answers(rows: list[dict]) -> dict:
    """未完成的人工审核与 AI 辅助判定均不进入人工质量分母。"""
    if not rows or len({row["case_id"] for row in rows}) != len(rows):
        raise AnswerEvaluationError("summary_cases")
    reviewed = [row for row in rows if row["human_pass"] is not None]
    passed = sum(row["human_pass"] is True for row in reviewed)
    return {
        "case_count": len(rows),
        "answered_cases": sum(not row["response"]["abstained"] for row in rows),
        "abstained_cases": sum(row["response"]["abstained"] for row in rows),
        "citation_span_pass_cases": sum(row["citation_span_check"] == "通过" for row in rows),
        "citation_span_not_applicable_cases": sum(row["citation_span_check"] == "不适用" for row in rows),
        "abstention_matches_expected_cases": sum(row["abstention_matches_expected"] for row in rows),
        "human_reviewed_cases": len(reviewed), "human_pass_cases": passed,
        "human_fail_cases": len(reviewed) - passed,
        "awaiting_human_review_cases": len(rows) - len(reviewed),
        "ai_assisted_cases": sum(row["semantic_review_state"] == "AI辅助复核" for row in rows),
        "human_review_coverage": len(reviewed) / len(rows),
        "human_review_pass_fraction": passed / len(reviewed) if reviewed else None,
    }
