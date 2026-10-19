"""5단계 검증 기준: 화면 숫자가 DB 조회 결과와 일치, 보고서 숫자가 계산값과 같음, 디스코드 전송. + HTTP API 흐름."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config, db, learn, problems, report, stats
from tests.conftest import ok


def _correct(p):
    return next(c["value"] for c in p["choices"] if c["correct"])


def _wrong(p, i=0):
    return [c["value"] for c in p["choices"] if not c["correct"]][i]


def play(conn, lid, results):
    """results: 'first' | 'steps' | 'retry' | 'wrong'"""
    for res in results:
        r = learn.start_attempt(conn, lid, "normal", "m3-poly")
        p = problems.load(conn, r["problem"]["id"])
        aid = r["attempt_id"]
        if res == "steps":
            learn.steps(conn, aid)
        if res in ("first", "steps"):
            learn.answer(conn, aid, _correct(p))
        elif res == "retry":
            learn.answer(conn, aid, _wrong(p))
            learn.answer(conn, aid, _correct(p))
        else:
            learn.answer(conn, aid, _wrong(p, 0))
            learn.answer(conn, aid, _wrong(p, 1))


# ---- 보고서 --------------------------------------------------------------

def test_report_numbers_match_db(seeded, learner):
    play(seeded, learner, ["first"] * 6 + ["steps", "retry", "wrong", "wrong"])
    ws = report.monday(date.today())
    st = report.compute_stats(seeded, learner, ws)
    assert st["solved"] == 10 and st["goal_days"] == 1
    u = st["units"][0]
    assert u["n"] == 10 and u["rate_pct"] == 60 and u["sentence"] == config.cfg()["report"]["tiers"]["t60"]
    assert u["change_pp"] is None                        # 지난주 데이터가 없으면 변화 문구를 붙이지 않는다
    assert sum(m["count"] for m in st["top_mistakes"]) <= 4 + 1 + 2
    text = "\n".join(report.fact_lines(st))
    for n in report.required_numbers(st):
        assert n in text


def test_tier_sentences():
    t = config.cfg()["report"]["tiers"]
    assert report.tier_sentence(0.95) == t["t90"] and report.tier_sentence(0.9) == t["t90"]
    assert report.tier_sentence(0.8) == t["t75"] and report.tier_sentence(0.6) == t["t60"]
    assert report.tier_sentence(0.59) == t["t0"]


def test_llm_report_with_altered_numbers_is_rejected(seeded, learner, fake, monkeypatch):
    play(seeded, learner, ["first"] * 4)
    sent = []
    monkeypatch.setattr(report, "send_discord", lambda body: (sent.append(body) or True, ""))
    fake.handlers["report"] = lambda s, u, kw: ok("하늘은 이번 주에 99문제를 풀었다.")      # 숫자를 바꿈
    r = report.generate_report(seeded, learner, report.monday(date.today()))
    assert "99" not in r["body"] and "4개" in r["body"]                                   # 정해진 문장으로 대체
    assert sent and sent[0] == r["body"] and r["discord_sent"]


def test_llm_report_accepted_when_numbers_kept(seeded, learner, fake, monkeypatch):
    play(seeded, learner, ["first"] * 4)
    monkeypatch.setattr(report, "send_discord", lambda body: (True, ""))
    st = report.compute_stats(seeded, learner, report.monday(date.today()))

    def good(system, user, kw):
        return ok(f"하늘은 이번 주에 {st['solved']}문제를 풀었고 하루 목표를 채운 날은 {st['goal_days']}일이다. "
                  f"정답률은 {st['units'][0]['rate_pct']}%({st['units'][0]['n']}문제)이다.")
    fake.handlers["report"] = good
    r = report.generate_report(seeded, learner, report.monday(date.today()))
    assert r["body"].startswith("하늘은 이번 주에")


def test_discord_failure_is_recorded(seeded, learner, monkeypatch):
    monkeypatch.setattr(report, "send_discord", lambda body: (False, "HTTP 500"))
    r = report.generate_report(seeded, learner, report.monday(date.today()))
    row = seeded.execute("SELECT discord_sent, stats_json FROM weekly_reports").fetchone()
    assert row["discord_sent"] == 0 and "HTTP 500" in row["stats_json"] and r["discord_error"] == "HTTP 500"


def test_send_discord_without_webhook():
    assert report.send_discord("x") == (False, "DISCORD_WEBHOOK_URL 없음")


# ---- HTTP API -----------------------------------------------------------

@pytest.fixture
def client(seeded, learner, monkeypatch):
    monkeypatch.setenv("PARENT_PIN", "1234")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_http_full_flow(client, learner):
    assert client.get("/").status_code == 200 and client.get("/parent").status_code == 200
    assert client.get("/api/learners").json() == [{"id": learner, "name": "하늘"}]
    home = client.get(f"/api/home?learner={learner}").json()
    assert home["units"][0]["total"] == 12 and home["goal"] == {"done": 0, "target": 10}
    r = client.post("/api/attempts", json={"learner": learner, "unit": "m3-poly"}).json()
    aid = r["attempt_id"]
    assert "answer" not in str(r)
    ch = client.post(f"/api/attempts/{aid}/choices").json()
    assert len(ch["choices"]) == 4 and all(isinstance(c, str) for c in ch["choices"])
    # 선택지 중 정답을 서버에 물어 찾는다 (브라우저에는 정답 표시가 없다)
    conn = db.connect()
    p = problems.load(conn, r["problem"]["id"])
    conn.close()
    out = client.post(f"/api/attempts/{aid}/answer", json={"value": _correct(p)}).json()
    assert out["correct"] and out["score"] == 10 and out["goal"]["done"] == 1


def test_parent_requires_pin(client, learner):
    assert client.get(f"/api/parent/summary?learner={learner}").status_code == 401
    assert client.get(f"/api/parent/summary?learner={learner}", headers={"X-Parent-Pin": "0000"}).status_code == 401
    assert client.get(f"/api/parent/summary?learner={learner}", headers={"X-Parent-Pin": "1234"}).status_code == 200


def test_parent_locked_without_pin(seeded, learner, monkeypatch):
    from app.main import app
    with TestClient(app) as c:
        assert c.get(f"/api/parent/summary?learner={learner}", headers={"X-Parent-Pin": ""}).status_code == 503


def test_parent_numbers_match_db(client, seeded, learner):
    play(seeded, learner, ["first", "first", "steps", "retry", "wrong"])
    H = {"X-Parent-Pin": "1234"}
    s = client.get(f"/api/parent/summary?learner={learner}", headers=H).json()
    row = seeded.execute("SELECT COUNT(*) n, SUM(score) s FROM attempts WHERE ended_at IS NOT NULL").fetchone()
    assert s["today"]["solved"] == row["n"] == 5 and s["today"]["score"] == row["s"]
    assert s["days"][-1]["solved"] == 5 and s["days"][-1]["achieved"] is False
    u = client.get(f"/api/parent/units?learner={learner}", headers=H).json()[0]
    st = stats.unit_stats(seeded, learner, "m3-poly")
    assert u["n"] == st["n"] == 5 and u["independent"] == st["independent"] == 2 and u["helped"] == st["helped"] == 1
    assert u["rate_pct"] == 40
    att = client.get(f"/api/parent/attempts?learner={learner}", headers=H).json()
    assert len(att) == 5 and all(a["sequence"] for a in att)
    assert any("c" in a["sequence"] for a in att)


def test_parent_review_queue_and_approve_discard(client, seeded):
    p = problems.load(seeded, "m3-poly-s01")
    bad = dict(p, id="m3-poly-bad", status="draft", status_reason="answer_mismatch", issues=[], draft={
        "status": "ok", "reason": "", "hint": p["hint"], "intent": p["intent"], "steps": p["steps"],
        "distractors": [{k: c[k] for k in ("error_type", "error_step", "wrong_step_sympy", "feedback")} for c in p["choices"] if not c["correct"]]})
    bad.pop("choices")
    problems.save_and_index(seeded, bad)
    H = {"X-Parent-Pin": "1234"}
    q = client.get("/api/parent/review-queue", headers=H).json()
    assert [x["id"] for x in q] == ["m3-poly-bad"]
    r = client.post("/api/parent/approve", headers=H, json={"problem_id": "m3-poly-bad", "action": "approve"}).json()
    assert r == {"ok": True, "status": "verified"}
    r = client.post("/api/parent/approve", headers=H, json={"problem_id": "m3-poly-bad", "action": "discard"}).json()
    assert r["status"] == "retired"
    assert seeded.execute("SELECT status FROM problems WHERE id='m3-poly-bad'").fetchone()["status"] == "retired"


def test_parent_settings_add_learner_and_goal(client):
    H = {"X-Parent-Pin": "1234"}
    new = client.post("/api/parent/learners", headers=H, json={"name": "바다", "daily_goal": 7}).json()["id"]
    names = [x["name"] for x in client.get("/api/learners").json()]
    assert "바다" in names
    client.post(f"/api/parent/learners/{new}/goal", headers=H, json={"daily_goal": 12})
    assert client.get(f"/api/home?learner={new}").json()["goal"]["target"] == 12


def test_costs_endpoint(client, seeded):
    seeded.execute("INSERT INTO ai_calls(purpose,provider,model,input_tokens,output_tokens,ok,at) VALUES('coach','gemini','gemini-2.5-flash-lite',1000000,1000000,1,?)", (db.now(),))
    seeded.commit()
    c = client.get("/api/parent/costs", headers={"X-Parent-Pin": "1234"}).json()
    assert abs(c["오늘"]["total_usd"] - (0.10 + 0.40)) < 1e-6 and c["오늘"]["by_purpose"]["coach"] > 0
