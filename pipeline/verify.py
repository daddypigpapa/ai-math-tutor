"""sympy 검증과 값 채우기 (명세 5절 4단계).

LLM은 단계 식과 잘못된 단계 식(wrong_step_sympy)만 내고, 정답·오답의 value는 여기서 sympy가 채운다.
- expanded / number : 값으로 비교한다 (오답 value = 잘못된 단계 식을 끝까지 전개·계산한 결과).
- factored          : 꼴로 비교한다 (오답 value = 잘못된 마지막 단계 식을 정리하지 않고 그대로, evaluate=False).
"""
import re
from collections import defaultdict

import sympy as sp
from sympy.parsing.sympy_parser import convert_xor, parse_expr, standard_transformations

from app import taxonomy

TRANS = standard_transformations + (convert_xor,)
_LOCAL = {c: sp.Symbol(c) for c in "abcdefghijklmnopqrstuvwxyz"}
_ALLOWED_WORDS = {"sqrt", "abs", "pi"}
_UNI = str.maketrans({"−": "-", "–": "-", "×": "*", "·": "*", "⋅": "*", "÷": "/", "²": "**2", "³": "**3"})


class ParseError(ValueError):
    pass


def _clean(s: str) -> str:
    s = str(s).translate(_UNI).strip()
    if not s:
        raise ParseError("빈 식")
    if not re.fullmatch(r"[0-9a-z+\-*/^().,\s]+", s):
        raise ParseError(f"허용되지 않는 문자: {s!r}")
    for w in re.findall(r"[a-z]{2,}", s):
        if w not in _ALLOWED_WORDS:
            # xy 처럼 붙여 쓴 변수는 허용하지 않는다 (모호함) — 모델이 * 를 쓰게 한다
            raise ParseError(f"허용되지 않는 이름: {w!r}")
    return s


def parse(s: str, evaluate: bool = True) -> sp.Expr:
    s = _clean(s)
    try:
        return parse_expr(s, local_dict=dict(_LOCAL), transformations=TRANS, evaluate=evaluate)
    except Exception as e:  # sympy 구문 오류는 종류가 다양하다
        raise ParseError(f"{s!r}: {type(e).__name__}") from e


# ---- 꼴(form) 비교 -------------------------------------------------------

def _collect(e, mult: int, coef: list, bag: dict) -> None:
    """곱의 인수를 평탄화해 (수 계수, {인수: 지수}) 로 모은다. 각 인수는 평가된 전개형으로 정규화한다."""
    if e.is_Mul:
        for a in e.args:
            _collect(a, mult, coef, bag)
    elif e.is_Pow and e.exp.is_Integer and int(e.exp) > 0 and not e.base.is_Number:
        _collect(e.base, mult * int(e.exp), coef, bag)
    elif e.is_Number:
        coef[0] = coef[0] * (e ** mult)
    else:
        key = sp.expand(e.doit())
        if key.is_Number:
            coef[0] = coef[0] * (key ** mult)
        else:
            bag[key] += mult


def canon(e) -> tuple:
    """인수분해 꼴의 표준형. 인수 순서·묶는 방식과 무관하고, 덜 인수분해한 꼴과는 달라진다."""
    coef = [sp.Integer(1)]
    bag: dict = defaultdict(int)
    _collect(e, 1, coef, bag)
    return (coef[0], frozenset(bag.items()))


def same_form(a_str: str, b_expr) -> bool:
    """a_str(evaluate=False 로 파싱)의 꼴이 b_expr 의 꼴과 같은가."""
    return canon(parse(a_str, evaluate=False)) == canon(b_expr)


# ---- 표시 문자열 --------------------------------------------------------

