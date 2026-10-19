"""명령줄 실행 (명세 11절): input/ → problems/

  python -m pipeline.run process [--force] [--only m3-poly-0003]
  python -m pipeline.run generate --unit m3-poly --count 5 [--difficulty 1] [--error-type SIGN]
  python -m pipeline.run reindex
"""
import argparse

from app import db, problems
from pipeline import generate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="input/*.json 의 문제를 자료로 만든다")
    p.add_argument("--force", action="store_true")
    p.add_argument("--only")
    g = sub.add_parser("generate", help="AI가 새 문제를 만들고 검증한다")
    g.add_argument("--unit", default="m3-poly")
    g.add_argument("--count", type=int, default=5)
    g.add_argument("--difficulty", type=int, default=1)
    g.add_argument("--error-type")
    sub.add_parser("reindex", help="problems/ 의 JSON 을 DB 에 다시 색인")
    a = ap.parse_args()

    conn = db.connect()
    db.init(conn)
    if a.cmd == "process":
        res = generate.process_input_dir(conn, force=a.force, only=a.only)
    elif a.cmd == "generate":
        res = generate.make_ai_problems(conn, a.unit, a.count, difficulty=a.difficulty, focus=a.error_type)
    else:
        print(problems.reindex_all(conn), "개 색인")
        return
    ok = sum(1 for r in res if r["status"] == "verified")
    print(f"완료: {len(res)}개 처리, verified {ok}개")


if __name__ == "__main__":
    main()
