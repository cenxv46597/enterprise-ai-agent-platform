"""保守的完整规则语法；不能从带优惠等条件的段落中摘出两个价格。"""

import re


PRICE = r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?"
STANDARD_RULE = re.compile(
    rf"(?P<monthly_quote>月费\s*(?P<monthly>{PRICE})\s*元)\s*[，,；;。]\s*"
    rf"(?P<visit_quote>每次上门服务费\s*(?P<visit>{PRICE})\s*元)\s*[。.]?"
)


def match_standard_rule(text: str) -> re.Match[str]:
    match = STANDARD_RULE.fullmatch(text.strip())
    if match is None:
        raise ValueError("仅支持完整的标准月费加上门次费规则")
    return match
