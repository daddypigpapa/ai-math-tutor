"""학습 흐름·채점·출제 순서 (명세 6·7절).

아이 화면에는 정답·오답 정보를 미리 보내지 않는다. 선택할 때마다 서버에 묻는 구조다.
"""
import random

from . import coach, config, db, problems, stats


class LearnError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


PRAISE = ["잘했어! 정확해.", "좋아, 맞았어!", "멋지다! 계산이 깔끔해.", "그렇지! 잘 풀었어.", "완벽해, 이대로 계속 가보자."]


# ---- 조회 보조 -----------------------------------------------------------

def _learner(conn, lid: int):
    row = conn.execute("SELECT * FROM learners WHERE id=?", (lid,)).fetchone()
    if not row:
        raise LearnError(404, "없는 학습자")
    return row


def _attempt(conn, aid: int, open_only: bool = True):
    row = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not row:
        raise LearnError(404, "없는 풀이")
    if open_only and row["ended_at"]:
        raise LearnError(409, "이미 끝난 문제")
    return dict(row)


def _problem(conn, pid: str) -> dict:
    p = problems.load(conn, pid)
    if not p or p.get("status") != "verified":
        raise LearnError(404, "문제 자료 없음")
    return p


def _event(conn, aid: int, kind: str, value: str = "", error_type: str | None = None) -> None:
    conn.execute("INSERT INTO attempt_events(attempt_id,kind,value,error_type,at) VALUES(?,?,?,?,?)",
                 (aid, kind, value, error_type, db.now()))
    conn.commit()


def learners(conn) -> list[dict]:
    return [{"id": r["id"], "name": r["name"]} for r in conn.execute("SELECT id,name FROM learners ORDER BY id")]


def ordered_ids(conn, unit: str) -> list[str]:
    """단원 출제 순서: 문제집 번호 순 → AI 생성 문제 (복습용 ai_review 는 제외)."""
    return [r["id"] for r in conn.execute(
        "SELECT id FROM problems WHERE unit=? AND status='verified' AND source IN ('workbook','ai') "
        "ORDER BY CASE source WHEN 'workbook' THEN 0 ELSE 1 END, seq, id", (unit,))]


def _solved(conn, lid: int) -> set[str]:
    return {r["problem_id"] for r in conn.execute(
        "SELECT DISTINCT problem_id FROM attempts WHERE learner_id=? AND mode='normal' AND ended_at IS NOT NULL", (lid,))}


def next_problem_id(conn, lid: int, unit: str) -> tuple[str, int, int] | None:
    """마지막으로 푼 위치 다음부터 이어서. 반환: (id, 번호, 총 문제 수)."""
    ids = ordered_ids(conn, unit)
    if not ids:
        return None
    solved = _solved(conn, lid)
    last = conn.execute(
        "SELECT problem_id FROM attempts WHERE learner_id=? AND mode='normal' AND ended_at IS NOT NULL "
        "AND problem_id IN (%s) ORDER BY ended_at DESC, id DESC LIMIT 1" % ",".join("?" * len(ids)),
        [lid] + ids).fetchone()
    start = ids.index(last["problem_id"]) + 1 if last else 0
    for i in list(range(start, len(ids))) + list(range(0, start)):
        if ids[i] not in solved:
            return ids[i], i + 1, len(ids)
    return None


def goal_progress(conn, lid: int) -> dict:
    row = _learner(conn, lid)
    done = conn.execute("SELECT COUNT(*) n FROM attempts WHERE learner_id=? AND ended_at LIKE ?",
                        (lid, db.today() + "%")).fetchone()["n"]
    target = row["daily_goal"] or int(config.cfg()["goal"]["daily"])
    return {"done": done, "target": target}


def home(conn, lid: int) -> dict:
    learner = _learner(conn, lid)
    solved = _solved(conn, lid)
    units = []
    for u in conn.execute("SELECT code,name FROM units ORDER BY ord"):
        ids = ordered_ids(conn, u["code"])
        nxt = next_problem_id(conn, lid, u["code"])
        units.append({"code": u["code"], "name": u["name"], "total": len(ids),
                      "solved": len([i for i in ids if i in solved]),
                      "next_number": nxt[1] if nxt else None})
    return {"learner": {"id": learner["id"], "name": learner["name"]}, "units": units,
            "goal": goal_progress(conn, lid), "review": stats.review_available(conn, lid)}


# ---- 문제 시작 -----------------------------------------------------------

