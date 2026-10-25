"""1단계 검증 기준: 일부러 틀린 단계·오답을 넣은 JSON이 이슈 코드로 걸러지고, 덜 인수분해한 오답이 정답과 구분된다."""
import copy
import json

import pytest

from pipeline import seed, verify


def _item(pid):
    return next(i for i in seed.load_seed_files() if i["id"] == pid)


def _build(item, mutate=None):
    it = copy.deepcopy(item)
    out = {"status": "ok", "reason": "", "hint": it["hint"], "intent": it["intent"], "steps": it["steps"],
           "distractors": it["distractors"]}
    if mutate:
        mutate(out)
    meta = {"id": it["id"], "unit": "m3-poly", "source": "ai", "seq": 1, "difficulty": 1, "answer_form": it["answer_form"],
            "question": it["question"], "original_sympy": it["original_sympy"], "prompt_version": "t"}
    return verify.build_problem(meta, out)


def codes(issues):
    return {i["code"] for i in issues}


@pytest.mark.parametrize("item", seed.load_seed_files(), ids=lambda i: i["id"])
def test_seed_problems_verify(item):
    p, issues = _build(item)
    assert not issues, issues
    assert p["status"] == "verified"
    assert sum(1 for c in p["choices"] if c["correct"]) == 1 and len(p["choices"]) == 4
    assert len({c["value"] for c in p["choices"]}) == 4


def test_example_from_spec_values():
    p, _ = _build(_item("m3-poly-s01"))
    vals = {c["value"] for c in p["choices"]}
    assert vals == {"x^2 - 2x - 15", "x^2 - 15", "x^2 - 2x + 15", "x^2 + 2x - 15"}
    assert next(c for c in p["choices"] if c["correct"])["value"] == "x^2 - 2x - 15"


def test_factored_incomplete_is_distinct_from_answer():
    p, issues = _build(_item("m3-poly-s10"))
    assert not issues
    assert p["answer"] == "2(x - 2)(x + 2)"
    inc = next(c for c in p["choices"] if c.get("error_type") == "FACTOR_INCOMPLETE")
    assert inc["value"] == "2(x^2 - 4)"          # 정리하지 않고 그대로
    assert inc["value"] != p["answer"]


def test_factored_same_form_reordered_is_flagged():
    def m(out):
        out["distractors"][0]["wrong_step_sympy"] = "2*(x+2)*(x-2)"   # 정답과 같은 꼴(순서만 다름)
    _, issues = _build(_item("m3-poly-s10"), m)
    assert "distractor_equals_answer" in codes(issues)


def test_step_value_mismatch():
    def m(out):
        out["steps"][1]["sympy"] = "x**2 - 5*x + 3*x - 14"
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "step_value_mismatch" in codes(issues)


def test_answer_mismatch_when_last_step_not_expanded():
    def m(out):
        out["steps"][-1]["sympy"] = "(x+3)*(x-5)"
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "answer_mismatch" in codes(issues)


def test_answer_mismatch_factored_incomplete_last_step():
    def m(out):
        out["steps"][-1]["sympy"] = "2*(x**2-4)"
    _, issues = _build(_item("m3-poly-s10"), m)
    assert "answer_mismatch" in codes(issues)


def test_distractor_equals_answer_expanded():
    def m(out):
        out["distractors"][0]["wrong_step_sympy"] = "x*x + x*(-5) + 3*x + 3*(-5)"
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "distractor_equals_answer" in codes(issues)


def test_distractor_duplicate():
    def m(out):
        out["distractors"][1]["wrong_step_sympy"] = out["distractors"][0]["wrong_step_sympy"]
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "distractor_duplicate" in codes(issues)


def test_wrong_count_and_error_type_duplicate():
    def m(out):
        out["distractors"].pop()
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "wrong_count" in codes(issues)

    def m2(out):
        out["distractors"][1]["error_type"] = out["distractors"][0]["error_type"]
    _, issues = _build(_item("m3-poly-s01"), m2)
    assert "error_type_duplicate" in codes(issues)


def test_sympy_parse_error_and_forbidden_names():
    def m(out):
        out["steps"][0]["sympy"] = "x**2 +* 3"
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "sympy_parse_error" in codes(issues)

    def m2(out):
        out["steps"][0]["sympy"] = "__import__('os').system('echo hi')"
    _, issues = _build(_item("m3-poly-s01"), m2)
    assert "sympy_parse_error" in codes(issues)


def test_feedback_that_leaks_answer_is_flagged():
    def m(out):
        out["distractors"][0]["feedback"] = "정답은 x^2 - 2x - 15 이야."
    _, issues = _build(_item("m3-poly-s01"), m)
    assert "feedback_leaks_answer" in codes(issues)


