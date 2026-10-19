"""정답률·약점 판정·복습 문제 고르기 (명세 7절). 정답률은 저장하지 않고 조회할 때 계산한다.

정의
- 단원 정답률 = (첫 시도 정답 중 계산단계(c)를 보지 않은 문제 수) ÷ 푼 문제 수, 최근 N문제 기준.
  재시도 정답·c를 본 뒤 맞힌 문제는 오답으로 센다. "도움 받아 맞힘" = c를 본 뒤 맞힌(첫 시도/재시도) 문제 수.
- 실수유형 정답률 = 그 유형의 오답이 선택지에 있었던 문제 중, 그 오답을 고르지 않은 비율 (최근 N회 노출 기준).
"""
import json

from . import config, db


def _w() -> dict:
    return config.cfg()["weak"]


# ---- 기본 집계 -----------------------------------------------------------

def unit_stats(conn, learner_id: int, unit: str, window: int | None = None) -> dict:
    window = window or int(_w()["window"])
    rows = conn.execute(
        "SELECT a.result, a.used_steps FROM attempts a JOIN problems p ON p.id=a.problem_id "
        "WHERE a.learner_id=? AND p.unit=? AND a.ended_at IS NOT NULL "
        "ORDER BY a.ended_at DESC, a.id DESC LIMIT ?",
        (learner_id, unit, window),
    ).fetchall()
    n = len(rows)
    independent = sum(1 for r in rows if r["result"] == "first" and not r["used_steps"])
    helped = sum(1 for r in rows if r["result"] in ("first", "retry") and r["used_steps"])
    return {"unit": unit, "n": n, "independent": independent, "helped": helped,
            "rate": (independent / n) if n else None}


def _exposures(conn, learner_id: int, error_type: str, window: int):
    return conn.execute(
        "SELECT a.id, EXISTS(SELECT 1 FROM attempt_events e WHERE e.attempt_id=a.id AND e.kind='answer' "
        "AND e.error_type=?) AS picked "
        "FROM attempts a JOIN problem_error_types t ON t.problem_id=a.problem_id "
        "WHERE a.learner_id=? AND a.ended_at IS NOT NULL AND t.error_type=? "
        "ORDER BY a.ended_at DESC, a.id DESC LIMIT ?",
        (error_type, learner_id, error_type, window),
    ).fetchall()


def error_type_stats(conn, learner_id: int, error_type: str, window: int | None = None) -> dict:
    window = window or int(_w()["window"])
    rows = _exposures(conn, learner_id, error_type, window)
    n = len(rows)
    picked = sum(r["picked"] for r in rows)
    return {"error_type": error_type, "n": n, "picked": picked, "rate": ((n - picked) / n) if n else None}


def error_type_trend(conn, learner_id: int, error_type: str) -> dict:
    """최근 10회 노출 대비 그 앞 10회 노출의 정답률 변화."""
    rows = _exposures(conn, learner_id, error_type, 20)
    def rate(rs):
        return (sum(1 - r["picked"] for r in rs) / len(rs)) if rs else None
    recent, prev = rows[:10], rows[10:20]
    r1, r0 = rate(recent), rate(prev)
    return {"recent": r1, "previous": r0, "delta": (r1 - r0) if (r1 is not None and r0 is not None) else None}


def error_type_history(conn, learner_id: int, error_type: str, n: int = 10) -> tuple[int, int]:
    """(그 오답을 고른 횟수, 노출 횟수) — 최근 n번 노출 기준. 코칭 프롬프트의 '최근 10번 중 4번'."""
    rows = _exposures(conn, learner_id, error_type, n)
    return sum(r["picked"] for r in rows), len(rows)


# ---- 약점 판정 -----------------------------------------------------------

def weak_candidates(conn, learner_id: int) -> dict:
    """현재 약점인 (kind, code) → 정답률. 판정은 문제를 하나 풀 때마다 다시 계산한다."""
    w = _w()
    out = {}
    for u in conn.execute("SELECT DISTINCT p.unit FROM attempts a JOIN problems p ON p.id=a.problem_id "
                          "WHERE a.learner_id=? AND a.ended_at IS NOT NULL", (learner_id,)):
        s = unit_stats(conn, learner_id, u["unit"])
        if s["n"] >= int(w["min_problems"]) and s["rate"] < float(w["threshold"]):
            out[("unit", u["unit"])] = s["rate"]
    for t in conn.execute("SELECT code FROM error_types"):
        s = error_type_stats(conn, learner_id, t["code"])
        if s["n"] >= int(w["min_problems"]) and s["rate"] < float(w["threshold"]):
            out[("error_type", t["code"])] = s["rate"]
    return out