def start_attempt(conn, lid: int, mode: str, unit: str | None) -> dict:
    _learner(conn, lid)
    session_id = None
    if mode == "review":
        nxt = stats.next_review_problem(conn, lid)
        if not nxt:
            raise LearnError(404, "복습할 문제가 없어")
        pid, session_id, number, total = nxt
    elif mode == "normal":
        if not unit:
            first = conn.execute("SELECT code FROM units ORDER BY ord").fetchone()
            unit = first["code"] if first else None
        nxt = next_problem_id(conn, lid, unit) if unit else None
        if not nxt:
            return {"done": True, "goal": goal_progress(conn, lid)}
        pid, number, total = nxt
    else:
        raise LearnError(400, "mode 는 normal | review")
    p = _problem(conn, pid)
    cur = conn.execute(
        "INSERT INTO attempts(learner_id,problem_id,mode,used_steps,started_at,review_session_id) VALUES(?,?,?,?,?,?)",
        (lid, pid, mode, 0, db.now(), session_id))
    conn.commit()
    return {"done": False, "attempt_id": cur.lastrowid, "mode": mode,
            "problem": {"id": pid, "question": p["question"], "number": number, "total": total},
            "goal": goal_progress(conn, lid)}


# ---- a·b·c·d -------------------------------------------------------------

def hint(conn, aid: int) -> dict:
    a = _attempt(conn, aid)
    _event(conn, aid, "hint")
    return {"hint": _problem(conn, a["problem_id"])["hint"]}


def intent(conn, aid: int) -> dict:
    a = _attempt(conn, aid)
    _event(conn, aid, "intent")
    return {"intent": _problem(conn, a["problem_id"])["intent"]}


def steps(conn, aid: int) -> dict:
    a = _attempt(conn, aid)
    _event(conn, aid, "steps")
    conn.execute("UPDATE attempts SET used_steps=1 WHERE id=?", (aid,))
    conn.commit()
    p = _problem(conn, a["problem_id"])
    return {"steps": [{"no": s["no"], "text": s["text"]} for s in p["steps"]]}


def _tried(conn, aid: int) -> set[str]:
    return {r["value"] for r in conn.execute(
        "SELECT value FROM attempt_events WHERE attempt_id=? AND kind='answer'", (aid,))}


def choices(conn, aid: int) -> dict:
    """섞인 선택지(값만). 재시도 때는 처음 고른 오답을 뺀 3개."""
    a = _attempt(conn, aid)
    p = _problem(conn, a["problem_id"])
    tried = _tried(conn, aid)
    opts = [c["value"] for c in p["choices"] if c["value"] not in tried]
    random.shuffle(opts)
    return {"choices": opts, "retry": bool(tried)}


def score_for(result: str, used_steps: bool) -> int:
    s = config.cfg()["scoring"]
    if result == "first":
        return int(s["first_after_steps"] if used_steps else s["first"])
    if result == "retry":
        return int(s["retry_after_steps"] if used_steps else s["retry"])
    return int(s["wrong"])


def answer(conn, aid: int, value: str) -> dict:
    a = _attempt(conn, aid)
    p = _problem(conn, a["problem_id"])
    tried = _tried(conn, aid)
    chosen = next((c for c in p["choices"] if c["value"] == value), None)
    if chosen is None:
        raise LearnError(400, "보기에 없는 값")
    if value in tried:
        raise LearnError(400, "이미 고른 오답")
    used = bool(conn.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='steps' LIMIT 1", (aid,)).fetchone())
    _event(conn, aid, "answer", value, None if chosen.get("correct") else chosen.get("error_type"))

    def finish(result: str) -> int:
        score = score_for(result, used)
        conn.execute("UPDATE attempts SET result=?, used_steps=?, score=?, ended_at=? WHERE id=?",
                     (result, 1 if used else 0, score, db.now(), aid))
        conn.commit()
        return score

    out: dict = {"correct": bool(chosen.get("correct"))}
    if chosen.get("correct"):
        result = "retry" if tried else "first"
        out.update(score=finish(result), result=result, finished=True, praise=random.choice(PRAISE))
    elif tried:
        out.update(score=finish("wrong"), result="wrong", finished=True,
                   steps=[{"no": s["no"], "text": s["text"]} for s in p["steps"]], answer=p["answer"])
    else:
        c = coach.coach_wrong(conn, a, p, chosen)
        out.update(finished=False, coaching={"text": c["text"], "source": c["source"]}, ai_call_id=c["ai_call_id"])
    if out["finished"]:
        out["goal"] = goal_progress(conn, a["learner_id"])
        out["_new_weak"] = stats.refresh_weak(conn, a["learner_id"])  # 라우트가 꺼내 백그라운드 준비를 시작한다
    return out


def step_help(conn, aid: int, step: int, level: int) -> dict:
    a = _attempt(conn, aid)
    p = _problem(conn, a["problem_id"])
    if not any(s["no"] == step for s in p["steps"]):
        raise LearnError(400, "없는 단계")
    _event(conn, aid, "step_help", f"{step}:{level}")
    r = coach.step_help(conn, a, p, step, level)
    return {"text": r["text"], "ai_call_id": r["ai_call_id"], "level": r["level"], "can_more": r["can_more"]}


def rate_call(conn, call_id: int, helpful: int) -> None:
    row = conn.execute("SELECT purpose FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    if not row or row["purpose"] not in ("coach", "step_help"):
        raise LearnError(404, "평가할 수 없는 호출")
    conn.execute("UPDATE ai_calls SET helpful=? WHERE id=?", (1 if helpful else 0, call_id))
    conn.commit()
