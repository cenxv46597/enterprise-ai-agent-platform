"""确定性的字符切块基线；位置对应传入文本，不涉及文件或数据库。"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TextChunk:
    document_id: UUID
    chunk_index: int
    content: str
    start_char: int
    end_char: int


def split_text(
    document_id: UUID,
    text: str,
    *,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[TextChunk]:
    """块从 1 编号；[start_char, end_char) 可直接用于原文切片。"""

    if not isinstance(document_id, UUID):
        raise TypeError("文档标识必须是 UUID")
    if not isinstance(text, str):
        raise TypeError("待切块文本必须是字符串")
    if type(chunk_size) is not int or type(overlap) is not int:
        raise TypeError("块长度与重叠长度必须是整数")
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("块长度必须大于零，重叠长度必须在零与块长度之间，且不等于块长度")
    if not text.strip():
        return []

    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(
            TextChunk(
                document_id=document_id,
                chunk_index=len(chunks) + 1,
                content=text[start:end],
                start_char=start,
                end_char=end,
            )
        )
        if end == len(text):
            break
        start = end - overlap
    return chunks
