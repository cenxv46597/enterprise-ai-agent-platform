"""共用有界 Decimal 运算，不接受表达式或执行代码。"""

from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, localcontext

from app.agents.calculation_contracts import DIVISION_DECIMAL_PLACES, canonical_decimal


def decimal_operation(operation: str, left: str, right: str) -> tuple[str, bool, int | None]:
    left_value, right_value = Decimal(left), Decimal(right)
    if operation == "divide" and right_value.is_zero():
        raise DivisionByZero
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999, Emax=999,
                              traps=[InvalidOperation, DivisionByZero, Overflow])):
        rounded, places = False, None
        if operation == "add":
            value = left_value + right_value
        elif operation == "subtract":
            value = left_value - right_value
        elif operation == "multiply":
            value = left_value * right_value
        elif operation == "divide":
            value = (left_value / right_value).quantize(Decimal("0.00000001"))
            rounded = value * right_value != left_value
            places = DIVISION_DECIMAL_PLACES
        else:
            raise InvalidOperation
        return canonical_decimal(value), rounded, places
