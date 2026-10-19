"""단원·실수유형 사전 (명세 4절). 단원이 늘면 여기와 DB error_types 표에 추가한다."""

UNITS = [
    ("m3-poly", "다항식의 곱셈과 인수분해", 1),
]

# (코드, 이름, 예)
ERROR_TYPES = [
    ("EXPAND_MISSING_TERM", "전개할 때 곱할 항 누락", "(x+3)(x−5) → x²−15"),
    ("SQUARE_FORMULA", "완전제곱식에서 2ab 누락", "(a+b)² → a²+b²"),
    ("COEF_COMBINE", "동류항 계수 계산 실수", "−5x+3x → 2x"),
    ("SIGN", "부호 실수", "3×(−5) → +15"),
    ("DIFF_SQUARES", "합차공식 오용", "a²−b² → (a−b)²"),
    ("FACTOR_SIGN", "인수분해 부호 반대", "x²−2x−15 → (x−3)(x+5)"),
    ("FACTOR_INCOMPLETE", "덜 인수분해함", "2x²−8 → 2(x²−4)"),
    ("FACTOR_COMMON", "공통인수를 빼거나 잘못 묶음", "3x²+6x → 3(x²+2x)"),
]

# 단원 → 해당 실수유형 코드 (하나의 유형이 여러 단원에 걸칠 수 있다)
UNIT_ERROR_TYPES = {
    "m3-poly": [c for c, _, _ in ERROR_TYPES],
}

ANSWER_FORMS = ("expanded", "factored", "number")


def error_codes(unit: str | None = None) -> list[str]:
    if unit and unit in UNIT_ERROR_TYPES:
        return list(UNIT_ERROR_TYPES[unit])
    return [c for c, _, _ in ERROR_TYPES]


def error_name(code: str) -> str:
    for c, n, _ in ERROR_TYPES:
        if c == code:
            return n
    return code


def unit_name(code: str) -> str:
    for c, n, _ in UNITS:
        if c == code:
            return n
    return code
