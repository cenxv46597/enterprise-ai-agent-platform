"""次数解析和短期上下文预算；纯程序行为，不调用模型。"""

import pytest

from app.conversations.counts import parse_count
from app.conversations.service import recent_messages


@pytest.mark.parametrize("text,value", [
    ("三次", 3), ("三", 3), (" 3 次 ", 3), ("0次", 0), ("零次", 0), ("两次", 2),
    ("十次", 10), ("二十三次", 23), ("一百零三次", 103), ("一千零一十次", 1010),
    ("九千九百九十九次", 9999), ("9999", 9999),
])
def test_complete_count_reply(text, value):
    assert parse_count(text, "次") == value


@pytest.mark.parametrize("text", [
    "三个月", "三次或四次", "大概三次", "-1次", "3.5次", "10000次", "三四次", "零三次",
    "一十十次", "十百次", "03", "", "不确定", "换个问题", "三次，然后帮我算费用", "３次",
])
def test_ambiguous_unsupported_or_wrong_unit_reply_does_not_fill_slot(text):
    assert parse_count(text, "次") is None


def test_month_unit_must_match():
    assert parse_count("三个月", "个月") == 3
    assert parse_count("三次", "个月") is None


def test_memory_selects_latest_three_complete_pairs_in_time_order():
    messages = recent_messages([(f"问题{i}", f"回复{i}") for i in range(5)])
    assert [m.content for m in messages] == ["问题2", "回复2", "问题3", "回复3", "问题4", "回复4"]
    assert [m.role for m in messages] == ["user", "assistant"] * 3


def test_memory_character_budget_never_slices_a_pair_or_substitutes_older_history():
    messages = recent_messages([("旧问题", "旧回复"), ("甲" * 2000, "乙" * 2000), ("新问题", "新回复")])
    assert [m.content for m in messages] == ["新问题", "新回复"]
    assert recent_messages([("旧问题", "旧回复"), ("甲" * 2000, "乙" * 2001)]) == []
