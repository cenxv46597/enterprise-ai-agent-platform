"""小规模知识库的词项评分与名次融合；不读配置、不连接数据库或模型。"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
import unicodedata
from uuid import UUID


CHANNEL_LIMIT = 20
RRF_CONSTANT = 60
LEXICAL_VERSION = "han-bigram-ascii-bm25-v1"
_TOKEN = re.compile(r"[a-z0-9]+(?:[_./:\-][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    chunk_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class FusedMatch:
    chunk_id: UUID
    score: float
    vector_rank: int | None
    lexical_rank: int | None
    lexical_score: float | None


def tokenize(text: str) -> tuple[str, ...]:
    """中文连续片段取相邻两字；英文、数字及错误码保留完整词项。

    仅评分时做大小写与全半角归一化，原文和字符位置保持原样。
    单个汉字不进入词项通道；这是一项可解释的基线，没有中文词典。
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    terms = []
    for match in _TOKEN.finditer(normalized):
        value = match.group()
        if "\u3400" <= value[0] <= "\u9fff":
            terms.extend("zh:" + value[index:index + 2] for index in range(len(value) - 1))
        else:
            terms.append("word:" + value)
    return tuple(terms)


def lexical_ranking(query: str, contents: Mapping[UUID, str]) -> list[LexicalMatch]:
    """BM25 基线：稀有词权重、词频饱和与块长度归一化，零分不入选。"""
    query_terms = set(tokenize(query))
    if not query_terms or not contents:
        return []
    counts = {identifier: Counter(tokenize(content)) for identifier, content in contents.items()}
    lengths = {identifier: sum(counter.values()) for identifier, counter in counts.items()}
    average_length = sum(lengths.values()) / len(counts)
    if not average_length:
        return []
    frequencies = Counter(term for counter in counts.values() for term in query_terms if term in counter)
    weights = {
        term: math.log1p((len(counts) - count + 0.5) / (count + 0.5))
        for term, count in frequencies.items()
    }
    matches = []
    for identifier, counter in counts.items():
        normalization = 1.2 * (1 - 0.75 + 0.75 * lengths[identifier] / average_length)
        # 固定词项顺序避免浮点加法顺序随进程哈希种子变化。
        score = sum(
            weights[term] * counter[term] * 2.2 / (counter[term] + normalization)
            for term in sorted(query_terms & counter.keys())
        )
        if score > 0:
            matches.append(LexicalMatch(identifier, score))
    return sorted(matches, key=lambda match: (-match.score, match.chunk_id))[:CHANNEL_LIMIT]


def fuse_rankings(vector_ids: Sequence[UUID], lexical: Sequence[LexicalMatch], top_k: int) -> list[FusedMatch]:
    """同一块按 UUID 合并，累加各路 1/(60+名次)，稳定排序后截取。"""
    vector_ranks = {identifier: rank for rank, identifier in enumerate(dict.fromkeys(vector_ids), 1)}
    lexical_by_id = {match.chunk_id: match for match in reversed(lexical)}
    lexical_ranks = {identifier: rank for rank, identifier in enumerate(dict.fromkeys(match.chunk_id for match in lexical), 1)}
    merged = []
    for identifier in vector_ranks.keys() | lexical_ranks.keys():
        vector_rank, lexical_rank = vector_ranks.get(identifier), lexical_ranks.get(identifier)
        score = sum(1 / (RRF_CONSTANT + rank) for rank in (vector_rank, lexical_rank) if rank is not None)
        merged.append(FusedMatch(
            identifier, score, vector_rank, lexical_rank,
            lexical_by_id[identifier].score if identifier in lexical_by_id else None,
        ))
    return sorted(merged, key=lambda match: (-match.score, match.chunk_id))[:top_k]