def norm_display(s: str) -> str:
    """정답·오답 value 를 같은 규칙으로 정리한다 (서식 차이가 정답 단서가 되지 않게)."""
    s = str(s).translate(_UNI).replace(" ", "").replace("**", "^")
    s = re.sub(r"(?<![\d.])\*|\*(?![\d.])", "", s)          # 숫자·숫자 곱이 아닌 * 는 생략
    s = re.sub(r"(?<=[\d.])\*(?=[\d.])", "\\\\cdot ", s)    # 숫자 * 숫자
    s = re.sub(r"(?<=[0-9a-z)])([+\-])", r" \1 ", s)
    return s.strip()


def answer_text(ans) -> str:
    return norm_display(sp.sstr(ans))


# ---- 정답 ----------------------------------------------------------------

def compute_answer(expr0, form: str):
    if form == "expanded":
        return sp.expand(expr0)
    if form == "factored":
        return sp.factor(expr0)
    if form == "number":
        return sp.nsimplify(sp.simplify(expr0))
    raise ValueError(f"알 수 없는 answer_form: {form}")


def issue(code: str, field: str, expected="", actual="") -> dict:
    return {"code": code, "field": field, "expected": str(expected), "actual": str(actual)}


def _equal(a, b) -> bool:
    try:
        return sp.simplify(a - b) == 0
    except Exception:
        return False


