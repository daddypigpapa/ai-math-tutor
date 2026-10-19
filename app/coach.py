"""실시간 AI 코칭 (명세 8절). (가) 오답 직후 코칭, (나) 계산단계 추가 설명.

두 호출 모두 실패하면 미리 만든 문장으로 대체되므로 API가 끊겨도 학습은 계속된다.
정답 누설 방지 3겹: ① 정답을 프롬프트에 넣지 않는다 ② 금지 이유를 프롬프트에 적는다 ③ 응답을 여기서 검사한다.
"""
import json
import re


from . import config, db, llm, prompts, stats, taxonomy

_MD = re.compile(r"(\*\*|__|`|^\s*#|^\s*[-*•]\s|\[[^\]]+\]\([^)]+\))", re.M)
_FORMAL = re.compile(r"(요|습니다|입니다|세요|까요)[\s.?!~]*$")
_SUP = {"²": "^2", "³": "^3", "⁴": "^4"}


# ---- 검사 ----------------------------------------------------------------

def _norm(s: str) -> str:
    for k, v in _SUP.items():
        s = s.replace(k, v)
    s = s.replace("−", "-").replace("–", "-").replace("×", "*").replace("·", "*").replace("⋅", "*")
    s = re.sub(r"\\(left|right|cdot|,|;|!)", "", s)
    s = s.replace("{", "").replace("}", "").replace("$", "").replace("**", "^")
    s = s.replace("*", "").replace(" ", "")
    return s.lower()


def _answer_strings(problem: dict) -> list[str]:
    cands = {problem.get("answer", ""), problem.get("answer_sympy", "")}
    for c in problem.get("choices", []):
        if c.get("correct"):
            cands.add(c.get("value", ""))
    return [n for n in {_norm(c) for c in cands if c} if n]


def leaks(text: str, problem: dict) -> bool:
    """응답에 정답 문자열이나 그 변형(다른 표기·항 순서 바뀜)이 있으면 True."""
    form = problem.get("answer_form")
    answers = _answer_strings(problem)
    if not answers:
        return False
    if form == "number":
        for a in answers:
            if re.search(rf"(?<![\d.]){re.escape(a)}(?![\d.])", _norm(text)):
                return True
        return False
    t = _norm(text)
    if any(a in t for a in answers):
        return True
    # 항 순서가 바뀐 표기·같은 식의 다른 꼴: 수식처럼 보이는 토큰을 파싱해 정답과 비교한다
    from pipeline import verify  # 지연 import (순환 방지)
    ans_src = problem.get("answer_sympy") or problem.get("answer", "")
    try:
        ans = verify.parse(ans_src)
        ans_canon = verify.canon(ans) if form == "factored" else None
    except verify.ParseError:
        return False
    for tok in re.findall(r"[0-9a-z^+\-()/]{3,}", t)[:30]:
        try:
            if form == "factored":
                if verify.canon(verify.parse(tok, evaluate=False, implicit=True)) == ans_canon:
                    return True
            elif verify.parse(tok, implicit=True) == ans:   # 구조가 같은 식만 누설 — 문제 속 곱 (x+4)(x+4) 같은 미전개 꼴은 통과
                return True
        except Exception:
            continue
    return False


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|\n+", text.strip()) if s]


def is_banmal(text: str) -> bool:
    return all(not _FORMAL.search(s) for s in sentences(text))


def check_text(text: str, problem: dict | None = None) -> str | None:
    """통과면 None, 탈락이면 사유 코드."""
    if not text.strip():
        return "empty"
    if len(text) > int(config.cfg()["coach"]["max_chars"]):
        return "too_long"
    if _MD.search(text):
        return "markdown"
    if not is_banmal(text):
        return "formal_speech"
    if problem is not None and leaks(text, problem):
        return "leak"
    return None


def _sanitize(text: str) -> str:
    # 모델이 습관적으로 붙이는 백틱·별표는 지우고, 목록·제목 같은 구조적 마크다운은 check_text 가 거른다
    return text.replace("`", "").replace("**", "").strip().strip('"').strip()


# ---- 일일 한도 -----------------------------------------------------------

def calls_today(conn, learner_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) n FROM ai_calls WHERE learner_id=? AND purpose IN ('coach','step_help') AND at LIKE ?",
        (learner_id, db.today() + "%"),
    ).fetchone()
    return row["n"]


def within_limit(conn, learner_id: int) -> bool:
    return calls_today(conn, learner_id) < int(config.cfg()["coach"]["daily_limit"])


# ---- (가) 오답 직후 코칭 -------------------------------------------------

def _coach_examples() -> str:
    p = config.prompts_dir() / "examples" / "coach.json"
    if not p.is_file():
        return ""
    out = []
    for ex in json.loads(p.read_text(encoding="utf-8")):
        out.append(
            f"<example>\n<problem>{ex['problem']}</problem>\n<steps_until_error>{ex['steps_until_error']}</steps_until_error>\n"
            f"<chosen_answer error_type=\"{ex['error_type']}\" error_name=\"{taxonomy.error_name(ex['error_type'])}\" "
            f"error_step=\"{ex['error_step']}\">{ex['chosen']}</chosen_answer>\n"
            f"<stored_feedback>{ex['stored']}</stored_feedback>\n<recent_history error_type=\"{ex['error_type']}\">{ex['history']}</recent_history>\n"
            f"<coaching>{ex['coaching']}</coaching>\n</example>"
        )
    return "\n".join(out)


