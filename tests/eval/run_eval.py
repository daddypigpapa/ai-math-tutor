"""평가 세트 실행·채점 (명세 12절 0·1단계). 프롬프트나 모델을 바꿀 때마다 전체를 다시 돌려 결과를 비교한다.

  python -m tests.eval.run_eval [--limit N] [--judge] [--compare 이전결과.json]

채점 기준 (초안, 1단계 결과를 보고 조정)
  생성-1  재시도 2회 안에 검증 통과 95% 이상            → 코드 (이슈 코드 집계)   [tricky 중 expect 가 있는 항목은 제외]
  생성-2  오답이 '그 실수유형으로 실제 학생이 낼 만한 답' 1~5점 중 4점 이상이 90% 이상
          → LLM 채점, 생성 모델과 다른 모델 (config.toml 의 tasks.judge_generate)  [--judge 일 때만]
결과는 tests/eval/results/<시각>/ 에 저장한다 (임시 DB·problems 폴더 포함 — 실제 data/ 는 건드리지 않는다).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--compare")
    a = ap.parse_args()

    out_dir = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TUTOR_DB"] = str(out_dir / "eval.db")
    os.environ["TUTOR_PROBLEMS"] = str(out_dir / "problems")

    from app import config, db, llm, prompts, taxonomy
    from pipeline import generate

    if not (config.env("ANTHROPIC_API_KEY")):
        sys.exit("ANTHROPIC_API_KEY 가 없어 생성 평가를 돌릴 수 없다 (.env 확인).")
    conn = db.connect()
    db.init(conn)
    items = json.loads((HERE / "eval_problems.json").read_text(encoding="utf-8"))
    if a.limit:
        items = items[:a.limit]

    rows = []
    for raw in items:
        item = generate.item_from_input(raw)
        item["id"] = f"{item['unit']}-e{raw['no']:04d}"
        print(f"[{item['id']}] {raw['question']}")
        p = generate.process_item(conn, item, log=lambda *_: None)
        rows.append({"id": item["id"], "no": raw["no"], "tricky": raw.get("tricky"), "expect": raw.get("expect"),
                     "status": p["status"], "reason": p.get("status_reason", ""), "repairs": p.get("repair_tries", 0),
                     "issues": [i["code"] for i in p.get("issues", [])]})

    scored = [r for r in rows if not r["expect"]]
    passed = [r for r in scored if r["status"] == "verified" and r["repairs"] <= generate.MAX_REPAIRS]
    gen_rate = len(passed) / len(scored) if scored else 0.0
    issue_counts = Counter(c for r in scored for c in r["issues"])
    summary = {"count": len(rows), "scored": len(scored), "generation_pass_rate": gen_rate,
               "generation_pass_ok": gen_rate >= 0.95, "first_try_pass": sum(1 for r in passed if r["repairs"] == 0),
               "unexpected_failures": [r["id"] for r in scored if r["status"] != "verified"],
               "expect_cases": {r["id"]: r["status"] for r in rows if r["expect"]},
               "issue_counts": dict(issue_counts)}

    if a.judge:
        system, tpl = prompts.load(config.task("judge_generate")["prompt"])
        scores = []
        for r in passed:
            prob = json.loads((config.problems_dir() / "m3-poly" / f"{r['id']}.json").read_text(encoding="utf-8"))
            for c in prob["choices"]:
                if c["correct"]:
                    continue
                user = prompts.fill(tpl, problem=prob["question"], error_type=c["error_type"],
                                    error_name=taxonomy.error_name(c["error_type"]), error_step=c["error_step"],
                                    wrong_step=c["wrong_step_sympy"], value=c["value"])
                res = llm.call(conn, "judge_generate", system, user, schema={
                    "type": "object", "additionalProperties": False, "required": ["score", "reason"],
                    "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}}})
                if res.ok and res.data:
                    scores.append({"id": r["id"], "error_type": c["error_type"], "score": res.data["score"], "reason": res.data["reason"]})
        good = sum(1 for s in scores if s["score"] >= 4)
        summary["plausibility_rate"] = (good / len(scores)) if scores else None
        summary["plausibility_ok"] = bool(scores) and good / len(scores) >= 0.90
        summary["plausibility_judged"] = len(scores)
        (out_dir / "judge.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("저장:", out_dir)

    if a.compare:
        prev = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        before = {r["id"]: r["status"] for r in prev["rows"]}
        for r in rows:
            if before.get(r["id"]) not in (None, r["status"]):
                print(f"  변화 {r['id']}: {before[r['id']]} → {r['status']}")


if __name__ == "__main__":
    main()
