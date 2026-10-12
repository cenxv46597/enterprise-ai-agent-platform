"""引用正确也可能丢失条件；语义结论需要独立审核记录。"""

from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.evaluation.answers import (
    AnswerEvaluationError, CaseReview, RubricCheck, evaluate_answer, summarize_answers,
)
from app.knowledge.answers import _validate_citations
from app.knowledge.retrieval import SearchHit
from app.providers.generation import GeneratedAnswer, GeneratedCitation


DOCUMENT_ID = "00000000-0000-0000-0000-000000000001"
CHUNK_ID = "00000000-0000-0000-0000-000000000002"
PUBLIC = "先暂停导入，再重启服务，执行健康检查。健康检查通过后，恢复文件导入。"
BAD_ANSWER = "先暂停导入，重启服务并执行健康检查后就恢复文件导入。"
CHECK = RubricCheck(id="healthcheck_gate", category="必要条件", description="通过后才能恢复导入", evidence=PUBLIC)


def response(answer=BAD_ANSWER, *, abstained=False):
    return {
        "answer": answer, "abstained": abstained,
        "citations": [] if abstained else [{
            "document_id": DOCUMENT_ID, "chunk_id": CHUNK_ID, "document_name": "public.md",
            "chunk_index": 1, "quote": PUBLIC, "start_char": 0, "end_char": len(PUBLIC),
        }],
    }


def evaluate(value=None, *, review=None, expected_abstained=False, checks=None):
    return evaluate_answer(
        case_id="recovery", question="什么时候恢复导入？", expected_abstained=expected_abstained,
        response=value or response(), checks=checks or [CHECK], public_text=PUBLIC,
        document_id=DOCUMENT_ID, document_name="public.md", chunk_ranges={1: (0, len(PUBLIC))}, review=review,
    )


def reviewed(base, *, verdict="不通过", kind="人工"):
    data = deepcopy(base["review"])
    data.update(reviewer_kind=kind, reviewer_name="受控测试审核者")
    reason = "答案保留检查通过后才恢复导入的前提。" if verdict == "通过" else "答案将执行检查等同于检查通过，改变了恢复导入的前提。"
    decision = {"verdict": verdict, "reason": reason}
    if verdict == "不通过":
        decision.update(answer_excerpt="执行健康检查后就恢复文件导入", evidence_excerpt="健康检查通过后，恢复文件导入。")
    data["checks"]["healthcheck_gate"] = decision
    return CaseReview.model_validate(data)


def test_real_citation_validator_accepts_answer_that_drops_required_condition() -> None:
    # 学习者先预测成功，再用现有生产函数验证；成功只说明引用校验通过。
    hit = SearchHit(UUID(CHUNK_ID), UUID(DOCUMENT_ID), "public.md", 1, PUBLIC, 0, len(PUBLIC), 0.1)
    draft = GeneratedAnswer(answer=BAD_ANSWER, abstained=False, citations=[GeneratedCitation(source_id=1, quote=PUBLIC)])
    citations = _validate_citations(draft, [hit])
    assert len(citations) == 1
    assert citations[0].quote == PUBLIC
    assert citations[0].end_char == len(PUBLIC)


def test_all_dataset_hint_terms_can_match_while_required_condition_changes() -> None:
    dataset_path = Path(__file__).resolve().parents[2] / "examples/evaluation/rag-smoke-cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    case = next(item for item in dataset["cases"] if item["id"] == "restart_recovery_steps")
    evidence = case["expected_source_texts"][0]
    bad_answer = evidence.replace("健康检查通过后", "健康检查完成后")

    # 固定题集的所有提示词仍命中，但完成检查并不保证检查通过。
    assert bad_answer != evidence
    assert all(term in bad_answer for term in case["expected_answer_terms"])
    assert "健康检查通过后" not in bad_answer
    hit = SearchHit(UUID(CHUNK_ID), UUID(DOCUMENT_ID), "demo-product.md", 1, evidence, 0, len(evidence), 0.1)
    draft = GeneratedAnswer(
        answer=bad_answer, abstained=False,
        citations=[GeneratedCitation(source_id=1, quote=evidence)],
    )
    citations = _validate_citations(draft, [hit])
    assert len(citations) == 1
    assert citations[0].quote == evidence


def test_valid_citation_does_not_automatically_pass_semantic_review() -> None:
    result = evaluate()
    assert result["citation_span_check"] == "通过"
    assert result["abstention_matches_expected"] is True
    assert result["semantic_review_state"] == "待审核"
    assert result["human_pass"] is None
    summary = summarize_answers([result])
    assert summary["human_reviewed_cases"] == 0
    assert summary["human_review_pass_fraction"] is None


def test_condition_omission_fails_after_separate_human_review() -> None:
    base = evaluate()
    result = evaluate(review=reviewed(base))
    assert result["citation_span_check"] == "通过"
    assert result["semantic_review_state"] == "人工审核完成"
    assert result["human_pass"] is False
    assert summarize_answers([result])["human_fail_cases"] == 1


def test_complete_answer_can_pass_human_review() -> None:
    good = response(PUBLIC)
    base = evaluate(good)
    result = evaluate(good, review=reviewed(base, verdict="通过"))
    assert result["human_pass"] is True


