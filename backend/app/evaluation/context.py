"""分开检查检索候选与实际选中上下文的黄金证据覆盖，不用黄金答案选块。"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.evaluation.retrieval import CaseEvaluation, EvaluationCase, evaluate_case
from app.knowledge.context import AnswerContextLimitError, MAX_CONTEXT_CHARACTERS, select_context


@dataclass(frozen=True, slots=True)
class SelectedSource:
    source_id: int
    retrieval_rank: int
    document_id: str
    document_name: str
    chunk_id: str | None
    chunk_index: int
    start_char: int
    end_char: int
    character_count: int


@dataclass(frozen=True, slots=True)
class ContextEvaluation:
    top_k: int
    max_context_characters: int
    selection_status: Literal["selected", "no_candidates", "no_block_fits"]
    used_characters: int
    dropped_candidate_count: int
    selected_sources: tuple[SelectedSource, ...]
    retrieval_evidence: CaseEvaluation
    context_evidence: CaseEvaluation
    lost_evidence_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "max_context_characters": self.max_context_characters,
            "selection_status": self.selection_status,
            "used_characters": self.used_characters,
            "dropped_candidate_count": self.dropped_candidate_count,
            "selected_sources": [asdict(source) for source in self.selected_sources],
            "retrieval_evidence": self.retrieval_evidence.to_dict(),
            "context_evidence": self.context_evidence.to_dict(),
            "lost_evidence_count": self.lost_evidence_count,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    content: str
    retrieval_rank: int
    row: Mapping[str, Any]


def evaluate_context(
    case: EvaluationCase,
    hits: Sequence[Mapping[str, Any]],
    public_sources: Mapping[str, Mapping[str, str]],
    *,
    top_k: int,
    max_context_characters: int,
) -> ContextEvaluation:
    """先核实全部候选，再复用业务选块规则，分别计算选择前后的证据覆盖。

    selected_sources 保存原检索排名和重新编号的 source_id。上下文覆盖结果
    的 first_complete_rank 是选中列表的位置；它不一定等于原检索排名。
    没有候选与所有块放不下分别记录；两者都没有真正调用生成模型。
    """
    if type(max_context_characters) is not int or not 1 <= max_context_characters <= MAX_CONTEXT_CHARACTERS:
        raise AnswerContextLimitError("回答上下文预算配置无效")
    retrieved = evaluate_case(case, hits, public_sources, top_k=top_k)
    candidates = [_Candidate(row["content"], rank, row) for rank, row in enumerate(hits[:top_k], 1)]
    status: Literal["selected", "no_candidates", "no_block_fits"] = "no_candidates"
    selected: list[_Candidate] = []
    if candidates:
        try:
            selected = select_context(candidates, max_context_characters)
            status = "selected"
        except AnswerContextLimitError:
            status = "no_block_fits"
    context = evaluate_case(case, [item.row for item in selected], public_sources, top_k=top_k)
    metadata = tuple(
        SelectedSource(
            source_id, item.retrieval_rank, item.row["document_id"], item.row["document_name"],
            item.row.get("chunk_id"), item.row["chunk_index"], item.row["start_char"], item.row["end_char"],
            len(item.content),
        )
        for source_id, item in enumerate(selected, 1)
    )
    return ContextEvaluation(
        top_k, max_context_characters, status, sum(len(item.content) for item in selected),
        len(candidates) - len(selected), metadata, retrieved, context,
        retrieved.covered_evidence_count - context.covered_evidence_count,
    )
