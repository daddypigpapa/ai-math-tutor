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
    ("MULT_CALC", "곱셈·제곱·제곱근 계산 실수", "(3x)² → 3x²"),
    ("FACTOR_PAIR", "곱·합에 맞는 두 수를 잘못 찾음", "x²−9x+20 → (x−3)(x−6)"),
]

# 생성 프롬프트와 평가 채점기가 같은 뜻으로 쓰도록 한 곳에 둔다
DEFINITIONS = {
    "EXPAND_MISSING_TERM": "분배할 때 곱할 항을 빠뜨림",
    "SQUARE_FORMULA": "(a±b)²에서 가운데 2ab 항을 빠뜨리거나 잘못 씀",
    "COEF_COMBINE": "동류항을 더하거나 뺄 때 계수 계산을 틀림(곱셈·제곱 실수는 해당 없음)",
    "SIGN": "부호를 틀림",
    "DIFF_SQUARES": "합차공식 a²−b²=(a+b)(a−b)와 (a−b)²·(a+b)²를 혼동함",
    "FACTOR_SIGN": "올바른 인수분해에서 각 인수의 상수항 부호만 반대로 씀(값이 원래 식과 달라진다)",
    "FACTOR_INCOMPLETE": "값은 원래 식과 같지만 더 인수분해할 수 있는 꼴에서 멈춤",
    "FACTOR_COMMON": "공통인수를 빠뜨리거나 일부만, 또는 잘못 묶음",
    "MULT_CALC": "곱셈 하나, 제곱 하나, 제곱근 하나만 틀리고 나머지 계산은 맞음"
                 "(예: (3x)²→3x², x·x²→x², 2·(−3)→−1, x²−16→(x−8)(x+8))",
    "FACTOR_PAIR": "두 수를 고를 때 곱(상수항)과 합(가운데 항 계수) 중 한 조건만 맞춤"
                   "(예: x²−9x+20→(x−3)(x−6)은 합 −9만 맞음, x²−10x+25→(x−1)(x−25)는 곱 25만 맞음)",
}

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


def error_example(code: str) -> str:
    return next((ex for c, _, ex in ERROR_TYPES if c == code), "")


def unit_name(code: str) -> str:
    for c, n, _ in UNITS:
        if c == code:
            return n
    return code
