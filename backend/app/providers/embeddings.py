"""向量生成协议及数据库写入前的结果校验。"""

from collections.abc import Sequence
from dataclasses import dataclass
import math
import struct
from typing import Protocol


FLOAT32_MAX = 3.4028234663852886e38


class EmbeddingError(RuntimeError):
    """模型调用或返回结果不符合要求。"""


class EmbeddingTimeoutError(EmbeddingError):
    """模型请求超时。"""


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    dimensions: int

    def __post_init__(self) -> None:
        for field in ("provider", "model"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise TypeError("模型身份必须是字符串")
            value = value.strip()
            if not value:
                raise ValueError("模型身份不能为空")
            if len(value) > (512 if field == "provider" else 200):
                raise ValueError("模型身份超过存储长度上限")
            object.__setattr__(self, field, value)
        if type(self.dimensions) is not int:
            raise TypeError("向量维度必须是整数")
        if not 1 <= self.dimensions <= 16_000:
            raise ValueError("向量维度超出支持范围")


class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_embeddings(
    vectors: Sequence[Sequence[float]], *, expected_count: int, dimensions: int
) -> list[list[float]]:
    """保留原值并返回独立列表；验证其可安全转为 pgvector 单精度数值。"""
    if type(expected_count) is not int or expected_count < 0:
        raise EmbeddingError("预期向量数量无效")
    if type(dimensions) is not int or not 1 <= dimensions <= 16_000:
        raise EmbeddingError("预期向量维度无效")
    if not isinstance(vectors, Sequence) or len(vectors) != expected_count:
        raise EmbeddingError("返回的向量数量与文本块数量不一致")

    result: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, Sequence) or len(vector) != dimensions:
            raise EmbeddingError("返回的向量维度不一致")
        copy: list[float] = []
        has_nonzero_float32 = False
        for coordinate in vector:
            if type(coordinate) not in (int, float):
                raise EmbeddingError("向量坐标必须是数值")
            try:
                value = float(coordinate)
            except (OverflowError, ValueError):
                raise EmbeddingError("向量坐标超过存储范围") from None
            if not math.isfinite(value) or abs(value) > FLOAT32_MAX:
                raise EmbeddingError("向量坐标包含无效或超范围数值")
            # pgvector 使用 float32；极小数全部下溢后仍不能成为零向量。
            stored_value = struct.unpack("f", struct.pack("f", value))[0]
            has_nonzero_float32 |= stored_value != 0.0
            copy.append(value)
        if not has_nonzero_float32:
            raise EmbeddingError("零向量无法用于余弦距离")
        result.append(copy)
    return result
