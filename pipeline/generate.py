"""자료 생성 파이프라인 (명세 5절). LLM은 문장과 실수 시나리오를 만들고, 숫자는 verify.py 가 확정한다.

흐름: 입력 → (원문→sympy 변환) → 자료 생성(Claude 1회) → 검증 → 이슈가 있으면 부분 수정 재요청(최대 2회) → 저장.
"""
import json
import re
import threading
import traceback

from app import config, db, llm, problems, prompts, taxonomy
from pipeline import verify

MAX_REPAIRS = 2
JOBS: dict[str, dict] = {}   # 부모 화면에서 보는 백그라운드 작업 상태 (메모리)


# ---- 스키마·프롬프트 -----------------------------------------------------

def generation_schema(error_codes: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["status", "reason", "hint", "intent", "steps", "distractors"],
        "properties": {
            "status": {"type": "string", "enum": ["ok", "unsuitable"]},
            "reason": {"type": "string"},
            "hint": {"type": "string"},
            "intent": {"type": "string"},
            "steps": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["no", "text", "sympy"],
                "properties": {"no": {"type": "integer"}, "text": {"type": "string"}, "sympy": {"type": "string"}}}},
            "distractors": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["error_type", "error_step", "wrong_step_sympy", "feedback"],
                "properties": {
                    "error_type": {"type": "string", "enum": error_codes},
                    "error_step": {"type": "integer"},
                    "wrong_step_sympy": {"type": "string"},
                    "feedback": {"type": "string"}}}},
        },
    }


NEWPROBLEM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["question", "latex", "answer_form"],
    "properties": {"question": {"type": "string"}, "latex": {"type": "string"},
                   "answer_form": {"type": "string", "enum": list(taxonomy.ANSWER_FORMS)}},
}
CONVERT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sympy"],
    "properties": {"sympy": {"type": "string"}},
}


def _taxonomy_text(unit: str) -> str:
    lines = []
    for c, n, ex in taxonomy.ERROR_TYPES:
        if c in taxonomy.error_codes(unit):
            lines.append(f"- {c} ({n}): {taxonomy.DEFINITIONS[c]}. 예 {ex}")
    return "\n".join(lines)


def _examples_text(unit: str) -> str:
    p = config.prompts_dir() / "examples" / "distractors.json"
    out = []
    for ex in json.loads(p.read_text(encoding="utf-8")):
        if ex["error_type"] in taxonomy.error_codes(unit):
            out.append(
                f'  <example error_type="{ex["error_type"]}">{ex["question"]} / error_step={ex["error_step"]} / '
                f'wrong_step_sympy={ex["wrong_step_sympy"]} / feedback={ex["feedback"]}</example>')
    return "\n".join(out)


def system_prompt(unit: str) -> str:
    """고정 부분(역할·규칙·실수유형 사전·예시) — 프롬프트 캐시를 위해 같은 문자열을 유지한다."""
    system, _ = prompts.load(config.task("generate")["prompt"])
    return prompts.fill(system, error_taxonomy=_taxonomy_text(unit), distractor_examples=_examples_text(unit))


# ---- LLM 호출 ------------------------------------------------------------

def _call_with_token_retry(conn, task, system, user, schema, **kw):
    r = llm.call(conn, task, system, user, schema=schema, **kw)
    if not r.ok and r.fail_reason.startswith("bad_json") and "max_tokens" in r.stop_reason:
        r = llm.call(conn, task, system, user, schema=schema,
                     max_tokens=int(config.task(task).get("max_tokens", 4000)) * 2, **kw)
    return r


def convert_to_sympy(conn, latex: str, problem_id: str | None = None) -> str | None:
    t = config.task("convert")
    system, user_tpl = prompts.load(t["prompt"])
    r = llm.call(conn, "convert", system, prompts.fill(user_tpl, latex=latex), schema=CONVERT_SCHEMA,
                 problem_id=problem_id)
    if not r.ok or not r.data:
        return None
    return r.data.get("sympy")


def new_problem_text(conn, unit: str, difficulty: int, answer_form: str, existing: list[str],
                     focus: str | None) -> dict | None:
    t = config.task("newproblem")
    system, user_tpl = prompts.load(t["prompt"])
    user = prompts.fill(
        user_tpl, unit=unit, unit_name=taxonomy.unit_name(unit), difficulty=difficulty, answer_form=answer_form,
        existing="\n".join(f"<q>{q}</q>" for q in existing[-40:]) or "(없음)",
        focus=f"<focus_error_type>{focus}</focus_error_type>\n" if focus else "")
    r = llm.call(conn, "newproblem", system, user, schema=NEWPROBLEM_SCHEMA)
    return r.data if r.ok else None


