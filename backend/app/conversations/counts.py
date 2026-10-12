"""保守解析一个完整的非负次数回复；不从长句中猜测数字。"""

import re

from app.conversations.contracts import CountUnit


_DIGITS = dict(zip("零一二三四五六七八九", range(10), strict=True))
_DIGITS["两"] = 2
_MULTIPLIERS = {"十": 10, "百": 100, "千": 1000}


def parse_count(text: str, unit: CountUnit) -> int | None:
    """支持 0..9999、常用中文整数及可选的正确单位；歧义输入保持待补。"""
    token = text.strip()
    if token.endswith(unit):
        token = token[:-len(unit)].strip()
    if re.fullmatch(r"(?:0|[1-9][0-9]{0,3})", token):
        return int(token)
    if token in _DIGITS:
        return _DIGITS[token]
    if not token or any(char not in _DIGITS and char not in _MULTIPLIERS for char in token):
        return None
    # 用规范表示回验，拒绝“三四”“一十十”“零三”等不确定表示。
    total, digit, last_multiplier = 0, None, 10000
    for char in token:
        if char in _DIGITS:
            if digit is not None and digit != 0:
                return None
            digit = _DIGITS[char]
        else:
            multiplier = _MULTIPLIERS[char]
            if multiplier >= last_multiplier or digit == 0:
                return None
            if digit is None and not (char == "十" and total == 0):
                return None
            total += (1 if digit is None else digit) * multiplier
            last_multiplier, digit = multiplier, None
    value = total + (digit or 0)
    if not 0 <= value <= 9999:
        return None
    canonical = _chinese(value)
    return value if token.replace("两", "二") == canonical else None


def _chinese(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value == 0:
        return "零"
    result, zero = "", False
    for multiplier, suffix in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, value = divmod(value, multiplier)
        if digit:
            if zero:
                result += "零"
            result += digits[digit] + suffix
            zero = False
        elif result and value:
            zero = True
    return result[1:] if result.startswith("一十") else result