def refresh_weak(conn, learner_id: int) -> list[int]:
    """약점 이력을 갱신한다. 새로 생긴 weak_areas.id 목록을 돌려준다(복습 문제 자동 준비 대상)."""
    cur = weak_candidates(conn, learner_id)
    open_rows = conn.execute(
        "SELECT id, kind, code FROM weak_areas WHERE learner_id=? AND cleared_at IS NULL", (learner_id,)
    ).fetchall()
    open_keys = {(r["kind"], r["code"]): r["id"] for r in open_rows}
    new_ids = []
    for key in cur:
        if key not in open_keys:
            c = conn.execute(
                "INSERT INTO weak_areas(learner_id,kind,code,started_at,review_ready) VALUES(?,?,?,?,0)",
                (learner_id, key[0], key[1], db.now()),
            )
            new_ids.append(c.lastrowid)
    for key, rid in open_keys.items():
        if key not in cur:
            conn.execute("UPDATE weak_areas SET cleared_at=? WHERE id=?", (db.now(), rid))
    conn.commit()
    return new_ids


def open_weak(conn, learner_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM weak_areas WHERE learner_id=? AND cleared_at IS NULL ORDER BY id", (learner_id,))]


# ---- 복습 ----------------------------------------------------------------

def pick_review_problems(conn, learner_id: int, size: int | None = None) -> list[str]:
    """우선순위: ① 예전에 틀린 문제 ② 약점 실수유형이 선택지에 있는 기존 문제 ③ 자동 준비한 AI 문제."""
    size = size or int(_w()["review_size"])
    weak = open_weak(conn, learner_id)
    ets = [w["code"] for w in weak if w["kind"] == "error_type"]
    units = [w["code"] for w in weak if w["kind"] == "unit"]
    picked: list[str] = []

    def add(ids):
        for i in ids:
            if i not in picked and len(picked) < size:
                picked.append(i)

    # ① 예전에 틀린 문제 (오답 처리), 최근 순
    add(r["problem_id"] for r in conn.execute(
        "SELECT a.problem_id, MAX(a.ended_at) m FROM attempts a JOIN problems p ON p.id=a.problem_id "
        "WHERE a.learner_id=? AND a.result='wrong' AND p.status='verified' GROUP BY a.problem_id ORDER BY m DESC",
        (learner_id,)))

    scope_sql, scope_args = [], []
    if ets:
        scope_sql.append("p.id IN (SELECT problem_id FROM problem_error_types WHERE error_type IN (%s))" % ",".join("?" * len(ets)))
        scope_args += ets
    if units:
        scope_sql.append("p.unit IN (%s)" % ",".join("?" * len(units)))
        scope_args += units
    if scope_sql:
        scope = "(" + " OR ".join(scope_sql) + ")"
        # ② 기존 문제 — 가장 오래 안 푼 것부터
        add(r["id"] for r in conn.execute(
            f"SELECT p.id, (SELECT MAX(a.ended_at) FROM attempts a WHERE a.problem_id=p.id AND a.learner_id=?) last_at "
            f"FROM problems p WHERE p.status='verified' AND p.source IN ('workbook','ai') AND {scope} "
            f"ORDER BY last_at IS NOT NULL, last_at, p.seq", [learner_id] + scope_args))
        # ③ 자동 준비한 AI 복습 문제 — 아직 안 푼 것
        add(r["id"] for r in conn.execute(
            f"SELECT p.id FROM problems p WHERE p.status='verified' AND p.source='ai_review' AND {scope} "
            f"AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.problem_id=p.id AND a.learner_id=?) ORDER BY p.id",
            scope_args + [learner_id]))
    return picked


def review_available(conn, learner_id: int) -> bool:
    """홈 화면의 '약한 부분 복습' 버튼: 준비 끝난 약점이 있고 고를 문제가 있을 때."""
    ready = [w for w in open_weak(conn, learner_id) if w["review_ready"]]
    if not ready:
        return False
    return bool(_open_session(conn, learner_id) or pick_review_problems(conn, learner_id))


def _open_session(conn, learner_id: int):
    return conn.execute(
        "SELECT * FROM review_sessions WHERE learner_id=? AND finished_at IS NULL ORDER BY id DESC LIMIT 1",
        (learner_id,)).fetchone()


def next_review_problem(conn, learner_id: int):
    """복습 1회 = 10문제. 반환: (problem_id, session_id, 현재 번호, 총 문제 수) 또는 None."""
    for _ in range(2):
        sess = _open_session(conn, learner_id)
        if not sess:
            ids = pick_review_problems(conn, learner_id)
            if not ids:
                return None
            c = conn.execute("INSERT INTO review_sessions(learner_id,problem_ids,started_at) VALUES(?,?,?)",
                             (learner_id, json.dumps(ids), db.now()))
            conn.commit()
            sess = conn.execute("SELECT * FROM review_sessions WHERE id=?", (c.lastrowid,)).fetchone()
        ids = json.loads(sess["problem_ids"])
        done = {r["problem_id"] for r in conn.execute(
            "SELECT problem_id FROM attempts WHERE review_session_id=? AND ended_at IS NOT NULL", (sess["id"],))}
        remaining = [i for i in ids if i not in done]
        if remaining:
            return remaining[0], sess["id"], len(ids) - len(remaining) + 1, len(ids)
        conn.execute("UPDATE review_sessions SET finished_at=? WHERE id=?", (db.now(), sess["id"]))
        conn.commit()
    return None