@pytest.mark.parametrize("answer,category,excerpt,reason", [
    ("先恢复文件导入，再执行健康检查，最后重启服务。", "操作顺序", "先恢复文件导入", "恢复导入被移到重启与健康检查前，改变原文的操作顺序。"),
    ("健康检查通过后恢复导入，系统还会自动退款。", "事实支持", "系统还会自动退款", "公开原文没有自动退款事实，引用不能支持这项附加结论。"),
])
def test_order_and_unsupported_fact_failures_are_recorded_separately_from_valid_citations(answer, category, excerpt, reason) -> None:
    check = CHECK.model_copy(update={"category": category})
    value = response(answer)
    base = evaluate(value, checks=[check])
    data = base["review"]
    data.update(reviewer_kind="人工", reviewer_name="受控测试审核者")
    data["checks"]["healthcheck_gate"] = {
        "verdict": "不通过", "reason": reason, "answer_excerpt": excerpt, "evidence_excerpt": PUBLIC,
    }
    result = evaluate(value, checks=[check], review=CaseReview.model_validate(data))
    assert result["citation_span_check"] == "通过"
    assert result["human_pass"] is False


def test_ai_assisted_verdict_is_excluded_from_human_score() -> None:
    base = evaluate()
    result = evaluate(review=reviewed(base, kind="AI辅助"))
    summary = summarize_answers([result])
    assert result["human_pass"] is None
    assert summary["ai_assisted_cases"] == 1
    assert summary["awaiting_human_review_cases"] == 1
    assert summary["human_review_pass_fraction"] is None


def test_partial_human_review_is_not_counted_as_complete() -> None:
    second = CHECK.model_copy(update={"id": "all_facts"})
    base = evaluate(checks=[CHECK, second])
    result = evaluate(checks=[CHECK, second], review=reviewed(base))
    assert result["semantic_review_state"] == "人工审核中"
    assert result["human_pass"] is None


def test_review_cannot_be_reused_after_answer_changes() -> None:
    base = evaluate()
    with pytest.raises(AnswerEvaluationError, match="review_answer_changed"):
        evaluate(response(PUBLIC), review=reviewed(base))


@pytest.mark.parametrize("change", [
    {"reviewer_kind": None}, {"reviewer_name": " "},
    {"checks": {"healthcheck_gate": {"verdict": "不通过", "reason": " "}}},
    {"checks": {"healthcheck_gate": {"verdict": "不通过", "reason": "有错误"}}},
    {"checks": {"healthcheck_gate": {"verdict": "待审核", "reason": "已经看过"}}},
    {"unknown": "不能忽略的字段"},
])
def test_review_requires_identity_reason_and_failure_excerpts(change) -> None:
    data = reviewed(evaluate()).model_dump()
    data.update(change)
    with pytest.raises(ValidationError):
        CaseReview.model_validate(data)


@pytest.mark.parametrize("field,value,reason", [
    ("answer_excerpt", "答案没有这段话", "review_answer_excerpt"),
    ("evidence_excerpt", "原文没有这段话", "review_evidence_excerpt"),
])
def test_review_excerpts_must_exist_in_answer_and_public_evidence(field, value, reason) -> None:
    data = reviewed(evaluate()).model_dump()
    data["checks"]["healthcheck_gate"][field] = value
    with pytest.raises(AnswerEvaluationError, match=reason):
        evaluate(review=CaseReview.model_validate(data))


def test_missing_check_does_not_turn_partial_review_into_pass() -> None:
    data = reviewed(evaluate()).model_dump()
    data["checks"] = {"wrong_check": next(iter(data["checks"].values()))}
    with pytest.raises(AnswerEvaluationError, match="review_check_set"):
        evaluate(review=CaseReview.model_validate(data))


@pytest.mark.parametrize("change", [
    {"document_id": "00000000-0000-0000-0000-000000000003"},
    {"chunk_id": "not-a-uuid"}, {"document_name": "private.md"},
    {"chunk_index": 2}, {"chunk_index": True}, {"quote": "捏造的引文"},
    {"start_char": -1}, {"end_char": len(PUBLIC) + 1},
])
def test_invalid_citation_cannot_be_accepted_as_reviewable_response(change) -> None:
    value = response()
    value["citations"][0].update(change)
    with pytest.raises(AnswerEvaluationError):
        evaluate(value)


def test_duplicate_citations_are_rejected() -> None:
    value = response()
    value["citations"] *= 2
    with pytest.raises(AnswerEvaluationError, match="duplicate_citation"):
        evaluate(value)


def test_correct_abstention_has_no_citation_pass_and_still_needs_review() -> None:
    result = evaluate(response("目前资料不足以确认答案。", abstained=True), expected_abstained=True)
    assert result["citation_span_check"] == "不适用"
    assert result["abstention_matches_expected"] is True
    assert result["human_pass"] is None


def test_wrong_abstention_decision_cannot_pass_even_if_review_marks_all_checks_passed() -> None:
    base = evaluate(expected_abstained=True)
    result = evaluate(expected_abstained=True, review=reviewed(base, verdict="通过"))
    assert result["abstention_matches_expected"] is False
    assert result["human_pass"] is False


@pytest.mark.parametrize("abstained,citations", [(False, []), (True, [response()["citations"][0]])])
def test_final_api_abstention_contract_is_checked(abstained, citations) -> None:
    value = response()
    value.update(abstained=abstained, citations=citations)
    with pytest.raises(AnswerEvaluationError, match="response_contract"):
        evaluate(value)


def test_summary_excludes_pending_case_from_pass_fraction_but_records_review_coverage() -> None:
    pending = evaluate()
    failed = evaluate(review=reviewed(pending))
    good = response(PUBLIC)
    passed = evaluate(good, review=reviewed(evaluate(good), verdict="通过"))
    failed["case_id"] = "failed"
    passed["case_id"] = "passed"
    summary = summarize_answers([pending, failed, passed])
    assert summary["human_review_pass_fraction"] == 0.5
    assert summary["human_review_coverage"] == 2 / 3
    assert summary["awaiting_human_review_cases"] == 1


def test_duplicate_cases_cannot_inflate_summary() -> None:
    with pytest.raises(AnswerEvaluationError, match="summary_cases"):
        summarize_answers([evaluate(), evaluate()])