def build_coach_prompt(conn, learner_id: int, problem: dict, chosen: dict) -> tuple[str, str]:
    """코칭 프롬프트 조립. 정답과 틀린 단계 이후의 단계는 넣지 않는다 (평가 스크립트도 이 함수를 쓴다)."""
    system, user_tpl = prompts.load(config.task("coach")["prompt"])
    system = prompts.fill(system, examples=_coach_examples())
    err = chosen.get("error_type", "")
    until = [s["text"] for s in problem.get("steps", []) if s["no"] < chosen.get("error_step", 1)]
    picked, expo = stats.error_type_history(conn, learner_id, err, 10)
    history = f"최근 {expo}번 중 {picked}번" if expo else "기록 없음"
    user = prompts.fill(
        user_tpl, problem=problem["question"],
        steps_until_error=" / ".join(until) if until else "없음(첫 단계에서 틀림)",
        chosen=chosen["value"], error_type=err, error_name=taxonomy.error_name(err), error_step=chosen.get("error_step", 1), stored_feedback=chosen.get("feedback", ""), history=history,
    )
    return system, user


def _step_leak(text: str, problem: dict, chosen: dict) -> str | None:
    """틀린 단계 앞의 계산단계는 프롬프트에 들어가므로(명세 8절), 모델이 그 풀이를 새게 하면 탈락시킨다.
    ① 단계 문장을 그대로 베낌  ② 문제·고른 답·저장 피드백에 없는 수(4 이상)를 단계에서 가져와 말함(예: 인수분해의 두 수)."""
    t = _norm(text)
    prev = [s["text"] for s in problem.get("steps", []) if s["no"] < chosen.get("error_step", 1)]
    for s in prev:
        if len(_norm(s)) >= 6 and _norm(s) in t:
            return "leak_step"
    known = set(re.findall(r"\d+", " ".join([problem.get("question", ""), chosen.get("value", ""), chosen.get("feedback", "")])))
    for n in set(re.findall(r"\d+", " ".join(prev))) - known:
        if int(n) >= 4 and re.search(rf"(?<![\d.]){n}(?![\d.])", text):
            return "leak_step"
    return None


def coach_wrong(conn, attempt: dict, problem: dict, chosen: dict) -> dict:
    """반환: {text, ai_call_id, source('ai'|'stored')}. 정답과 이후 단계는 프롬프트에 넣지 않는다."""
    stored = {"text": chosen.get("feedback", ""), "ai_call_id": None, "source": "stored"}
    lid = attempt["learner_id"]
    if not within_limit(conn, lid):
        return stored
    system, user = build_coach_prompt(conn, lid, problem, chosen)
    r = llm.call(conn, "coach", system, user, learner_id=lid, attempt_id=attempt["id"], problem_id=problem["id"],
                 timeout=float(config.cfg()["coach"]["timeout_s"]))
    if not r.ok:
        return stored
    text = _sanitize(r.text)
    reason = check_text(text, problem) or _step_leak(text, problem, chosen)
    if reason:
        llm.mark_failed(conn, r.call_id, reason)
        return stored
    return {"text": text, "ai_call_id": r.call_id, "source": "ai"}


# ---- (나) 계산단계 추가 설명 ---------------------------------------------

LEVEL_HINT = {
    1: "이 단계를 더 잘게 나눠서 설명해줘.",
    2: "한 번 더 쉽게. 더 작은 수나 비유를 써서 설명해줘.",
    3: "마지막 설명이야. 가장 쉬운 말로, 손으로 따라 할 수 있게 설명해줘.",
}


def step_help(conn, attempt: dict, problem: dict, step_no: int, level: int) -> dict:
    """반환: {text, ai_call_id, source('cache'|'ai'|'stored'), level, can_more}."""
    more_max = int(config.cfg()["coach"]["step_help_more"])
    max_level = 1 + more_max
    level = max(1, min(level, max_level))
    steps = problem.get("steps", [])
    step = next((s for s in steps if s["no"] == step_no), None)
    base = {"level": level, "can_more": level < max_level}
    stored_text = step["text"] if step else ""
    row = conn.execute("SELECT text FROM step_help_cache WHERE problem_id=? AND step_no=? AND level=?",
                       (problem["id"], step_no, level)).fetchone()
    if row:
        return {**base, "text": row["text"], "ai_call_id": None, "source": "cache"}
    fallback = {**base, "text": stored_text, "ai_call_id": None, "source": "stored"}
    if not step or not within_limit(conn, attempt["learner_id"]):
        return fallback
    t = config.task("step_help")
    system, user_tpl = prompts.load(t["prompt"])
    prev = next((s["text"] for s in steps if s["no"] == step_no - 1), "없음")
    nxt = next((s["text"] for s in steps if s["no"] == step_no + 1), "없음")
    user = prompts.fill(user_tpl, problem=problem["question"], prev=prev, step=step["text"], next=nxt,
                        level=level, level_hint=LEVEL_HINT[level])
    r = llm.call(conn, "step_help", system, user, learner_id=attempt["learner_id"], attempt_id=attempt["id"],
                 problem_id=problem["id"], timeout=float(config.cfg()["coach"]["timeout_s"]))
    if not r.ok:
        return fallback
    text = _sanitize(r.text)
    reason = check_text(text)  # 단계는 이미 보여주는 내용이므로 정답 누설 검사는 하지 않는다
    if reason:
        llm.mark_failed(conn, r.call_id, reason)
        return fallback
    conn.execute("INSERT OR REPLACE INTO step_help_cache(problem_id,step_no,level,text) VALUES(?,?,?,?)",
                 (problem["id"], step_no, level, text))
    conn.commit()
    return {**base, "text": text, "ai_call_id": r.call_id, "source": "ai"}
