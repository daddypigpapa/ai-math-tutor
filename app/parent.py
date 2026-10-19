"""부모 API (명세 9·11절). /api/parent/... 는 PIN(X-Parent-Pin 헤더)이 필요하다."""
import hmac
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from . import config, db, llm, problems, report, stats, taxonomy


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def auth(x_parent_pin: str = Header(default="")):
    pin = config.env("PARENT_PIN")
    if not pin:
        raise HTTPException(503, "PARENT_PIN 이 설정되지 않아 부모 화면이 잠겨 있어요 (.env 확인)")
    if not hmac.compare_digest(x_parent_pin.encode(), pin.encode()):
        time.sleep(1)  # 무차별 대입 완화
        raise HTTPException(401, "PIN이 달라요")


router = APIRouter(prefix="/api/parent", dependencies=[Depends(auth)])

EVENT_LETTER = {"hint": "a", "intent": "b", "steps": "c", "answer": "d", "step_help": "c+"}


@router.get("/ping")
def ping():
    return {"ok": True}


# ---- 요약·통계 -----------------------------------------------------------

def _range(conn, lid: int, start: str, end: str) -> dict:
    cap = int(config.cfg()["parent"]["time_cap_s"])
    r = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(score),0) score, "
        "COALESCE(SUM(MIN(?, CAST((julianday(ended_at)-julianday(started_at))*86400 AS INTEGER))),0) secs "
        "FROM attempts WHERE learner_id=? AND ended_at >= ? AND ended_at < ?", (cap, lid, start, end)).fetchone()
    return {"solved": r["n"], "score": r["score"], "minutes": round(r["secs"] / 60, 1)}


