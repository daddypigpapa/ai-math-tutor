"""2단계 검증 기준: 점수 10/5/0, 계산단계 본 뒤 5/2/0, 정답이 선택 전 응답에 없음, 문제집 순서·이어 풀기."""
import json

import pytest

from app import learn, problems


def _problem(conn, aid):
    row = conn.execute("SELECT problem_id FROM attempts WHERE id=?", (aid,)).fetchone()
    return problems.load(conn, row["problem_id"])


def _correct(p):
    return next(c["value"] for c in p["choices"] if c["correct"])


def _wrongs(p):
    return [c["value"] for c in p["choices"] if not c["correct"]]


def start(conn, lid):
    return learn.start_attempt(conn, lid, "normal", "m3-poly")["attempt_id"]


def test_first_try_correct_10(seeded, learner):
    aid = start(seeded, learner)
    out = learn.answer(seeded, aid, _correct(_problem(seeded, aid)))
    assert out["correct"] and out["score"] == 10 and out["result"] == "first" and out["finished"]


def test_retry_correct_5(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    w = learn.answer(seeded, aid, _wrongs(p)[0])
    assert not w["correct"] and not w["finished"] and w["coaching"]["text"]
    out = learn.answer(seeded, aid, _correct(p))
    assert out["score"] == 5 and out["result"] == "retry"


def test_two_wrong_is_0(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.answer(seeded, aid, _wrongs(p)[0])
    out = learn.answer(seeded, aid, _wrongs(p)[1])
    assert out["score"] == 0 and out["result"] == "wrong" and out["finished"] and out["steps"] and out["answer"] == p["answer"]


def test_scores_after_viewing_steps(seeded, learner):
    # 첫 시도 정답 5점
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.steps(seeded, aid)
    assert learn.answer(seeded, aid, _correct(p))["score"] == 5
    # 재시도 정답 2점
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.steps(seeded, aid)
    learn.answer(seeded, aid, _wrongs(p)[0])
    assert learn.answer(seeded, aid, _correct(p))["score"] == 2
    # 오답 0점
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.steps(seeded, aid)
    learn.answer(seeded, aid, _wrongs(p)[0])
    assert learn.answer(seeded, aid, _wrongs(p)[1])["score"] == 0


def test_hint_and_intent_do_not_reduce_score(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.hint(seeded, aid)
    learn.intent(seeded, aid)
    assert learn.answer(seeded, aid, _correct(p))["score"] == 10


def test_nothing_reveals_answer_before_choosing(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    pre = [learn.home(seeded, learner), learn.hint(seeded, aid), learn.intent(seeded, aid), learn.choices(seeded, aid)]
    started = learn.start_attempt(seeded, learner, "normal", "m3-poly")
    pre.append(started)
    text = json.dumps(pre, ensure_ascii=False)
    for forbidden in ('"correct"', '"error_type"', '"feedback"', '"answer"', "wrong_step_sympy"):
        assert forbidden not in text
    assert len(learn.choices(seeded, aid)["choices"]) == 4


def test_retry_choices_exclude_first_wrong(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    first = _wrongs(p)[0]
    learn.answer(seeded, aid, first)
    c = learn.choices(seeded, aid)
    assert c["retry"] and len(c["choices"]) == 3 and first not in c["choices"]
    with pytest.raises(learn.LearnError):
        learn.answer(seeded, aid, first)


def test_choices_order_is_shuffled(seeded, learner):
    aid = start(seeded, learner)
    orders = {tuple(learn.choices(seeded, aid)["choices"]) for _ in range(25)}
    assert len(orders) > 1


def test_workbook_order_then_ai_and_resume(seeded, learner):
    # 문제집 문제 둘(번호 5, 2)과 AI 문제(시작 문제)가 있을 때: 문제집 번호 순 → AI 문제 뒤
    for no in (5, 2):
        p = dict(problems.load(seeded, "m3-poly-s01"))
        p.update(id=f"m3-poly-{no:04d}", source="workbook", seq=no)
        problems.save_and_index(seeded, p)
    ids = learn.ordered_ids(seeded, "m3-poly")
    assert ids[:2] == ["m3-poly-0002", "m3-poly-0005"] and ids[2] == "m3-poly-s01"
    served = []
    for _ in range(4):
        r = learn.start_attempt(seeded, learner, "normal", "m3-poly")
        served.append(r["problem"]["id"])
        learn.answer(seeded, r["attempt_id"], _correct(problems.load(seeded, r["problem"]["id"])))
    assert served == ids[:4]
    # 이어 풀기: 마지막으로 푼 위치 다음부터
    assert learn.start_attempt(seeded, learner, "normal", "m3-poly")["problem"]["id"] == ids[4]


def test_ten_problems_to_the_end(seeded, learner):
    for _ in range(10):
        r = learn.start_attempt(seeded, learner, "normal", "m3-poly")
        assert not r["done"]
        learn.answer(seeded, r["attempt_id"], _correct(problems.load(seeded, r["problem"]["id"])))
    g = learn.goal_progress(seeded, learner)
    assert g == {"done": 10, "target": 10}


def test_only_verified_problems_are_served(seeded, learner):
    seeded.execute("UPDATE problems SET status='draft'")
    seeded.commit()
    assert learn.start_attempt(seeded, learner, "normal", "m3-poly")["done"] is True


def test_answer_after_finish_is_rejected(seeded, learner):
    aid = start(seeded, learner)
    learn.answer(seeded, aid, _correct(_problem(seeded, aid)))
    with pytest.raises(learn.LearnError):
        learn.answer(seeded, aid, _correct(_problem(seeded, aid)))


def test_unknown_value_rejected(seeded, learner):
    aid = start(seeded, learner)
    with pytest.raises(learn.LearnError):
        learn.answer(seeded, aid, "999")


def test_events_are_recorded_in_order(seeded, learner):
    aid = start(seeded, learner)
    p = _problem(seeded, aid)
    learn.hint(seeded, aid)
    learn.steps(seeded, aid)
    learn.answer(seeded, aid, _wrongs(p)[0])
    learn.answer(seeded, aid, _correct(p))
    kinds = [r["kind"] for r in seeded.execute("SELECT kind FROM attempt_events WHERE attempt_id=? ORDER BY id", (aid,))]
    assert kinds == ["hint", "steps", "answer", "answer"]