def _user_message(meta: dict, latex: str, focus: str | None, allowed: list[str]) -> str:
    _, tpl = prompts.load(config.task("generate")["prompt"])
    return prompts.fill(tpl, unit=meta["unit"], difficulty=meta["difficulty"], answer_form=meta["answer_form"],
                        latex=latex, sympy=meta["original_sympy"], allowed_error_types=", ".join(allowed),
                        focus=f"<focus_error_type>{focus}</focus_error_type>\n" if focus else "")


def _repair_message(prev: dict, issues: list[dict]) -> str:
    _, tpl = prompts.load(config.task("repair")["prompt"])
    return prompts.fill(tpl, previous_output=json.dumps(prev, ensure_ascii=False),
                        verification_report=json.dumps(issues, ensure_ascii=False))


# ---- 한 문제 처리 --------------------------------------------------------

def process_item(conn, item: dict, *, focus: str | None = None, log=print) -> dict:
    """item: {id, unit, source, seq, difficulty, answer_form, question, latex?, sympy?}.
    결과 problem JSON 을 저장·색인하고 돌려준다. 실패해도 draft/unsuitable 로 남겨 부모 화면 '검토 필요'에 올린다."""
    unit = item["unit"]
    pv = config.task("generate")["prompt"]
    meta = {"id": item["id"], "unit": unit, "source": item.get("source", "workbook"), "seq": item.get("seq"),
            "difficulty": item.get("difficulty", 1), "answer_form": item["answer_form"],
            "question": item["question"], "prompt_version": pv}
    latex = item.get("latex") or item["question"]

    sym = item.get("sympy")
    if not sym:
        sym = convert_to_sympy(conn, latex, item["id"])
    if not sym:
        return _save_failed(conn, meta, "convert_failed", "원문→sympy 변환 실패(키 없음·응답 실패)")
    meta["original_sympy"] = sym
    try:
        e0 = verify.parse(sym)
    except verify.ParseError as e:
        return _save_failed(conn, meta, "sympy_parse_error", str(e))

    system = system_prompt(unit)
    allowed = verify.applicable_error_types(e0, meta["answer_form"], unit)
    schema = generation_schema(allowed)
    r = _call_with_token_retry(conn, "generate", system, _user_message(meta, latex, focus, allowed), schema,
                               problem_id=item["id"], prompt_version=pv)
    if not r.ok:
        return _save_failed(conn, meta, r.fail_reason or "llm_failed", "자료 생성 호출 실패")
    out = r.data
    problem, issues = verify.build_problem(meta, out)
    tries = 0
    while issues and tries < MAX_REPAIRS:
        tries += 1
        log(f"  [{item['id']}] 이슈 {[i['code'] for i in issues]} → 부분 수정 재요청 {tries}/{MAX_REPAIRS}")
        rr = _call_with_token_retry(conn, "repair", system, _repair_message(out, issues), schema,
                                    problem_id=item["id"], prompt_version=config.task("repair")["prompt"])
        if not rr.ok:
            break
        out = rr.data
        problem, issues = verify.build_problem(meta, out)
    problem["repair_tries"] = tries
    problems.save_and_index(conn, problem)
    log(f"  [{item['id']}] → {problem['status']} {problem.get('status_reason', '')}")
    return problem


def _save_failed(conn, meta: dict, code: str, detail: str) -> dict:
    p = {"id": meta["id"], "unit": meta["unit"], "source": meta.get("source"), "seq": meta.get("seq"),
         "difficulty": meta.get("difficulty", 1), "status": "draft", "status_reason": code,
         "prompt_version": meta.get("prompt_version", ""), "question": meta["question"],
         "answer_form": meta["answer_form"], "original_sympy": meta.get("original_sympy", ""),
         "issues": [verify.issue(code, "pipeline", "", detail)], "hint": "", "intent": "", "steps": []}
    problems.save_and_index(conn, p)
    return p


# ---- 입력 폴더 처리 ------------------------------------------------------

def item_from_input(raw: dict) -> dict:
    """input/ JSON 한 항목 → 파이프라인 항목. 문제집 문제: 번호(no)를 seq·id 로 쓴다."""
    unit = raw["unit"]
    no = int(raw["no"])
    return {"id": f"{unit}-{no:04d}", "unit": unit, "source": "workbook", "seq": no,
            "difficulty": int(raw.get("difficulty", 1)), "answer_form": raw["answer_form"],
            "question": raw["question"], "latex": raw.get("latex"), "sympy": raw.get("sympy")}


