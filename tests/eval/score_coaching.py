"""코칭 평가 (명세 12절 3단계 기준). 현재 DB의 verified 문제(또는 --problems 폴더)의 오답마다 코칭을 생성해 채점한다.

  python -m tests.eval.score_coaching [--limit N] [--judge]

  코칭-1  정답 누설 0%            → 코드 (정답 문자열·변형 검사: coach.leaks)
  코칭-2  200자 이하·반말 100%     → 코드 (coach.check_text 의 길이·반말 규칙)
  코칭-3  틀린 단계를 맞게 짚음 90% 이상 → LLM 채점 (tasks.judge_coach, 코칭 모델과 다른 모델)  [--judge]
런타임 필터가 걸러내기 '전'의 모델 원문을 평가한다(필터가 가려 주면 모델 품질을 알 수 없으므로).
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--judge", action="store_true")
    a = ap.parse_args()

    from app import coach, config, db, llm, problems, prompts, taxonomy
    if not config.env("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY 가 없어 코칭 평가를 돌릴 수 없다 (.env 확인).")
    conn = db.connect()
    db.init(conn)
    lid = (conn.execute("SELECT id FROM learners ORDER BY id LIMIT 1").fetchone() or {"id": None})["id"]
    if lid is None:
        lid = db.add_learner(conn, "평가용")

    cases = []
    for r in conn.execute("SELECT id FROM problems WHERE status='verified' ORDER BY id"):
        p = problems.load(conn, r["id"])
        for c in p["choices"]:
            if not c["correct"]:
                cases.append((p, c))
    cases = cases[:a.limit]
    if not cases:
        sys.exit("verified 문제가 없다. 먼저 python -m pipeline.seed 또는 pipeline.run 을 실행하라.")

    rows = []
    for p, c in cases:
        system, user = coach.build_coach_prompt(conn, lid, p, c)
        res = llm.call(conn, "coach", system, user, learner_id=None, problem_id=p["id"], timeout=15)
        text = coach._sanitize(res.text) if res.ok else ""
        rows.append({"problem": p["id"], "error_type": c["error_type"], "ok": res.ok, "fail": res.fail_reason, "text": text,
                     "leak": bool(text) and coach.leaks(text, p),
                     "length_ok": bool(text) and len(text) <= int(config.cfg()["coach"]["max_chars"]),
                     "banmal": bool(text) and coach.is_banmal(text),
                     "_p": p, "_c": c})
    answered = [r for r in rows if r["ok"] and r["text"]]
    summary = {"cases": len(rows), "answered": len(answered),
               "leak_rate": sum(r["leak"] for r in answered) / len(answered) if answered else None,
               "length_and_banmal_rate": sum(r["length_ok"] and r["banmal"] for r in answered) / len(answered) if answered else None}
    summary["leak_ok"] = summary["leak_rate"] == 0
    summary["length_and_banmal_ok"] = summary["length_and_banmal_rate"] == 1

    if a.judge and answered:
        system, tpl = prompts.load(config.task("judge_coach")["prompt"])
        good = 0
        for r in answered:
            p, c = r["_p"], r["_c"]
            user = prompts.fill(tpl, problem=p["question"], error_step=c["error_step"],
                                error_name=taxonomy.error_name(c["error_type"]), wrong_step=c["wrong_step_sympy"], coaching=r["text"])
            j = llm.call(conn, "judge_coach", system, user, schema={
                "type": "object", "additionalProperties": False, "required": ["correct", "reason"],
                "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}}})
            r["judge"] = j.data if j.ok else None
            good += bool(j.ok and j.data and j.data["correct"])
        summary["points_to_wrong_step_rate"] = good / len(answered)
        summary["points_to_wrong_step_ok"] = good / len(answered) >= 0.90

    out_dir = HERE / "results" / ("coach-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        r.pop("_p"); r.pop("_c")
    (out_dir / "coaching.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("저장:", out_dir)


if __name__ == "__main__":
    main()