@router.get("/summary")
def summary(learner: int, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM learners WHERE id=?", (learner,)).fetchone()
    if not row:
        raise HTTPException(404, "없는 학습자")
    today = date.today()
    target = row["daily_goal"] or int(config.cfg()["goal"]["daily"])
    tomorrow = (today + timedelta(days=1)).isoformat()
    wk = report.monday(today).isoformat()
    days = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        n = conn.execute("SELECT COUNT(*) n FROM attempts WHERE learner_id=? AND ended_at LIKE ?",
                         (learner, d.isoformat() + "%")).fetchone()["n"]
        days.append({"date": d.isoformat(), "solved": n, "target": target, "achieved": n >= target})
    return {"learner": {"id": row["id"], "name": row["name"], "daily_goal": target},
            "today": _range(conn, learner, today.isoformat(), tomorrow),
            "week": _range(conn, learner, wk, tomorrow), "days": days}


@router.get("/units")
def units(learner: int, conn=Depends(get_conn)):
    weak = {w["code"] for w in stats.open_weak(conn, learner) if w["kind"] == "unit"}
    out = []
    for u in conn.execute("SELECT code,name FROM units ORDER BY ord"):
        s = stats.unit_stats(conn, learner, u["code"])
        out.append({"code": u["code"], "name": u["name"], "n": s["n"], "independent": s["independent"],
                    "helped": s["helped"], "rate_pct": None if s["rate"] is None else round(s["rate"] * 100),
                    "weak": u["code"] in weak})
    return out


@router.get("/error-types")
def error_types(learner: int, conn=Depends(get_conn)):
    weak = {w["code"] for w in stats.open_weak(conn, learner) if w["kind"] == "error_type"}
    out = []
    for t in conn.execute("SELECT code,name FROM error_types"):
        s = stats.error_type_stats(conn, learner, t["code"])
        tr = stats.error_type_trend(conn, learner, t["code"])
        pct = lambda v: None if v is None else round(v * 100)
        out.append({"code": t["code"], "name": t["name"], "n": s["n"], "picked": s["picked"],
                    "rate_pct": pct(s["rate"]), "recent_pct": pct(tr["recent"]), "previous_pct": pct(tr["previous"]),
                    "weak": t["code"] in weak})
    return out


@router.get("/attempts")
def attempts(learner: int, limit: int = 50, conn=Depends(get_conn)):
    out = []
    rows = conn.execute(
        "SELECT a.*, p.unit FROM attempts a JOIN problems p ON p.id=a.problem_id "
        "WHERE a.learner_id=? AND a.ended_at IS NOT NULL ORDER BY a.ended_at DESC, a.id DESC LIMIT ?",
        (learner, min(limit, 200))).fetchall()
    for a in rows:
        prob = problems.load(conn, a["problem_id"]) or {}
        ev = conn.execute("SELECT kind,value,error_type FROM attempt_events WHERE attempt_id=? ORDER BY id", (a["id"],)).fetchall()
        seq = " ".join(
            f"{EVENT_LETTER.get(e['kind'], e['kind'])}" + (f"({e['value']}{'×' if e['error_type'] else '○'})" if e["kind"] == "answer" else "")
            for e in ev)
        secs = None
        try:
            secs = int((datetime.fromisoformat(a["ended_at"]) - datetime.fromisoformat(a["started_at"])).total_seconds())
        except Exception:
            pass
        out.append({"id": a["id"], "ended_at": a["ended_at"], "problem_id": a["problem_id"], "mode": a["mode"],
                    "question": prob.get("question", ""), "result": a["result"], "used_steps": a["used_steps"],
                    "score": a["score"], "sequence": seq, "seconds": secs})
    return out


# ---- 문제 관리·검토 -------------------------------------------------------

@router.get("/review-queue")
def review_queue(conn=Depends(get_conn)):
    out = []
    for r in conn.execute("SELECT id,unit,status,status_reason,source FROM problems "
                          "WHERE status IN ('draft','unsuitable') ORDER BY created_at DESC"):
        p = problems.load(conn, r["id"]) or {}
        out.append({"id": r["id"], "unit": r["unit"], "status": r["status"], "reason": r["status_reason"],
                    "question": p.get("question", ""), "issues": p.get("issues", [])})
    return out


@router.get("/problems")
def list_problems(status: str | None = None, source: str | None = None, unit: str | None = None, conn=Depends(get_conn)):
    q, args = "SELECT id,unit,source,seq,answer_form,difficulty,status,status_reason FROM problems WHERE 1=1", []
    for col, v in (("status", status), ("source", source), ("unit", unit)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    return [dict(r) for r in conn.execute(q + " ORDER BY unit, source, seq LIMIT 500", args)]


@router.get("/problems/{pid}")
def get_problem(pid: str, conn=Depends(get_conn)):
    p = problems.load(conn, pid)
    if not p:
        raise HTTPException(404, "없는 문제")
    return p


class ProblemIn(BaseModel):
    unit: str = "m3-poly"
    no: int
    question: str
    latex: str | None = None
    sympy: str | None = None
    answer_form: str
    difficulty: int = 1


@router.post("/problems")
def add_problem(body: ProblemIn):
    if body.answer_form not in taxonomy.ANSWER_FORMS:
        raise HTTPException(400, "answer_form 은 expanded | factored | number")
    from pipeline import generate
    item = generate.item_from_input(body.model_dump())
    generate.run_job(f"input-{item['id']}", f"문제 입력 {item['id']}",
                     lambda conn: [generate.process_item(conn, item)])
    return {"queued": item["id"]}


class GenerateIn(BaseModel):
    unit: str = "m3-poly"
    count: int = 5
    difficulty: int = 1
    error_type: str | None = None


@router.post("/generate")
def generate_problems(body: GenerateIn):
    from pipeline import generate
    n = max(1, min(body.count, 30))
    generate.run_job(f"gen-{int(time.time())}", f"AI 문제 {n}개 생성 ({body.unit})",
                     lambda conn: generate.make_ai_problems(conn, body.unit, n, difficulty=body.difficulty,
                                                            focus=body.error_type, log=lambda *_: None))
    return {"queued": n}


@router.get("/jobs")
def jobs():
    from pipeline import generate
    return generate.JOBS


class ApproveIn(BaseModel):
    problem_id: str
    action: str                 # approve | discard
    edits: dict | None = None   # hint, intent, steps, distractors 중 고친 것


@router.post("/approve")
def approve(body: ApproveIn, conn=Depends(get_conn)):
    from pipeline import verify
    p = problems.load(conn, body.problem_id)
    if not p:
        raise HTTPException(404, "없는 문제")
    if body.action == "discard":
        p["status"], p["status_reason"] = "retired", "부모가 폐기"
        problems.save_and_index(conn, p)
        return {"ok": True, "status": "retired"}
    if body.action != "approve":
        raise HTTPException(400, "action 은 approve | discard")
    out = dict(p.get("draft") or {"hint": p.get("hint", ""), "intent": p.get("intent", ""),
                                  "steps": p.get("steps", []), "distractors": []})
    out["status"], out["reason"] = "ok", ""
    for k in ("hint", "intent", "steps", "distractors"):
        if body.edits and k in body.edits:
            out[k] = body.edits[k]
    meta = {k: p.get(k) for k in ("id", "unit", "source", "seq", "difficulty", "answer_form", "question",
                                  "original_sympy", "prompt_version")}
    new, issues = verify.build_problem(meta, out)
    if issues:
        return {"ok": False, "issues": issues}
    problems.save_and_index(conn, new)
    return {"ok": True, "status": new["status"]}


# ---- AI 호출·비용 --------------------------------------------------------

@router.get("/ai-calls")
def ai_calls(limit: int = 50, purpose: str | None = None, conn=Depends(get_conn)):
    q, args = ("SELECT id,learner_id,purpose,provider,model,prompt_version,input_tokens,output_tokens,ms,ok,fail_reason,"
               "helpful,at FROM ai_calls"), []
    if purpose:
        q += " WHERE purpose=?"
        args.append(purpose)
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [min(limit, 200)])]


