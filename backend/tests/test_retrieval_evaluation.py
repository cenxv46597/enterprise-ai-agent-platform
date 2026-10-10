"""纯规则测试：预期证据必须被真实公开原文的候选范围完整覆盖。"""

from dataclasses import replace
import subprocess
import sys

import pytest

from app.evaluation.retrieval import (
    RetrievalEvaluationError,
    evaluate_case,
    prepare_cases,
)


SOURCES = {
    "doc-a": {"name": "恢复说明.md", "text": "上传格式：TXT。恢复步骤：暂停导入，再重启服务。日志位置：控制台。"},
    "doc-b": {"name": "其他说明.md", "text": "恢复步骤：暂停导入，再重启服务。"},
}
GOLD = "恢复步骤：暂停导入，再重启服务。"
PRIVATE = "PRIVATE-CONTENT-MUST-NOT-BE-REPORTED"


def row(*, answerable=True, evidence=None, related=None, case_id="recovery"):
    result = {
        "id": case_id,
        "question": "如何恢复服务？",
        "answerable": answerable,
        "required_evidence": evidence if evidence is not None else [{"document_name": "恢复说明.md", "text": GOLD}],
    }
    if related is not None:
        result["related_but_insufficient_evidence"] = related
    return result


def hit(document_id="doc-a", *, start=None, end=None, text=None, chunk_index=1, sources=SOURCES):
    source = sources[document_id]
    if text is not None:
        start = source["text"].index(text)
        end = start + len(text)
    if start is None:
        start = 0
    if end is None:
        end = len(source["text"])
    return {
        "document_id": document_id,
        "document_name": source["name"],
        "chunk_id": f"chunk-{document_id}-{chunk_index}",
        "chunk_index": chunk_index,
        "start_char": start,
        "end_char": end,
        "content": source["text"][start:end],
        "distance": 0.125,
    }


def evaluate(hits, *, source_rows=SOURCES, case_row=None, top_k=5):
    prepared = prepare_cases([case_row or row()], source_rows)[0]
    return evaluate_case(prepared, hits, source_rows, top_k=top_k)


def test_complete_evidence_has_first_rank_and_serializable_result():
    result = evaluate([hit(text=GOLD)])
    assert result.all_evidence_found is True
    assert result.evidence_hit_fraction == 1
    assert result.evidence[0].first_complete_rank == 1
    assert result.to_dict()["evidence"] == [{
        "document_name": "恢复说明.md", "text": GOLD,
        "covered": True, "first_complete_rank": 1,
    }]


def test_same_document_wrong_paragraph_does_not_count():
    result = evaluate([hit(text="上传格式：TXT。")])
    assert result.all_evidence_found is False
    assert result.evidence_hit_fraction == 0
    assert result.evidence[0].first_complete_rank is None


def test_identical_evidence_in_another_document_does_not_count():
    result = evaluate([hit("doc-b", text=GOLD)])
    assert result.covered_evidence_count == 0


def test_adjacent_chunks_jointly_cover_one_evidence():
    start = SOURCES["doc-a"]["text"].index(GOLD)
    middle = start + 8
    result = evaluate([hit(start=middle, end=start + len(GOLD)), hit(start=start, end=middle)])
    assert result.covered_evidence_count == 1
    assert result.evidence[0].first_complete_rank == 2


def test_overlapping_and_duplicate_chunks_do_not_duplicate_evidence_count():
    start = SOURCES["doc-a"]["text"].index(GOLD)
    first = hit(start=start, end=start + 12)
    result = evaluate([first, first, hit(start=start + 8, end=start + len(GOLD))])
    assert result.expected_evidence_count == result.covered_evidence_count == 1
    assert result.evidence[0].first_complete_rank == 3


def test_one_character_gap_cannot_cover_evidence():
    start = SOURCES["doc-a"]["text"].index(GOLD)
    result = evaluate([hit(start=start, end=start + 8), hit(start=start + 9, end=start + len(GOLD))])
    assert result.covered_evidence_count == 0


def test_intervals_from_other_documents_cannot_fill_gap():
    start = SOURCES["doc-a"]["text"].index(GOLD)
    result = evaluate([hit(start=start, end=start + 8), hit("doc-b", start=8, end=len(GOLD))])
    assert result.covered_evidence_count == 0


def test_any_complete_repeated_occurrence_counts_once():
    sources = {"doc-a": {"name": "恢复说明.md", "text": GOLD + "中间说明。" + GOLD}}
    second_start = len(GOLD + "中间说明。")
    result = evaluate([hit(start=second_start, sources=sources)], source_rows=sources)
    assert result.covered_evidence_count == 1
    assert result.evidence[0].first_complete_rank == 1


def test_fragments_of_different_occurrences_cannot_be_stitched():
    sources = {"doc-a": {"name": "恢复说明.md", "text": "AB--AB"}}
    case_row = row(evidence=[{"document_name": "恢复说明.md", "text": "AB"}])
    result = evaluate([hit(start=0, end=1, sources=sources), hit(start=5, end=6, sources=sources)], source_rows=sources, case_row=case_row)
    assert result.covered_evidence_count == 0


