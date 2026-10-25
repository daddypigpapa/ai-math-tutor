"""생성 뒤 오답 검토(review): 거부된 오답은 부분 수정 재요청으로 넘기고, 끝내 거부되면 draft, 검토 호출이 실패하면 통과."""
import copy

from pipeline import generate, seed
from tests.conftest import ok

S01 = next(i for i in seed.load_seed_files() if i["id"] == "m3-poly-s01")
OUT = {"status": "ok", "reason": "", "hint": S01["hint"], "intent": S01["intent"], "steps": S01["steps"],
       "distractors": S01["distractors"]}
ITEM = {"id": "m3-poly-0001", "unit": "m3-poly", "source": "workbook", "seq": 1, "difficulty": 1,
        "answer_form": "expanded", "question": S01["question"], "latex": "(x+3)(x-5)", "sympy": S01["original_sympy"]}


def _review(*oks):
    return ok(data={"reviews": [{"index": i, "ok": v, "reason": "" if v else "어색한 꼴"} for i, v in enumerate(oks)]})


def _run(conn, fake, reviews):
    seq = iter(reviews or [])
    fake.handlers.update(generate=lambda s, u, kw: ok(data=copy.deepcopy(OUT)),
                         repair=lambda s, u, kw: ok(data=copy.deepcopy(OUT)))
    if reviews is not None:
        fake.handlers["review"] = lambda s, u, kw: next(seq)
    return generate.process_item(conn, dict(ITEM), log=lambda *_: None)


def test_rejected_distractor_goes_to_repair_then_passes(conn, fake):
    p = _run(conn, fake, [_review(True, True, False), _review(True, True, True)])
    assert p["status"] == "verified" and p["repair_tries"] == 1
    repair_user = next(u for t, _, u, _ in fake.calls if t == "repair")
    assert "review_rejected" in repair_user and "distractors[2]" in repair_user
    review_user = next(u for t, _, u, _ in fake.calls if t == "review")
    assert "<shown_value>x^2 + 2x - 15</shown_value>" in review_user    # 학생에게 보일 값을 검토자가 본다


def test_still_rejected_after_repairs_is_draft(conn, fake):
    p = _run(conn, fake, [_review(False, True, True)] * 3)
    assert p["status"] == "draft" and p["status_reason"] == "review_rejected"
    assert p["repair_tries"] == generate.MAX_REPAIRS


def test_unsuitable_is_not_reviewed(conn, fake):
    fake.handlers.update(generate=lambda s, u, kw: ok(data={**OUT, "status": "unsuitable", "reason": "방정식"}),
                         review=lambda s, u, kw: _review(True, True, True))
    p = generate.process_item(conn, dict(ITEM), log=lambda *_: None)
    assert p["status"] == "unsuitable"
    assert not [c for c in fake.calls if c[0] == "review"]


def test_review_call_failure_does_not_block(conn, fake):
    p = _run(conn, fake, None)          # review 처리기 없음 → no_api_key 실패
    assert p["status"] == "verified" and p["repair_tries"] == 0
