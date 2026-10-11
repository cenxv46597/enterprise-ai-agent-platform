"""验证证据被预算排除、完整块跳过和引用来源重新编号等真实风险。"""

from dataclasses import dataclass

import pytest

from app.evaluation.context import evaluate_context
from app.evaluation.retrieval import RetrievalEvaluationError, prepare_cases
from app.knowledge.context import AnswerContextLimitError, select_context


GOLD = "健康检查通过后恢复导入。"
PRIVATE = "PRIVATE-CONTENT-MUST-NOT-BE-REPORTED"


def scenario(*, answerable: bool = True):
    content = "观" * 500 + GOLD + "恢" * (500 - len(GOLD)) + "日" * 80
    sources = {"doc-a": {"name": "公开说明.md", "text": content}}
    case = prepare_cases([{
        "id": "recovery", "question": "如何恢复导入？", "answerable": answerable,
        "required_evidence": [{"document_name": "公开说明.md", "text": GOLD}] if answerable else [],
    }], sources)[0]
    hits = [{
        "document_id": "doc-a", "document_name": "公开说明.md", "chunk_id": f"chunk-{index}",
        "chunk_index": index, "start_char": start, "end_char": end,
        "content": content[start:end], "distance": index / 10,
    } for index, (start, end) in enumerate(((0, 500), (500, 1000), (1000, 1080)), 1)]
    return case, hits, sources


def test_retrieval_hit_can_be_lost_from_generation_context_under_budget():
    case, hits, sources = scenario()
    result = evaluate_context(case, hits, sources, top_k=3, max_context_characters=600)
    assert result.retrieval_evidence.all_evidence_found is True
    assert result.context_evidence.all_evidence_found is False
    assert result.lost_evidence_count == 1
    assert [source.retrieval_rank for source in result.selected_sources] == [1, 3]
    assert [source.source_id for source in result.selected_sources] == [1, 2]
    assert result.used_characters == 580 and result.dropped_candidate_count == 1


def test_exact_budget_restores_complete_evidence_without_truncating_blocks():
    case, hits, sources = scenario()
    result = evaluate_context(case, hits, sources, top_k=3, max_context_characters=1000)
    assert result.context_evidence.all_evidence_found is True and result.lost_evidence_count == 0
    assert result.used_characters == 1000
    assert [source.retrieval_rank for source in result.selected_sources] == [1, 2]
    assert result.context_evidence.evidence[0].first_complete_rank == 2


def test_selection_does_not_use_golden_evidence_to_cheat():
    case, hits, sources = scenario()
    positive = evaluate_context(case, hits, sources, top_k=3, max_context_characters=600)
    negative_case, _, _ = scenario(answerable=False)
    negative = evaluate_context(negative_case, hits, sources, top_k=3, max_context_characters=600)
    assert positive.selected_sources == negative.selected_sources
    assert negative.context_evidence.all_evidence_found is None
    assert negative.context_evidence.evidence_hit_fraction is None


def test_top_k_limits_candidates_before_context_selection():
    case, hits, sources = scenario()
    result = evaluate_context(case, hits, sources, top_k=1, max_context_characters=12000)
    assert result.retrieval_evidence.all_evidence_found is False
    assert result.context_evidence.all_evidence_found is False
    assert result.lost_evidence_count == 0 and result.used_characters == 500


@pytest.mark.parametrize("budget", [0, -1, 50001, True, 600.0, "600"])
def test_invalid_budgets_are_rejected(budget):
    case, hits, sources = scenario()
    with pytest.raises(AnswerContextLimitError):
        evaluate_context(case, hits, sources, top_k=3, max_context_characters=budget)


def test_no_candidates_and_no_fitting_block_are_distinct_outcomes():
    case, hits, sources = scenario()
    empty = evaluate_context(case, [], sources, top_k=3, max_context_characters=600)
    too_small = evaluate_context(case, hits, sources, top_k=3, max_context_characters=10)
    assert empty.selection_status == "no_candidates" and empty.lost_evidence_count == 0
    assert too_small.selection_status == "no_block_fits" and too_small.lost_evidence_count == 1
    assert empty.selected_sources == too_small.selected_sources == ()


def test_dropped_and_outside_top_k_candidates_must_still_match_public_text():
    case, hits, sources = scenario()
    hits[-1] = {**hits[-1], "content": PRIVATE}
    with pytest.raises(RetrievalEvaluationError) as error:
        evaluate_context(case, hits, sources, top_k=1, max_context_characters=600)
    assert PRIVATE not in str(error.value)


def test_cross_chunk_evidence_is_not_complete_when_one_part_is_dropped():
    sources = {"doc-a": {"name": "公开说明.md", "text": "恢复服务时先暂停导入，再重启服务，健康检查通过后恢复导入。"}}
    text = sources["doc-a"]["text"]
    case = prepare_cases([{
        "id": "cross", "question": "如何恢复？", "answerable": True,
        "required_evidence": [{"document_name": "公开说明.md", "text": text}],
    }], sources)[0]
    hits = [{"document_id": "doc-a", "document_name": "公开说明.md", "chunk_index": index,
             "start_char": start, "end_char": end, "content": text[start:end]}
            for index, (start, end) in enumerate(((0, 20), (16, len(text))), 1)]
    partial = evaluate_context(case, hits, sources, top_k=2, max_context_characters=20)
    complete = evaluate_context(case, hits, sources, top_k=2, max_context_characters=40)
    assert partial.retrieval_evidence.all_evidence_found is True
    assert partial.context_evidence.all_evidence_found is False
    assert complete.context_evidence.covered_evidence_count == 1
    assert complete.context_evidence.evidence[0].first_complete_rank == 2
    assert complete.used_characters == len(text) + 4  # overlap 仍占输入字符，但证据只计一次。


def test_shared_selector_preserves_original_objects_and_continues_after_skip():
    @dataclass(frozen=True)
    class Item:
        content: str

    first, skipped, later = Item("一" * 500), Item("二" * 500), Item("三" * 80)
    selected = select_context([first, skipped, later], 600)
    assert selected[0] is first and selected[1] is later


def test_context_rank_can_differ_from_original_retrieval_rank():
    case, hits, sources = scenario()
    hits[1] = {**hits[1], "content": GOLD, "end_char": 500 + len(GOLD)}
    result = evaluate_context(case, hits, sources, top_k=3, max_context_characters=len(GOLD))
    assert [source.retrieval_rank for source in result.selected_sources] == [2]
    assert result.retrieval_evidence.evidence[0].first_complete_rank == 2
    assert result.context_evidence.evidence[0].first_complete_rank == 1
    assert result.context_evidence.all_evidence_found is True
    encoded = result.to_dict()
    assert encoded["used_characters"] == sum(source["character_count"] for source in encoded["selected_sources"])
