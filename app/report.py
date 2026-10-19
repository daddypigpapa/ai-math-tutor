"""주간 보고서 (명세 9-1절). 숫자는 코드가 계산하고, 정답률 구간마다 정해진 문장을 고른다.
LLM은 문장을 이어 붙이기만 하며, 원래 숫자가 결과에 그대로 있는지 코드로 확인한다. 실패하면 정해진 문장 목록을 쓴다.

  python -m app.report [--learner ID|all] [--week-start YYYY-MM-DD] [--no-send]
Windows 작업 스케줄러가 일요일 21시에 실행한다 (scripts/register_tasks.ps1).
"""
import argparse
import json
from datetime import date, datetime, timedelta

import httpx

from . import config, db, llm, prompts, stats, taxonomy


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def tier_sentence(rate: float | None) -> str:
    t = config.cfg()["report"]["tiers"]
    if rate is None:
        return "이번 주에는 푼 문제가 없다"
    if rate >= 0.90:
        return t["t90"]
    if rate >= 0.75:
        return t["t75"]
    if rate >= 0.60:
        return t["t60"]
    return t["t0"]


def _pct(rate: float | None) -> int | None:
    return None if rate is None else round(rate * 100)


def _unit_week(conn, lid: int, unit: str, start: str, end: str) -> dict:
    rows = conn.execute(
        "SELECT a.result, a.used_steps FROM attempts a JOIN problems p ON p.id=a.problem_id "
        "WHERE a.learner_id=? AND p.unit=? AND a.ended_at >= ? AND a.ended_at < ?", (lid, unit, start, end)).fetchall()
    n = len(rows)
    ind = sum(1 for r in rows if r["result"] == "first" and not r["used_steps"])
    return {"n": n, "rate": (ind / n) if n else None}


def _et_week(conn, lid: int, code: str, start: str, end: str) -> dict:
    rows = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM attempt_events e WHERE e.attempt_id=a.id AND e.kind='answer' AND e.error_type=?) picked "
        "FROM attempts a JOIN problem_error_types t ON t.problem_id=a.problem_id "
        "WHERE a.learner_id=? AND t.error_type=? AND a.ended_at >= ? AND a.ended_at < ?",
        (code, lid, code, start, end)).fetchall()
    n = len(rows)
    picked = sum(r["picked"] for r in rows)
    return {"n": n, "picked": picked, "rate": ((n - picked) / n) if n else None}


def compute_stats(conn, lid: int, week_start: date) -> dict:
    learner = conn.execute("SELECT * FROM learners WHERE id=?", (lid,)).fetchone()
    s0, s1 = week_start.isoformat(), (week_start + timedelta(days=7)).isoformat()
    p0 = (week_start - timedelta(days=7)).isoformat()
    thr = int(config.cfg()["report"]["change_threshold_pp"])
    target = learner["daily_goal"] or int(config.cfg()["goal"]["daily"])

    solved = conn.execute("SELECT COUNT(*) n FROM attempts WHERE learner_id=? AND ended_at >= ? AND ended_at < ?",
                          (lid, s0, s1)).fetchone()["n"]
    per_day = conn.execute(
        "SELECT substr(ended_at,1,10) d, COUNT(*) n FROM attempts WHERE learner_id=? AND ended_at >= ? AND ended_at < ? GROUP BY d",
        (lid, s0, s1)).fetchall()
    goal_days = sum(1 for r in per_day if r["n"] >= target)

    def change(cur, prev):
        if cur is None or prev is None:
            return None
        pp = round(cur * 100) - round(prev * 100)
        return pp if abs(pp) >= thr else None

    units = []
    for u in conn.execute("SELECT code,name FROM units ORDER BY ord"):
        cur, prev = _unit_week(conn, lid, u["code"], s0, s1), _unit_week(conn, lid, u["code"], p0, s0)
        if cur["n"] == 0:
            continue
        units.append({"code": u["code"], "name": u["name"], "n": cur["n"], "rate_pct": _pct(cur["rate"]),
                      "sentence": tier_sentence(cur["rate"]), "change_pp": change(cur["rate"], prev["rate"])})
    ets = []
    for t in conn.execute("SELECT code,name FROM error_types"):
        cur, prev = _et_week(conn, lid, t["code"], s0, s1), _et_week(conn, lid, t["code"], p0, s0)
        if cur["n"] == 0:
            continue
        ets.append({"code": t["code"], "name": t["name"], "n": cur["n"], "picked": cur["picked"],
                    "rate_pct": _pct(cur["rate"]), "change_pp": change(cur["rate"], prev["rate"])})
    top = sorted([e for e in ets if e["picked"] > 0], key=lambda e: (-e["picked"], e["code"]))[:3]

    def weak_names(rows):
        return [(taxonomy.unit_name(r["code"]) if r["kind"] == "unit" else taxonomy.error_name(r["code"])) for r in rows]
    new_weak = weak_names(conn.execute(
        "SELECT kind,code FROM weak_areas WHERE learner_id=? AND started_at >= ? AND started_at < ?", (lid, s0, s1)))
    cleared = weak_names(conn.execute(
        "SELECT kind,code FROM weak_areas WHERE learner_id=? AND cleared_at >= ? AND cleared_at < ?", (lid, s0, s1)))
    return {"learner": learner["name"], "week_start": s0, "week_end": (week_start + timedelta(days=6)).isoformat(),
            "target": target, "solved": solved, "goal_days": goal_days, "units": units, "error_types": ets,
            "top_mistakes": [{"name": e["name"], "count": e["picked"]} for e in top],
            "new_weak": new_weak, "cleared_weak": cleared}


