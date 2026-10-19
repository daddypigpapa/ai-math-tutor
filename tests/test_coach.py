"""3단계 검증 기준: 정답 누설 차단, 200자·반말, 키 없이도 저장된 피드백으로 진행, 한도 초과 시 저장 문장."""
import pytest

from app import coach, config, learn, problems
from tests.conftest import ok


def _prob(conn, pid="m3-poly-s01"):
    return problems.load(conn, pid)


@pytest.mark.parametrize("text", [
    "정답은 x²−2x−15 야.",
    "x^2-2x-15 가 맞아",
    "x^2 - 2x - 15",
    "−15 + x² − 2x 라고 써봐",
    "답은 $x^2-2x-15$ 이야",
    "X^2 - 2X - 15",
])
def test_leak_detected_expanded(seeded, text):
    assert coach.leaks(text, _prob(seeded))


@pytest.mark.parametrize("text", [
    "곱해야 할 항이 몇 개인지 세어 볼래?",
    "x²과 −5x를 다시 확인해 봐.",
    "(x+3)(x−5)를 다시 보고 네 번 곱했는지 확인해 볼래?",   # 문제 속 미전개 곱은 누설이 아니다
])
def test_no_leak_expanded(seeded, text):
    assert not coach.leaks(text, _prob(seeded))


def test_leak_factored_variants(seeded):
    p = _prob(seeded, "m3-poly-s08")   # (x-5)(x+3)
    assert coach.leaks("(x−5)(x+3) 이야", p)
    assert coach.leaks("(x+3)(x-5)", p)                       # 순서 바꾼 표기
    assert coach.leaks("(x+3)*(x-5)", p)
    assert not coach.leaks("곱이 −15, 합이 −2인 두 수를 다시 찾아봐", p)


def test_check_text_rules(seeded):
    p = _prob(seeded)
    assert coach.check_text("첫 단계에서 곱하는 항이 빠졌어. 몇 개를 곱해야 할까?", p) is None
    assert coach.check_text("가" * 201, p) == "too_long"
    assert coach.check_text("**굵게** 말해", p) == "markdown"
    assert coach.check_text("다시 확인해 보세요.", p) == "formal_speech"
    assert coach.check_text("정답은 x^2-2x-15 야.", p) == "leak"
    assert coach.check_text("   ", p) == "empty"


def _attempt(conn, learner_id, value_index=0):
    r = learn.start_attempt(conn, learner_id, "normal", "m3-poly")
    p = problems.load(conn, r["problem"]["id"])
    wrong = [c["value"] for c in p["choices"] if not c["correct"]][value_index]
    return r["attempt_id"], p, wrong


def test_no_api_key_falls_back_to_stored_feedback(seeded, learner):
    aid, p, wrong = _attempt(seeded, learner)
    out = learn.answer(seeded, aid, wrong)
    stored = next(c["feedback"] for c in p["choices"] if c["value"] == wrong)
    assert out["coaching"] == {"text": stored, "source": "stored"} and out["ai_call_id"] is None


def test_ai_coaching_used_when_valid(seeded, learner, fake):
    fake.handlers["coach"] = lambda s, u, kw: ok("첫 단계에서 곱하는 항이 빠진 것 같아. 몇 개를 곱해야 할까?")
    aid, p, wrong = _attempt(seeded, learner)
    out = learn.answer(seeded, aid, wrong)
    assert out["coaching"]["source"] == "ai" and out["ai_call_id"]


def test_prompt_never_contains_the_answer(seeded, learner, fake):
    fake.handlers["coach"] = lambda s, u, kw: ok("한 번 더 확인해 볼래?")
    aid, p, wrong = _attempt(seeded, learner)
    learn.answer(seeded, aid, wrong)
    _, system, user, _ = fake.calls[-1]
    assert p["answer"] not in system + user
    assert "x^2 - 2x - 15" not in user.replace(wrong, "")
    # 틀린 단계 직전까지의 단계만 전달 (error_step 이후 단계는 없다)
    chosen = next(c for c in p["choices"] if c["value"] == wrong)
    later = [s["text"] for s in p["steps"] if s["no"] >= chosen["error_step"]]
    assert not any(t in user for t in later)


@pytest.mark.parametrize("bad,reason", [
    ("정답은 x^2 - 2x - 15 이야.", "leak"),
    ("다시 해 보세요.", "formal_speech"),
    ("가" * 250, "too_long"),
])
def test_bad_ai_text_is_discarded(seeded, learner, fake, bad, reason):
    fake.handlers["coach"] = lambda s, u, kw: ok(bad)
    aid, p, wrong = _attempt(seeded, learner)
    out = learn.answer(seeded, aid, wrong)
    assert out["coaching"]["source"] == "stored"
    row = seeded.execute("SELECT ok, fail_reason FROM ai_calls WHERE purpose='coach'").fetchone()
    assert row["ok"] == 0 and row["fail_reason"] == reason


def test_api_failure_falls_back(seeded, learner, fake):
    from app import llm
    fake.handlers["coach"] = lambda s, u, kw: llm.Result(False, fail_reason="timeout")
    aid, p, wrong = _attempt(seeded, learner)
    assert learn.answer(seeded, aid, wrong)["coaching"]["source"] == "stored"


def test_daily_limit_switches_to_stored(seeded, learner, fake, monkeypatch):
    fake.handlers["coach"] = lambda s, u, kw: ok("한 번 더 확인해 볼래?")
    config.cfg()["coach"]["daily_limit"] = 2
    try:
        sources = []
        for _ in range(4):
            aid, p, wrong = _attempt(seeded, learner)
            sources.append(learn.answer(seeded, aid, wrong)["coaching"]["source"])
        assert sources == ["ai", "ai", "stored", "stored"]
    finally:
        config.reload()


def test_step_help_cache_and_fallback(seeded, learner, fake):
    fake.handlers["step_help"] = lambda s, u, kw: ok("x에 (x−5)를 곱하면 x²과 −5x가 나와. 이렇게 하나씩 곱해 봐.")
    r = learn.start_attempt(seeded, learner, "normal", "m3-poly")
    aid = r["attempt_id"]
    a = learn.step_help(seeded, aid, 1, 1)
    b = learn.step_help(seeded, aid, 1, 1)
    assert a["text"] == b["text"] and a["ai_call_id"] and b["ai_call_id"] is None
    assert len([c for c in fake.calls if c[0] == "step_help"]) == 1       # 같은 요청은 다시 호출하지 않음
    assert a["can_more"] and learn.step_help(seeded, aid, 1, 3)["can_more"] is False   # "한 번 더 쉽게" 최대 2회
    # 호출 실패 시 해당 단계 text 만 표시
    from app import llm
    fake.handlers["step_help"] = lambda s, u, kw: llm.Result(False, fail_reason="timeout")
    p = problems.load(seeded, r["problem"]["id"])
    c = learn.step_help(seeded, aid, 2, 1)
    assert c["text"] == p["steps"][1]["text"] and c["ai_call_id"] is None


def test_rating_saved(seeded, learner, fake):
    fake.handlers["coach"] = lambda s, u, kw: ok("한 번 더 확인해 볼래?")
    aid, p, wrong = _attempt(seeded, learner)
    out = learn.answer(seeded, aid, wrong)
    learn.rate_call(seeded, out["ai_call_id"], 1)
    assert seeded.execute("SELECT helpful FROM ai_calls WHERE id=?", (out["ai_call_id"],)).fetchone()["helpful"] == 1
