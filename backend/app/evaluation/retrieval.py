"""按公开原文中的证据范围检查检索候选，不把主题相似当成证据命中。

导入该模块不会创建应用、读取配置或调用模型。字符范围沿用解析后文本的
左闭右开区间。指标仅描述人工预先标注的证据，不能替代完整相关性标注。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
from typing import Any


class RetrievalEvaluationError(ValueError):
    """输入不符合评测约定；错误消息不包含输入正文。"""


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class GoldenEvidence:
    document_id: str
    document_name: str
    text: str
    occurrences: tuple[EvidenceSpan, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    question: str
    answerable: bool
    required_evidence: tuple[GoldenEvidence, ...]
    related_but_insufficient_evidence: tuple[GoldenEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    document_name: str
    text: str
    covered: bool
    first_complete_rank: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "text": self.text,
            "covered": self.covered,
            "first_complete_rank": self.first_complete_rank,
        }


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    case_id: str
    question: str
    answerable: bool
    top_k: int
    returned_count: int
    expected_evidence_count: int
    covered_evidence_count: int
    all_evidence_found: bool | None
    evidence_hit_fraction: float | None
    evidence: tuple[EvidenceResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "answerable": self.answerable,
            "top_k": self.top_k,
            "returned_count": self.returned_count,
            "expected_evidence_count": self.expected_evidence_count,
            "covered_evidence_count": self.covered_evidence_count,
            "all_evidence_found": self.all_evidence_found,
            "evidence_hit_fraction": self.evidence_hit_fraction,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class _PublicSource:
    document_id: str
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class _VerifiedHit:
    document_id: str
    start_char: int
    end_char: int


def _text(value: Any, *, maximum: int | None = None) -> str:
    if type(value) is not str or not value.strip() or (maximum is not None and len(value) > maximum):
        raise RetrievalEvaluationError("评测文本字段无效")
    return value


def _rows(value: Any, *, allow_empty: bool = False, maximum: int = 100) -> Sequence:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RetrievalEvaluationError("评测列表字段无效")
    if len(value) > maximum or (not allow_empty and not value):
        raise RetrievalEvaluationError("评测列表数量无效")
    return value


def _sources(public_sources: Mapping[str, Mapping[str, str]]) -> dict[str, _PublicSource]:
    if not isinstance(public_sources, Mapping) or not public_sources:
        raise RetrievalEvaluationError("公开文档集合无效")
    result: dict[str, _PublicSource] = {}
    names: set[str] = set()
    for document_id, row in public_sources.items():
        _text(document_id, maximum=128)
        if not isinstance(row, Mapping) or set(row) != {"name", "text"}:
            raise RetrievalEvaluationError("公开文档字段无效")
        name = _text(row["name"], maximum=255)
        text = _text(row["text"])
        if name in names:
            raise RetrievalEvaluationError("公开文档名称必须唯一")
        names.add(name)
        result[document_id] = _PublicSource(document_id, name, text)
    return result


def _locate(text: str, expected: str) -> tuple[EvidenceSpan, ...]:
    positions: list[EvidenceSpan] = []
    cursor = 0
    while (start := text.find(expected, cursor)) >= 0:
        positions.append(EvidenceSpan(start, start + len(expected)))
        # 同一句可以重复出现，甚至发生重叠；任一完整出现被覆盖即可。
        cursor = start + 1
    if not positions:
        raise RetrievalEvaluationError("预期证据不在指定公开原文中")
    return tuple(positions)


def _golden_rows(rows: Any, sources: Mapping[str, _PublicSource]) -> tuple[GoldenEvidence, ...]:
    by_name = {source.name: source for source in sources.values()}
    result: list[GoldenEvidence] = []
    seen: set[tuple[str, str]] = set()
    for row in _rows(rows, allow_empty=True):
        if not isinstance(row, Mapping) or set(row) != {"document_name", "text"}:
            raise RetrievalEvaluationError("预期证据字段无效")
        name = _text(row["document_name"], maximum=255)
        expected = _text(row["text"])
        source = by_name.get(name)
        if source is None:
            raise RetrievalEvaluationError("预期证据引用了未知公开文档")
        identity = (name, expected)
        if identity in seen:
            raise RetrievalEvaluationError("同一题的预期证据不能重复标注")
        seen.add(identity)
        result.append(GoldenEvidence(source.document_id, name, expected, _locate(source.text, expected)))
    return tuple(result)


def prepare_cases(
    case_rows: Sequence[Mapping[str, Any]],
    public_sources: Mapping[str, Mapping[str, str]],
) -> tuple[EvaluationCase, ...]:
    """在检索前解析固定题集并定位黄金原文；不会利用检索结果生成标注。

    public_sources 为服务器文档 ID 到 {name, text} 的公开原文映射。正例必须
    标注所需证据，负例不能标注所需证据；相关但不足的文字可单独存档。
    """
    sources = _sources(public_sources)
    prepared: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    required_keys = {"id", "question", "answerable", "required_evidence"}
    for row in _rows(case_rows):
        if not isinstance(row, Mapping) or not required_keys <= set(row) or set(row) - required_keys - {"related_but_insufficient_evidence"}:
            raise RetrievalEvaluationError("评测题目字段无效")
        case_id = _text(row["id"], maximum=128)
        if re.fullmatch(r"[A-Za-z0-9_-]+", case_id) is None or case_id in seen_ids:
            raise RetrievalEvaluationError("评测题目编号无效或重复")
        seen_ids.add(case_id)
        question = _text(row["question"], maximum=2000)
        if type(row["answerable"]) is not bool:
            raise RetrievalEvaluationError("可回答标记必须为布尔值")
        gold = _golden_rows(row["required_evidence"], sources)
        related = _golden_rows(row.get("related_but_insufficient_evidence", []), sources)
        if row["answerable"] != bool(gold):
            raise RetrievalEvaluationError("可回答标记与所需证据数量不一致")
        if {(item.document_name, item.text) for item in gold} & {(item.document_name, item.text) for item in related}:
            raise RetrievalEvaluationError("所需证据与不足证据不能使用同一段标注")
        prepared.append(EvaluationCase(case_id, question, row["answerable"], gold, related))
    return tuple(prepared)


def _verify_hits(hits: Sequence[Mapping[str, Any]], sources: Mapping[str, _PublicSource]) -> tuple[_VerifiedHit, ...]:
    required = {"document_id", "document_name", "chunk_index", "content", "start_char", "end_char"}
    optional = {"chunk_id", "distance"}
    verified: list[_VerifiedHit] = []
    for row in _rows(hits, allow_empty=True, maximum=20):
        if not isinstance(row, Mapping) or not required <= set(row) or set(row) - required - optional:
            raise RetrievalEvaluationError("检索候选字段无效")
        document_id = _text(row["document_id"], maximum=128)
        source = sources.get(document_id)
        if source is None:
            raise RetrievalEvaluationError("检索候选不属于本次公开文档")
        if type(row["document_name"]) is not str or row["document_name"] != source.name:
            raise RetrievalEvaluationError("检索候选文档名称不一致")
        start, end, chunk_index = row["start_char"], row["end_char"], row["chunk_index"]
        if any(type(value) is not int for value in (start, end, chunk_index)) or chunk_index < 1 or not 0 <= start < end <= len(source.text):
            raise RetrievalEvaluationError("检索候选字符范围或块编号无效")
        if type(row["content"]) is not str or row["content"] != source.text[start:end]:
            raise RetrievalEvaluationError("检索候选正文与公开原文不一致")
        if "chunk_id" in row:
            _text(row["chunk_id"], maximum=128)
        if "distance" in row:
            distance = row["distance"]
            if type(distance) not in (int, float) or not 0 <= distance <= 2 or not math.isfinite(distance):
                raise RetrievalEvaluationError("检索候选距离无效")
        verified.append(_VerifiedHit(document_id, start, end))
    return tuple(verified)


def _covers(span: EvidenceSpan, intervals: Sequence[tuple[int, int]]) -> bool:
    cursor = span.start_char
    for start, end in sorted(intervals):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= span.end_char:
            return True
    return False


def evaluate_case(
    case: EvaluationCase,
    hits: Sequence[Mapping[str, Any]],
    public_sources: Mapping[str, Mapping[str, str]],
    *,
    top_k: int,
) -> CaseEvaluation:
    """按返回顺序检查前 K 个候选覆盖了多少预先标注的证据。

    rank 为列表中从 1 开始的位置，与 chunk_index 无关。证据可以由同一
    文档的多个无间隙块共同覆盖；重叠块不会重复增加证据计数。即使调用方
    只统计前 K 条，也会校验所有传入候选，避免未核实的正文进入报告。
    """
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise RetrievalEvaluationError("评测返回条数必须为 1 至 20 的整数")
    if type(case) is not EvaluationCase:
        raise RetrievalEvaluationError("评测题目尚未准备")
    sources = _sources(public_sources)
    if type(case.required_evidence) is not tuple or type(case.related_but_insufficient_evidence) is not tuple:
        raise RetrievalEvaluationError("已准备题目的证据结构无效")
    if any(type(gold) is not GoldenEvidence for gold in (*case.required_evidence, *case.related_but_insufficient_evidence)):
        raise RetrievalEvaluationError("已准备题目的证据结构无效")
    # 同时防止直接构造 dataclass 绕过题目约束，并核对黄金证据位置没有变化。
    prepared_again = prepare_cases([{
        "id": case.case_id,
        "question": case.question,
        "answerable": case.answerable,
        "required_evidence": [{"document_name": gold.document_name, "text": gold.text} for gold in case.required_evidence],
        "related_but_insufficient_evidence": [{"document_name": gold.document_name, "text": gold.text} for gold in case.related_but_insufficient_evidence],
    }], public_sources)[0]
    if prepared_again != case:
        raise RetrievalEvaluationError("评测原文与准备阶段不一致")
    verified = _verify_hits(hits, sources)
    results: list[EvidenceResult] = []
    for gold in case.required_evidence:
        intervals: list[tuple[int, int]] = []
        first_rank = None
        for rank, hit in enumerate(verified[:top_k], start=1):
            if hit.document_id != gold.document_id:
                continue
            intervals.append((hit.start_char, hit.end_char))
            if any(_covers(span, intervals) for span in gold.occurrences):
                first_rank = rank
                break
        results.append(EvidenceResult(gold.document_name, gold.text, first_rank is not None, first_rank))
    expected_count = len(results)
    covered_count = sum(item.covered for item in results)
    return CaseEvaluation(
        case.case_id, case.question, case.answerable, top_k, min(len(verified), top_k),
        expected_count, covered_count,
        covered_count == expected_count if expected_count else None,
        covered_count / expected_count if expected_count else None,
        tuple(results),
    )
