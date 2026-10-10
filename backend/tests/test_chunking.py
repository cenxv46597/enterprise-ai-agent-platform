"""验证重复文本定位、原文覆盖、确定性和切块边界。"""

from uuid import UUID

import pytest

from app.knowledge.chunking import split_text


DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_identical_text_has_distinct_indices_and_source_positions() -> None:
    sentence = "重启后恢复服务。"
    text = sentence + sentence
    chunks = split_text(DOCUMENT_ID, text, chunk_size=len(sentence), overlap=0)

    assert [chunk.content for chunk in chunks] == [sentence, sentence]
    assert [chunk.chunk_index for chunk in chunks] == [1, 2]
    assert [(chunk.start_char, chunk.end_char) for chunk in chunks] == [(0, 8), (8, 16)]
    assert all(text[chunk.start_char:chunk.end_char] == chunk.content for chunk in chunks)
    assert {(chunk.document_id, chunk.chunk_index) for chunk in chunks} == {
        (DOCUMENT_ID, 1), (DOCUMENT_ID, 2)
    }


def test_document_identity_distinguishes_chunks_with_the_same_index() -> None:
    other_id = UUID("00000000-0000-0000-0000-000000000002")
    first = split_text(DOCUMENT_ID, "说明")
    second = split_text(other_id, "说明")
    assert first[0].chunk_index == second[0].chunk_index == 1
    assert first[0].document_id != second[0].document_id


def test_rechunking_changes_numbered_content_but_preserves_original_reference() -> None:
    text = "重启后恢复服务。重启后恢复服务。"
    old_chunks = split_text(DOCUMENT_ID, text, chunk_size=8, overlap=0)
    new_chunks = split_text(DOCUMENT_ID, text, chunk_size=12, overlap=0)

    # 下标 1 取第 2 块；!= 检查两份内容不同，== 检查相同。
    assert old_chunks[1].content != new_chunks[1].content
    assert old_chunks[1].content == "重启后恢复服务。"
    assert new_chunks[1].content == "复服务。"
    assert old_chunks[1].document_id == new_chunks[1].document_id
    assert old_chunks[1].chunk_index == new_chunks[1].chunk_index == 2

    # 原文未变时，旧引用的字符范围仍能找回完整句子。
    old_reference = old_chunks[1]
    assert text[old_reference.start_char:old_reference.end_char] == old_reference.content


@pytest.mark.parametrize(
    ("text", "chunk_size", "overlap"),
    [
        ("短文", 10, 0),
        ("abcdefghij", 6, 2),
        ("abcdefghijk", 6, 2),
        ("abcdefghij", 6, 5),
        ("中🙂e\u0301\n文", 2, 1),
        ("  开始\r\n\r\n末尾  ", 5, 1),
        ("中🙂文", 1, 0),
        ("abcdef", 6, 2),
    ],
)
def test_chunks_reconstruct_the_input_and_preserve_exact_slices(
    text: str, chunk_size: int, overlap: int
) -> None:
    chunks = split_text(DOCUMENT_ID, text, chunk_size=chunk_size, overlap=overlap)
    assert chunks == split_text(DOCUMENT_ID, text, chunk_size=chunk_size, overlap=overlap)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(text)
    assert [chunk.chunk_index for chunk in chunks] == list(range(1, len(chunks) + 1))
    for chunk in chunks:
        assert chunk.document_id == DOCUMENT_ID
        assert 0 <= chunk.start_char < chunk.end_char <= len(text)
        assert len(chunk.content) <= chunk_size
        assert text[chunk.start_char:chunk.end_char] == chunk.content
    for previous, current in zip(chunks, chunks[1:]):
        assert current.start_char > previous.start_char
        assert previous.end_char - current.start_char == overlap
    reconstructed = chunks[0].content + "".join(chunk.content[overlap:] for chunk in chunks[1:])
    assert reconstructed == text


def test_final_chunk_is_not_followed_by_a_redundant_overlap_chunk() -> None:
    chunks = split_text(DOCUMENT_ID, "abcdefghij", chunk_size=6, overlap=2)
    assert [chunk.content for chunk in chunks] == ["abcdef", "efghij"]
    assert [(chunk.start_char, chunk.end_char) for chunk in chunks] == [(0, 6), (4, 10)]


@pytest.mark.parametrize("text", ["", " \n\t\r\n"])
def test_empty_or_whitespace_only_input_has_no_chunks(text: str) -> None:
    assert split_text(DOCUMENT_ID, text) == []


@pytest.mark.parametrize(
    ("chunk_size", "overlap"), [(0, 0), (-1, 0), (5, -1), (5, 5), (5, 6)]
)
def test_invalid_size_and_overlap_are_rejected(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        split_text(DOCUMENT_ID, "", chunk_size=chunk_size, overlap=overlap)


@pytest.mark.parametrize(("chunk_size", "overlap"), [(True, 0), (5, False), (5.0, 0), (5, "1")])
def test_size_and_overlap_require_actual_integers(chunk_size, overlap) -> None:
    with pytest.raises(TypeError):
        split_text(DOCUMENT_ID, "说明", chunk_size=chunk_size, overlap=overlap)


def test_unicode_positions_are_not_byte_offsets() -> None:
    text = "中🙂文"
    chunk = split_text(DOCUMENT_ID, text, chunk_size=2, overlap=0)[0]
    assert (chunk.start_char, chunk.end_char) == (0, 2)
    assert chunk.content == "中🙂"
    assert len(chunk.content.encode("utf-8")) == 7
