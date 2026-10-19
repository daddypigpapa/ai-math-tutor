"""4단계 검증 기준: 10문제 중 5개 정답 → 약점 판정 → 복습 문제 10개 생성 → 버튼 표시. 계산단계 본 정답은 오답 집계."""
import json
import re

from app import learn, problems, stats
from pipeline import generate, verify
from tests.conftest import ok


def _correct(p):
    return next(c["value"] for c in p["choices"] if c["correct"])


def _wrong(p, i=0):
    return [c["value"] for c in p["choices"] if not c["correct"]][i]


def solve(conn, lid, good: bool, steps: bool = False, mode="normal"):
    r = learn.start_attempt(conn, lid, mode, "m3-poly")
    p = problems.load(conn, r["problem"]["id"])
    if steps:
        learn.steps(conn, r["attempt_id"])
    if good:
        out = learn.answer(conn, r["attempt_id"], _correct(p))
    else:
        learn.answer(conn, r["attempt_id"], _wrong(p, 0))
        out = learn.answer(conn, r["attempt_id"], _wrong(p, 1))
    return out


def test_steps_viewed_correct_counts_as_wrong(seeded, learner):
    for _ in range(4):
        solve(seeded, learner, True)
    for _ in range(2):
        solve(seeded, learner, True, steps=True)
    s = stats.unit_stats(seeded, learner, "m3-poly")
    assert s["n"] == 6 and s["independent"] == 4 and s["helped"] == 2
    assert abs(s["rate"] - 4 / 6) < 1e-9


def test_retry_correct_counts_as_wrong_in_unit_rate(seeded, learner):
    r = learn.start_attempt(seeded, learner, "normal", "m3-poly")
    p = problems.load(seeded, r["problem"]["id"])
    learn.answer(seeded, r["attempt_id"], _wrong(p))
    learn.answer(seeded, r["attempt_id"], _correct(p))
    s = stats.unit_stats(seeded, learner, "m3-poly")
    assert s["n"] == 1 and s["independent"] == 0 and s["helped"] == 0


def test_error_type_rate_is_per_exposure(seeded, learner):
    r = learn.start_attempt(seeded, learner, "normal", "m3-poly")
    p = problems.load(seeded, r["problem"]["id"])
    bad = next(c for c in p["choices"] if not c["correct"])
    learn.answer(seeded, r["attempt_id"], bad["value"])
    learn.answer(seeded, r["attempt_id"], _correct(p))
    s = stats.error_type_stats(seeded, learner, bad["error_type"])
    assert s["n"] == 1 and s["picked"] == 1 and s["rate"] == 0.0
    other = [c for c in p["choices"] if not c["correct"] and c["error_type"] != bad["error_type"]][0]
    s2 = stats.error_type_stats(seeded, learner, other["error_type"])
    assert s2["n"] == 1 and s2["picked"] == 0 and s2["rate"] == 1.0


def test_two_wrong_records_both_error_types(seeded, learner):
    solve(seeded, learner, False)
    rows = seeded.execute("SELECT error_type FROM attempt_events WHERE kind='answer' AND error_type IS NOT NULL").fetchall()
    assert len(rows) == 2 and rows[0]["error_type"] != rows[1]["error_type"]


def test_no_weak_before_10_problems(seeded, learner):
    for _ in range(9):
        solve(seeded, learner, False)
    assert stats.weak_candidates(seeded, learner) == {}


# ---- 복습 문제 자동 준비용 가짜 LLM ---------------------------------------

