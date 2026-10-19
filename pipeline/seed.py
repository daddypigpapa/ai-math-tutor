"""손으로 쓴 시작 문제(seed/*.json)를 검증해 problems/ 에 넣는다. API 키 없이도 학습 화면을 쓸 수 있게 한다.

  python -m pipeline.seed [--learner 이름]

시작 문제도 AI 생성 문제와 똑같이 verify.build_problem 을 통과해야 verified 가 된다.
"""
import argparse
import json

from app import config, db, problems
from pipeline import verify

SEED_VERSION = "seed-v1"


def load_seed_files() -> list[dict]:
    items = []
    for f in sorted((config.ROOT / "seed").glob("*.json")):
        items += json.loads(f.read_text(encoding="utf-8"))
    return items


def seed(conn, log=print) -> list[dict]:
    done = []
    for i, it in enumerate(load_seed_files(), start=1):
        meta = {"id": it["id"], "unit": it.get("unit", "m3-poly"), "source": "ai", "seq": it.get("seq", i),
                "difficulty": it.get("difficulty", 1), "answer_form": it["answer_form"], "question": it["question"],
                "original_sympy": it["original_sympy"], "prompt_version": SEED_VERSION}
        out = {"status": "ok", "reason": "", "hint": it["hint"], "intent": it["intent"],
               "steps": it["steps"], "distractors": it["distractors"]}
        problem, issues = verify.build_problem(meta, out)
        problems.save_and_index(conn, problem)
        log(f"  {it['id']} → {problem['status']} {problem.get('status_reason', '')}")
        for iss in issues:
            log(f"      이슈 {iss}")
        done.append(problem)
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", help="학습자가 없으면 이 이름으로 한 명 만든다")
    a = ap.parse_args()
    conn = db.connect()
    db.init(conn)
    res = seed(conn)
    print(f"시작 문제 {len(res)}개 중 verified {sum(1 for r in res if r['status'] == 'verified')}개")
    if a.learner and not conn.execute("SELECT 1 FROM learners").fetchone():
        db.add_learner(conn, a.learner)
        print("학습자 추가:", a.learner)


if __name__ == "__main__":
    main()