def fact_lines(st: dict) -> list[str]:
    L = [f"{st['learner']}의 주간 학습 보고 ({st['week_start']} ~ {st['week_end']})",
         f"이번 주 푼 문제는 {st['solved']}개이고, 하루 목표({st['target']}문제)를 채운 날은 {st['goal_days']}일이다."]
    for u in st["units"]:
        line = f"{u['name']}: 정답률 {u['rate_pct']}% ({u['n']}문제). {u['sentence']}."
        if u["change_pp"] is not None:
            line += f" 지난주보다 {'좋아짐' if u['change_pp'] > 0 else '나빠짐'} ({abs(u['change_pp'])}%p)."
        L.append(line)
    if st["top_mistakes"]:
        L.append("가장 많이 반복된 실수: " + ", ".join(f"{m['name']} {m['count']}회" for m in st["top_mistakes"]) + ".")
    else:
        L.append("이번 주에 반복된 실수는 없다.")
    if st["new_weak"]:
        L.append("새로 생긴 약점: " + ", ".join(st["new_weak"]) + ".")
    if st["cleared_weak"]:
        L.append("해제된 약점: " + ", ".join(st["cleared_weak"]) + ".")
    return L


def required_numbers(st: dict) -> list[str]:
    nums = [str(st["solved"]), str(st["goal_days"])]
    for u in st["units"]:
        nums += [f"{u['rate_pct']}%", str(u["n"])]
        if u["change_pp"] is not None:
            nums.append(f"{abs(u['change_pp'])}%p")
    nums += [str(m["count"]) for m in st["top_mistakes"]]
    return nums


def llm_body(conn, st: dict, lines: list[str]) -> str | None:
    t = config.task("report")
    system, tpl = prompts.load(t["prompt"])
    r = llm.call(conn, "report", system, prompts.fill(tpl, facts="\n".join(lines)), timeout=30)
    if not r.ok:
        return None
    text = r.text.strip()
    missing = [n for n in required_numbers(st) if n not in text]
    if missing or len(text) > 1500 or st["learner"] not in text:
        llm.mark_failed(conn, r.call_id, f"numbers_missing:{','.join(missing)}" if missing else "bad_length_or_name")
        return None
    return text


def send_discord(body: str) -> tuple[bool, str]:
    url = config.env("DISCORD_WEBHOOK_URL")
    if not url:
        return False, "DISCORD_WEBHOOK_URL 없음"
    try:
        for i in range(0, len(body), 1900):
            r = httpx.post(url, json={"content": body[i:i + 1900]}, timeout=15, verify=llm.SSL_CONTEXT)
            if r.status_code >= 300:
                return False, f"HTTP {r.status_code}"
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def generate_report(conn, lid: int, week_start: date, send: bool = True) -> dict:
    st = compute_stats(conn, lid, week_start)
    lines = fact_lines(st)
    body = llm_body(conn, st, lines) or "\n".join(f"- {l}" if i else l for i, l in enumerate(lines))
    sent, err = (send_discord(body) if send else (False, "전송 안 함"))
    st["discord_error"] = "" if sent else err
    conn.execute("DELETE FROM weekly_reports WHERE learner_id=? AND week_start=?", (lid, week_start.isoformat()))
    cur = conn.execute(
        "INSERT INTO weekly_reports(learner_id,week_start,stats_json,body,discord_sent,created_at) VALUES(?,?,?,?,?,?)",
        (lid, week_start.isoformat(), json.dumps(st, ensure_ascii=False), body, 1 if sent else 0, db.now()))
    conn.commit()
    return {"id": cur.lastrowid, "body": body, "discord_sent": sent, "discord_error": st["discord_error"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", default="all")
    ap.add_argument("--week-start")
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    ws = datetime.strptime(a.week_start, "%Y-%m-%d").date() if a.week_start else monday(date.today())
    conn = db.connect()
    db.init(conn)
    ids = [r["id"] for r in conn.execute("SELECT id FROM learners")] if a.learner == "all" else [int(a.learner)]
    for lid in ids:
        r = generate_report(conn, lid, ws, send=not a.no_send)
        print(f"[학습자 {lid}] 전송 {'성공' if r['discord_sent'] else '실패/생략: ' + r['discord_error']}")


if __name__ == "__main__":
    main()