def install_fake_generation(fake):
    counter = {"n": 0}

    def newproblem(system, user, kw):
        counter["n"] += 1
        a, b = 1 + counter["n"] % 6, 2 + counter["n"] // 6          # 서로 다른 (x+a)(x+b)
        return ok(data={"question": f"다음 식을 전개하시오. $(x+{a})(x+{b})$", "latex": f"(x+{a})(x+{b})", "answer_form": "expanded"})

    def convert(system, user, kw):
        latex = re.search(r"<latex>(.*)</latex>", user).group(1)
        return ok(data={"sympy": latex.replace(")(", ")*(")})

    def generate_(system, user, kw):
        expr = re.search(r"<sympy>(.*)</sympy>", user).group(1)
        a, b = [int(x) for x in re.findall(r"x\+(\d+)", expr)]
        return ok(data={
            "status": "ok", "reason": "", "hint": "각 항을 빠짐없이 곱해 보자.", "intent": "전개 연습.",
            "steps": [{"no": 1, "text": "전개", "sympy": f"x*x + x*{b} + {a}*x + {a}*{b}"},
                      {"no": 2, "text": "정리", "sympy": f"x**2 + {a + b}*x + {a * b}"}],
            "distractors": [
                {"error_type": "EXPAND_MISSING_TERM", "error_step": 1, "wrong_step_sympy": f"x*x + {a}*{b}", "feedback": "곱할 항이 빠졌어. 몇 번 곱해야 할까?"},
                {"error_type": "SIGN", "error_step": 1, "wrong_step_sympy": f"x*x + x*{b} + {a}*x - {a}*{b}", "feedback": "마지막 곱의 부호를 다시 봐."},
                {"error_type": "COEF_COMBINE", "error_step": 2, "wrong_step_sympy": f"x**2 + {a + b + 1}*x + {a * b}", "feedback": "동류항을 합친 계수를 다시 계산해 볼래?"}]})

    fake.handlers.update(newproblem=newproblem, convert=convert, generate=generate_)
    return counter


def test_weak_detection_prepare_review_and_button(seeded, learner, fake):
    install_fake_generation(fake)
    for i in range(10):
        out = solve(seeded, learner, good=(i < 5))       # 10문제 중 5개 정답 → 50% < 60%
    weak = stats.open_weak(seeded, learner)
    assert [(w["kind"], w["code"]) for w in weak if w["kind"] == "unit"] == [("unit", "m3-poly")]
    assert not stats.review_available(seeded, learner)   # 준비 전에는 버튼이 없다
    wid = next(w["id"] for w in weak if w["kind"] == "unit")
    generate.prepare_review(wid)                          # 백그라운드 작업을 직접 실행
    seeded.commit()
    n = seeded.execute("SELECT COUNT(*) n FROM problems WHERE source='ai_review' AND status='verified'").fetchone()["n"]
    assert n == 10
    assert seeded.execute("SELECT review_ready FROM weak_areas WHERE id=?", (wid,)).fetchone()["review_ready"] == 1
    assert stats.review_available(seeded, learner)


def test_review_composition_priority(seeded, learner, fake):
    install_fake_generation(fake)
    for i in range(10):
        solve(seeded, learner, good=(i < 5))
    wrong_ids = {r["problem_id"] for r in seeded.execute("SELECT problem_id FROM attempts WHERE result='wrong'")}
    wid = next(w["id"] for w in stats.open_weak(seeded, learner) if w["kind"] == "unit")
    generate.prepare_review(wid)
    picked = stats.pick_review_problems(seeded, learner)
    assert len(picked) == 10 and len(set(picked)) == 10
    assert set(picked[:5]) == wrong_ids                   # ① 예전에 틀린 문제가 먼저
    srcs = [seeded.execute("SELECT source FROM problems WHERE id=?", (i,)).fetchone()["source"] for i in picked[5:]]
    assert srcs[0] in ("ai", "workbook")                  # ② 기존 문제가 ③ 자동 준비 문제보다 앞


def test_review_session_runs_10_and_weak_clears(seeded, learner, fake):
    install_fake_generation(fake)
    for i in range(10):
        solve(seeded, learner, good=(i < 5))
    wid = next(w["id"] for w in stats.open_weak(seeded, learner) if w["kind"] == "unit")
    generate.prepare_review(wid)
    seen = []
    for _ in range(10):
        out = solve(seeded, learner, good=True, mode="review")
        seen.append(out)
    n = seeded.execute("SELECT COUNT(*) n FROM attempts WHERE mode='review' AND ended_at IS NOT NULL").fetchone()["n"]
    assert n == 10
    assert stats.unit_stats(seeded, learner, "m3-poly")["rate"] >= 0.6     # 최근 20문제: 5+10 / 20 = 75%
    assert not [w for w in stats.open_weak(seeded, learner) if w["kind"] == "unit"]   # 자동 해제
    assert seeded.execute("SELECT cleared_at FROM weak_areas WHERE kind='unit'").fetchone()["cleared_at"]


def test_new_weak_returned_once(seeded, learner):
    ids_seen = []
    for i in range(12):
        out = solve(seeded, learner, good=False)
        ids_seen += out.get("_new_weak", [])
    assert len([w for w in ids_seen]) >= 1
    units = seeded.execute("SELECT COUNT(*) n FROM weak_areas WHERE kind='unit'").fetchone()["n"]
    assert units == 1                                     # 이미 약점이면 중복 생성하지 않는다
