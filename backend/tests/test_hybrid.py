"""词项基线与名次融合的行为验证，不以模拟排名宣称模型质量。"""

from uuid import UUID

import pytest

from app.knowledge.hybrid import LexicalMatch, fuse_rankings, lexical_ranking, tokenize


def test_identifier_is_atomic_case_insensitive_and_fullwidth_normalized():
    identifier, decoy = UUID(int=1), UUID(int=2)
    text = "返回 ＴＯＫＥＮ＿ＥＸＰＩＲＥＤ，需要重新配置。"
    result = lexical_ranking("token_expired", {identifier: text, decoy: "返回 TOKEN_EXPIRED_INTERNAL。"})
    assert [match.chunk_id for match in result] == [identifier]
    assert text == "返回 ＴＯＫＥＮ＿ＥＸＰＩＲＥＤ，需要重新配置。"


def test_chinese_two_character_terms_match_inside_longer_sentences():
    identifier = UUID(int=1)
    result = lexical_ranking("如何轮换令牌", {identifier: "管理员轮换令牌并检查服务。"})
    assert result[0].chunk_id == identifier and result[0].score > 0
    assert "zh:令牌" in tokenize("轮换令牌")


@pytest.mark.parametrize("query", ["", "？!", "问", "unmentioned_identifier"])
def test_no_matching_term_produces_no_lexical_candidates(query):
    assert lexical_ranking(query, {UUID(int=1): "需要更换访问令牌。"}) == []


def test_short_exact_answer_ranks_above_long_repeated_keyword_block():
    concise, stuffed = UUID(int=1), UUID(int=2)
    result = lexical_ranking("TOKEN_EXPIRED", {
        concise: "TOKEN_EXPIRED：更换令牌。",
        stuffed: "TOKEN_EXPIRED " * 3 + "无关背景内容" * 500,
    })
    assert [match.chunk_id for match in result] == [concise, stuffed]


def test_scores_do_not_get_added_to_cosine_distances_and_shared_hit_is_one_row():
    shared, vector_only, lexical_only = UUID(int=1), UUID(int=2), UUID(int=3)
    result = fuse_rankings([vector_only, shared], [LexicalMatch(shared, 0.01), LexicalMatch(lexical_only, 900)], 3)
    assert [match.chunk_id for match in result] == [shared, vector_only, lexical_only]
    assert result[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result[0].vector_rank == 2 and result[0].lexical_rank == 1
    assert result[0].lexical_score == 0.01
    assert len({match.chunk_id for match in result}) == 3


def test_channel_duplicates_cannot_increase_votes():
    identifier = UUID(int=1)
    result = fuse_rankings([identifier, identifier], [LexicalMatch(identifier, 8), LexicalMatch(identifier, 9)], 5)
    assert len(result) == 1
    assert result[0].score == pytest.approx(2 / 61)
    assert result[0].vector_rank == result[0].lexical_rank == 1
    assert result[0].lexical_score == 8


def test_no_lexical_hit_preserves_vector_order_and_tie_is_stable():
    first, second = UUID(int=1), UUID(int=2)
    assert [row.chunk_id for row in fuse_rankings([second, first], [], 2)] == [second, first]
    left = fuse_rankings([second], [LexicalMatch(first, 1)], 2)
    right = fuse_rankings([second], [LexicalMatch(first, 1)], 2)
    assert left == right and [row.chunk_id for row in left] == [first, second]