def test_unsuitable_is_kept_with_reason():
    it = _item("m3-poly-s01")
    p, issues = verify.build_problem(
        {"id": "x", "unit": "m3-poly", "answer_form": "expanded", "question": "q", "original_sympy": it["original_sympy"]},
        {"status": "unsuitable", "reason": "오답을 만들 수 없음"})
    assert p["status"] == "unsuitable" and p["status_reason"] == "오답을 만들 수 없음" and not issues


def test_norm_display_is_uniform():
    assert verify.norm_display("2*(x - 2)*(x + 2)") == "2(x - 2)(x + 2)"
    assert verify.norm_display("x**2-2*x-15") == "x^2 - 2x - 15"
    assert verify.norm_display("2*(x**2-4)") == "2(x^2 - 4)"


def test_problem_json_is_serializable():
    p, _ = _build(_item("m3-poly-s05"))
    assert json.loads(json.dumps(p, ensure_ascii=False))["status"] == "verified"


def test_factor_incomplete_must_keep_the_original_value():
    def m(out):
        out["distractors"][0]["wrong_step_sympy"] = "2*(x+2)*(x+6)"      # 덜 인수분해한 꼴이 아니라 값이 다른 오답
    _, issues = _build(_item("m3-poly-s10"), m)
    assert "error_type_mismatch" in codes(issues)


def test_factor_sign_must_be_a_sign_flip():
    def bad(out):
        out["distractors"][0]["wrong_step_sympy"] = "(x+5)*(x+3)"            # 부호 반대가 아니라 다른 오답
    _, issues = _build(_item("m3-poly-s08"), bad)
    assert "error_type_mismatch" in codes(issues)

    def good(out):
        out["distractors"][0]["wrong_step_sympy"] = "(x+5)*(x-3)"            # (x-5)(x+3) 의 상수항 부호만 반대
    _, issues = _build(_item("m3-poly-s08"), good)
    assert not issues


@pytest.mark.parametrize("form,expr,yes,no", [
    ("factored", "x**2+7*x+12", {"FACTOR_SIGN", "FACTOR_PAIR"}, {"FACTOR_COMMON", "COEF_COMBINE", "DIFF_SQUARES"}),
    ("factored", "x**2-16", {"DIFF_SQUARES", "MULT_CALC"}, {"FACTOR_SIGN", "FACTOR_COMMON", "FACTOR_PAIR"}),
    ("factored", "x**2+14*x+49", {"SQUARE_FORMULA", "FACTOR_PAIR"}, {"DIFF_SQUARES", "FACTOR_COMMON"}),
    ("factored", "x**3-4*x", {"FACTOR_COMMON", "FACTOR_INCOMPLETE", "DIFF_SQUARES"}, {"FACTOR_PAIR"}),
    ("expanded", "-2*x*(x-4)", {"EXPAND_MISSING_TERM", "MULT_CALC"}, {"COEF_COMBINE", "SQUARE_FORMULA", "FACTOR_COMMON"}),
    ("expanded", "(x+1)*(x**2-x+1)", {"COEF_COMBINE"}, {"DIFF_SQUARES"}),
    ("expanded", "(3*x+4)*(3*x-4)", {"DIFF_SQUARES", "COEF_COMBINE"}, {"SQUARE_FORMULA"}),
])
def test_applicable_error_types_follow_problem_structure(form, expr, yes, no):
    got = set(verify.applicable_error_types(verify.parse(expr), form, "m3-poly"))
    assert yes <= got and not (no & got), got
    assert len(got) >= 3                     # 서로 다른 유형 3개를 채울 수 있어야 한다


def test_error_type_not_applicable_is_flagged():
    def m(out):
        out["distractors"][2]["error_type"] = "FACTOR_COMMON"           # x²−2x−15 에는 공통인수가 없다
    _, issues = _build(_item("m3-poly-s08"), m)
    assert "error_type_not_applicable" in codes(issues)


def test_factor_pair_must_miss_exactly_one_condition():
    def bad(out):
        out["distractors"][1]["wrong_step_sympy"] = "(x-2)*(x-7)"      # x²−10x+25: 곱 14·합 −9 둘 다 틀림
    _, issues = _build(_item("m3-poly-s11"), bad)
    assert "error_type_mismatch" in codes(issues)
    p, issues = _build(_item("m3-poly-s11"))                          # (x−1)(x−25): 곱 25만 맞음
    assert not issues and p["status"] == "verified"


def test_norm_display_keeps_trailing_number_product_readable():
    assert verify.norm_display("(2*x+5)*(2*x+5)*2") == "(2x + 5)(2x + 5)\cdot 2"
    assert verify.norm_display("2*3") == "2\cdot 3"