def process_input_dir(conn, *, force: bool = False, only: str | None = None, log=print) -> list[dict]:
    done = []
    for f in sorted(config.input_dir().glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for raw in (data if isinstance(data, list) else [data]):
            item = item_from_input(raw)
            if only and item["id"] != only:
                continue
            row = conn.execute("SELECT status FROM problems WHERE id=?", (item["id"],)).fetchone()
            if row and row["status"] in ("verified", "unsuitable") and not force:
                log(f"  [{item['id']}] 이미 {row['status']} — 건너뜀")
                continue
            done.append(process_item(conn, item, log=log))
    return done


# ---- AI 문제 생성 --------------------------------------------------------

def make_ai_problems(conn, unit: str, count: int, *, difficulty: int = 1, focus: str | None = None,
                     source: str = "ai", answer_form: str | None = None, log=print) -> list[dict]:
    """'단원·난이도·기존 문제 예시'를 주고 원문을 만들게 한 뒤, 2단계부터 똑같이 검증한다."""
    made = []
    forms = [answer_form] if answer_form else ["expanded", "factored"]
    existing = problems.existing_questions(conn, unit)
    for i in range(count):
        form = forms[i % len(forms)]
        txt = new_problem_text(conn, unit, difficulty, form, existing, focus)
        if not txt:
            log("  새 문제 원문 생성 실패 — 중단")
            break
        key = re.sub(r"\s+", "", txt["latex"])
        if any(key == re.sub(r"\s+", "", q) for q in existing) or any(key in re.sub(r"\s+", "", q) for q in existing):
            log("  기존 문제와 겹침 — 건너뜀")
            continue
        existing.append(txt["question"])
        item = {"id": problems.next_id(conn, unit), "unit": unit, "source": source,
                "seq": problems.next_seq(conn, unit, source), "difficulty": difficulty,
                "answer_form": txt["answer_form"], "question": txt["question"], "latex": txt["latex"]}
        made.append(process_item(conn, item, focus=focus, log=log))
    return made


# ---- 약점 복습 문제 자동 준비 (백그라운드) -------------------------------

def prepare_review(weak_area_id: int) -> None:
    """약점이 처음 생기면 그 약점을 노리는 문제 N개를 생성·검증해 둔다. 끝나면 review_ready=1."""
    conn = db.connect()
    try:
        w = conn.execute("SELECT * FROM weak_areas WHERE id=?", (weak_area_id,)).fetchone()
        if not w:
            return
        n = int(config.cfg()["weak"]["review_prep_count"])
        unit = w["code"] if w["kind"] == "unit" else taxonomy.UNITS[0][0]
        focus = w["code"] if w["kind"] == "error_type" else None
        job = f"review-{weak_area_id}"
        JOBS[job] = {"status": "running", "what": f"약점 복습 문제 {n}개 ({w['kind']}:{w['code']})"}
        try:
            made = make_ai_problems(conn, unit, n, focus=focus, source="ai_review", log=lambda *_: None)
            JOBS[job] = {**JOBS[job], "status": "done", "made": len([m for m in made if m["status"] == "verified"])}
        except Exception:
            JOBS[job] = {**JOBS[job], "status": "error", "error": traceback.format_exc()[-400:]}
        # 생성이 일부 또는 전부 실패해도 기존 문제로 복습할 수 있으면 버튼을 띄운다
        conn.execute("UPDATE weak_areas SET review_ready=1 WHERE id=?", (weak_area_id,))
        conn.commit()
    finally:
        conn.close()


def start_background(fn, *args, name: str = "job") -> None:
    threading.Thread(target=fn, args=args, name=name, daemon=True).start()


def run_job(name: str, what: str, fn, *args, **kw) -> None:
    """부모 화면에서 요청한 생성 작업을 백그라운드로 돌리고 JOBS 에 상태를 남긴다."""
    def target():
        conn = db.connect()
        JOBS[name] = {"status": "running", "what": what}
        try:
            res = fn(conn, *args, **kw)
            JOBS[name] = {"status": "done", "what": what, "count": len(res) if res is not None else 0}
        except Exception:
            JOBS[name] = {"status": "error", "what": what, "error": traceback.format_exc()[-400:]}
        finally:
            conn.close()
    start_background(target, name=name)