def build_problem(meta: dict, out: dict) -> tuple[dict, list[dict]]:
    """meta: id/unit/source/seq/difficulty/answer_form/question/original_sympy/prompt_version
       out : LLM 출력 {status, reason, hint, intent, steps[], distractors[]}
       반환: (problem JSON, 이슈 목록). 이슈가 없으면 status=verified 이고 choices 가 채워진다."""
    form = meta["answer_form"]
    problem = {
        "id": meta["id"], "unit": meta["unit"], "source": meta.get("source", "workbook"),
        "seq": meta.get("seq"), "difficulty": meta.get("difficulty", 1),
        "status": "draft", "status_reason": "", "prompt_version": meta.get("prompt_version", ""),
        "question": meta["question"], "answer_form": form,
        "original_sympy": meta["original_sympy"],
        "hint": out.get("hint", ""), "intent": out.get("intent", ""),
        "steps": out.get("steps", []),
    }
    issues: list[dict] = []

    if out.get("status") == "unsuitable":
        problem["status"] = "unsuitable"
        problem["status_reason"] = out.get("reason") or "unsuitable"
        problem["draft"] = out
        return problem, []

    # 원래 식
    try:
        e0 = parse(meta["original_sympy"])
    except ParseError as e:
        problem["draft"] = out
        return _fail(problem, [issue("sympy_parse_error", "original_sympy", "", e)])
    try:
        ans = compute_answer(e0, form)
    except Exception as e:
        problem["draft"] = out
        return _fail(problem, [issue("sympy_parse_error", "original_sympy", "계산 가능한 식", e)])

    # 필수 문장
    for f in ("hint", "intent"):
        if not str(out.get(f, "")).strip():
            issues.append(issue("missing_field", f, "비어 있지 않음", ""))

    # 단계
    steps = out.get("steps") or []
    if not steps:
        issues.append(issue("missing_field", "steps", "1개 이상", 0))
    step_exprs = []
    for i, s in enumerate(steps):
        try:
            se = parse(s.get("sympy", ""))
        except ParseError as e:
            issues.append(issue("sympy_parse_error", f"steps[{i}].sympy", "", e))
            step_exprs.append(None)
            continue
        step_exprs.append(se)
        if not _equal(se, e0):
            issues.append(issue("step_value_mismatch", f"steps[{i}].sympy", sp.sstr(e0), sp.sstr(se)))
    if steps and step_exprs and step_exprs[-1] is not None:
        last = step_exprs[-1]
        if form == "factored":
            try:
                ok = same_form(steps[-1]["sympy"], ans)
            except ParseError:
                ok = False
            if not ok:
                issues.append(issue("answer_mismatch", f"steps[{len(steps) - 1}].sympy", sp.sstr(ans), steps[-1]["sympy"]))
        else:
            ok = _equal(last, ans) and (form != "expanded" or sp.expand(last) == last)
            if not ok:
                issues.append(issue("answer_mismatch", f"steps[{len(steps) - 1}].sympy", sp.sstr(ans), sp.sstr(last)))

    # 오답
    dis = out.get("distractors") or []
    if len(dis) != 3:
        issues.append(issue("wrong_count", "distractors", 3, len(dis)))
    types = [d.get("error_type") for d in dis]
    if len(set(types)) != len(types):
        issues.append(issue("error_type_duplicate", "distractors[].error_type", "서로 다름", types))
    valid_codes = set(taxonomy.error_codes(meta["unit"]))

    ans_text = answer_text(ans)
    seen_text = {ans_text}
    seen_canon = []
    ans_canon = canon(ans) if form == "factored" else None
    values: list[str | None] = []
    for i, d in enumerate(dis):
        fld = f"distractors[{i}]"
        if d.get("error_type") not in valid_codes:
            issues.append(issue("unknown_error_type", f"{fld}.error_type", sorted(valid_codes), d.get("error_type")))
        es = d.get("error_step")
        if not isinstance(es, int) or not (1 <= es <= max(len(steps), 1)):
            issues.append(issue("error_step_range", f"{fld}.error_step", f"1..{len(steps)}", es))
        if not str(d.get("feedback", "")).strip():
            issues.append(issue("missing_field", f"{fld}.feedback", "비어 있지 않음", ""))
        w = d.get("wrong_step_sympy", "")
        try:
            if form == "factored":
                parse(w)  # 구문 확인
                c = canon(parse(w, evaluate=False))
                text = norm_display(w)
                is_ans = (c == ans_canon)
                dup = c in seen_canon or text in seen_text
                seen_canon.append(c)
            else:
                we = parse(w)
                wv = sp.expand(we) if form == "expanded" else sp.nsimplify(sp.simplify(we))
                text = answer_text(wv)
                is_ans = _equal(wv, ans)
                dup = text in seen_text
        except ParseError as e:
            issues.append(issue("sympy_parse_error", f"{fld}.wrong_step_sympy", "", e))
            values.append(None)
            continue
        if is_ans:
            issues.append(issue("distractor_equals_answer", f"{fld}.wrong_step_sympy", f"≠ {ans_text}", text))
        elif dup:
            issues.append(issue("distractor_duplicate", f"{fld}.wrong_step_sympy", "다른 오답과 다른 값", text))
        seen_text.add(text)
        values.append(text)

    # 피드백이 정답 문자열을 말하면 안 된다 (저장된 피드백은 AI 실패 시 그대로 표시된다)
    from app import coach  # 지연 import (순환 방지)
    probe = {"answer": ans_text, "answer_sympy": sp.sstr(ans), "answer_form": form,
             "choices": [{"value": ans_text, "correct": True}]}
    for i, d in enumerate(dis):
        if coach.leaks(str(d.get("feedback", "")), probe):
            issues.append(issue("feedback_leaks_answer", f"distractors[{i}].feedback", "정답 미포함", d.get("feedback")))

    if issues:
        problem["draft"] = out
        return _fail(problem, issues)

    problem["answer"] = ans_text
    problem["answer_sympy"] = sp.sstr(ans)
    problem["choices"] = [{"value": ans_text, "correct": True}]
    for d, v in zip(dis, values):
        problem["choices"].append({
            "value": v, "correct": False, "error_type": d["error_type"], "error_step": d["error_step"],
            "wrong_step_sympy": d["wrong_step_sympy"], "feedback": d["feedback"],
        })
    problem["status"] = "verified"
    problem["status_reason"] = ""
    return problem, []


def _fail(problem: dict, issues: list[dict]) -> tuple[dict, list[dict]]:
    problem["status"] = "draft"
    problem["status_reason"] = ",".join(sorted({i["code"] for i in issues}))
    problem["issues"] = issues
    return problem, issues