@router.get("/ai-calls/{cid}")
def ai_call(cid: int, conn=Depends(get_conn)):
    r = conn.execute("SELECT * FROM ai_calls WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "없는 호출")
    return dict(r)


@router.get("/costs")
def costs(conn=Depends(get_conn)):
    now = datetime.now()
    periods = {"오늘": now.strftime("%Y-%m-%d"), "7일": (now - timedelta(days=6)).strftime("%Y-%m-%d"),
               "30일": (now - timedelta(days=29)).strftime("%Y-%m-%d")}
    out = {}
    for label, since in periods.items():
        by_model, by_purpose, total = {}, {}, 0.0
        for r in conn.execute("SELECT model,purpose,SUM(input_tokens) i,SUM(output_tokens) o,COUNT(*) n FROM ai_calls "
                              "WHERE at >= ? GROUP BY model,purpose", (since,)):
            c = llm.cost_usd(r["model"], r["i"] or 0, r["o"] or 0)
            c = c if c is not None else 0.0
            total += c
            m = by_model.setdefault(r["model"], {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
            m["calls"] += r["n"]; m["input"] += r["i"] or 0; m["output"] += r["o"] or 0; m["usd"] += c
            by_purpose[r["purpose"]] = by_purpose.get(r["purpose"], 0.0) + c
        out[label] = {"total_usd": round(total, 4), "by_model": by_model,
                      "by_purpose": {k: round(v, 4) for k, v in by_purpose.items()}}
    return out


# ---- 보고서·설정 ---------------------------------------------------------

@router.get("/reports")
def reports(learner: int, conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT id,week_start,body,discord_sent,stats_json,created_at FROM weekly_reports WHERE learner_id=? "
        "ORDER BY week_start DESC LIMIT 30", (learner,))]


class ReportIn(BaseModel):
    learner: int
    week_start: str | None = None
    send: bool = False


@router.post("/reports/generate")
def make_report(body: ReportIn, conn=Depends(get_conn)):
    ws = datetime.strptime(body.week_start, "%Y-%m-%d").date() if body.week_start else report.monday(date.today())
    return report.generate_report(conn, body.learner, ws, send=body.send)


@router.get("/learners")
def learners(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute("SELECT id,name,grade,daily_goal FROM learners ORDER BY id")]


class LearnerIn(BaseModel):
    name: str
    grade: str = "중3"
    daily_goal: int | None = None


@router.post("/learners")
def add_learner(body: LearnerIn, conn=Depends(get_conn)):
    if not body.name.strip():
        raise HTTPException(400, "이름이 비었어요")
    return {"id": db.add_learner(conn, body.name.strip(), body.grade, body.daily_goal)}


class GoalIn(BaseModel):
    daily_goal: int


@router.post("/learners/{lid}/goal")
def set_goal(lid: int, body: GoalIn, conn=Depends(get_conn)):
    if body.daily_goal < 1:
        raise HTTPException(400, "1 이상")
    conn.execute("UPDATE learners SET daily_goal=? WHERE id=?", (body.daily_goal, lid))
    conn.commit()
    return {"ok": True}