def test_rank_is_return_position_not_chunk_index_and_respects_k():
    distractor = hit(text="上传格式：TXT。", chunk_index=99)
    hits = [distractor] * 6 + [hit(text=GOLD, chunk_index=1)]
    prepared = prepare_cases([row()], SOURCES)[0]
    at_five = evaluate_case(prepared, hits, SOURCES, top_k=5)
    at_ten = evaluate_case(prepared, hits, SOURCES, top_k=10)
    assert at_five.evidence[0].first_complete_rank is None
    assert at_ten.evidence[0].first_complete_rank == 7
    assert at_five.returned_count == 5
    assert at_ten.returned_count == 7


def test_each_distinct_gold_is_scored_once_with_partial_fraction():
    evidence = [{"document_name": "恢复说明.md", "text": GOLD}, {"document_name": "恢复说明.md", "text": "日志位置：控制台。"}]
    result = evaluate([hit(text=GOLD)], case_row=row(evidence=evidence))
    assert result.expected_evidence_count == 2
    assert result.covered_evidence_count == 1
    assert result.evidence_hit_fraction == 0.5
    assert result.all_evidence_found is False


def test_negative_case_is_unscored_even_when_related_text_is_found():
    related = [{"document_name": "恢复说明.md", "text": "上传格式：TXT。"}]
    result = evaluate([hit(text="上传格式：TXT。")], case_row=row(answerable=False, evidence=[], related=related))
    assert result.expected_evidence_count == result.covered_evidence_count == 0
    assert result.all_evidence_found is None
    assert result.evidence_hit_fraction is None
    assert result.evidence == ()


def test_empty_results_for_positive_are_a_miss():
    result = evaluate([])
    assert result.returned_count == 0
    assert result.all_evidence_found is False
    assert result.evidence_hit_fraction == 0


@pytest.mark.parametrize("field,value", [("start_char", True), ("end_char", False), ("chunk_index", True), ("start_char", -1), ("chunk_index", -1), ("chunk_index", 0), ("end_char", 10_000)])
def test_character_ranges_and_positive_chunk_indices_are_strict_integers(field, value):
    invalid = {**hit(text=GOLD), field: value}
    with pytest.raises(RetrievalEvaluationError):
        evaluate([invalid])


@pytest.mark.parametrize("top_k", [True, 0, 21])
def test_top_k_rejects_boolean_and_out_of_range_values(top_k):
    with pytest.raises(RetrievalEvaluationError):
        evaluate([], top_k=top_k)


def test_unknown_private_document_is_rejected_without_disclosing_text():
    invalid = {**hit(text=GOLD), "document_id": PRIVATE, "content": PRIVATE}
    with pytest.raises(RetrievalEvaluationError) as error:
        evaluate([invalid])
    assert PRIVATE not in str(error.value)


def test_private_or_modified_content_is_rejected_even_outside_scored_prefix():
    invalid = {**hit(text=GOLD), "content": PRIVATE}
    with pytest.raises(RetrievalEvaluationError) as error:
        evaluate([hit(text=GOLD), invalid], top_k=1)
    assert PRIVATE not in str(error.value)


def test_document_id_and_name_must_agree():
    invalid = {**hit(text=GOLD), "document_name": "其他说明.md"}
    with pytest.raises(RetrievalEvaluationError):
        evaluate([invalid])


def test_nonexistent_gold_and_duplicate_annotations_are_rejected():
    for evidence in ([{"document_name": "恢复说明.md", "text": PRIVATE}], row()["required_evidence"] * 2):
        with pytest.raises(RetrievalEvaluationError) as error:
            prepare_cases([row(evidence=evidence)], SOURCES)
        assert PRIVATE not in str(error.value)


def test_case_ids_and_public_document_names_must_be_unique():
    with pytest.raises(RetrievalEvaluationError):
        prepare_cases([row(), row()], SOURCES)
    duplicate_names = {**SOURCES, "doc-c": {"name": "恢复说明.md", "text": GOLD}}
    with pytest.raises(RetrievalEvaluationError):
        prepare_cases([row()], duplicate_names)


def test_answerable_flag_requires_exact_boolean_and_consistent_gold():
    for invalid in (row(answerable=True, evidence=[]), row(answerable=False), row(answerable=1)):
        with pytest.raises(RetrievalEvaluationError):
            prepare_cases([invalid], SOURCES)


def test_prepared_case_cannot_be_reused_with_moved_evidence_or_forged_state():
    prepared = prepare_cases([row()], SOURCES)[0]
    changed_sources = {**SOURCES, "doc-a": {"name": "恢复说明.md", "text": "新增文字。" + SOURCES["doc-a"]["text"]}}
    with pytest.raises(RetrievalEvaluationError):
        evaluate_case(prepared, [], changed_sources, top_k=5)
    with pytest.raises(RetrievalEvaluationError):
        evaluate_case(replace(prepared, answerable=False), [], SOURCES, top_k=5)


def test_module_import_does_not_load_application_configuration():
    process = subprocess.run(
        [sys.executable, "-c", "import sys; import app.evaluation.retrieval; assert 'app.core.config' not in sys.modules; assert 'app.main' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert process.returncode == 0, "纯评测模块导入失败或读取了应用配置"
