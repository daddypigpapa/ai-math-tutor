"""문제 JSON 파일 저장·적재와 DB 색인 (명세 4·10절). 문제 본문은 JSON, DB에는 색인만 둔다."""
import json
from pathlib import Path

from . import config, db


def rel_path(unit: str, pid: str) -> str:
    return f"{unit}/{pid}.json"


def save(problem: dict) -> Path:
    p = config.problems_dir() / rel_path(problem["unit"], problem["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(problem, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load(conn, pid: str) -> dict | None:
    row = conn.execute("SELECT path FROM problems WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    p = config.problems_dir() / row["path"]
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def index(conn, problem: dict) -> None:
    """problems 표와 problem_error_types 표를 갱신한다."""
    conn.execute(
        "INSERT INTO problems(id,unit,source,seq,answer_form,difficulty,status,status_reason,prompt_version,path,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "unit=excluded.unit, source=excluded.source, seq=excluded.seq, answer_form=excluded.answer_form, "
        "difficulty=excluded.difficulty, status=excluded.status, status_reason=excluded.status_reason, "
        "prompt_version=excluded.prompt_version, path=excluded.path",
        (problem["id"], problem["unit"], problem.get("source"), problem.get("seq"), problem.get("answer_form"),
         problem.get("difficulty"), problem.get("status"), problem.get("status_reason"),
         problem.get("prompt_version"), rel_path(problem["unit"], problem["id"]), db.now()),
    )
    conn.execute("DELETE FROM problem_error_types WHERE problem_id=?", (problem["id"],))
    for c in problem.get("choices", []):
        if c.get("error_type"):
            conn.execute("INSERT OR IGNORE INTO problem_error_types(problem_id,error_type) VALUES(?,?)",
                         (problem["id"], c["error_type"]))
    conn.commit()


def save_and_index(conn, problem: dict) -> Path:
    p = save(problem)
    index(conn, problem)
    return p


def reindex_all(conn) -> int:
    n = 0
    base = config.problems_dir()
    if not base.is_dir():
        return 0
    for f in sorted(base.glob("*/*.json")):
        try:
            index(conn, json.loads(f.read_text(encoding="utf-8")))
            n += 1
        except Exception as e:  # 깨진 파일 하나가 서버 시작을 막지 않게 한다
            print(f"[reindex] 건너뜀 {f}: {e}")
    return n


def next_seq(conn, unit: str, source: str) -> int:
    row = conn.execute("SELECT MAX(seq) m FROM problems WHERE unit=? AND source=?", (unit, source)).fetchone()
    return (row["m"] or 0) + 1


def next_id(conn, unit: str) -> str:
    """AI 생성 문제 id: <unit>-a<번호>. 문제집 id(<unit>-<번호>)와 겹치지 않는다."""
    row = conn.execute("SELECT COUNT(*) n FROM problems WHERE unit=? AND source!='workbook'", (unit,)).fetchone()
    n = row["n"] + 1
    while conn.execute("SELECT 1 FROM problems WHERE id=?", (f"{unit}-a{n:04d}",)).fetchone():
        n += 1
    return f"{unit}-a{n:04d}"


def existing_questions(conn, unit: str, limit: int = 60) -> list[str]:
    out = []
    for r in conn.execute("SELECT id FROM problems WHERE unit=? ORDER BY created_at DESC LIMIT ?", (unit, limit)):
        p = load(conn, r["id"])
        if p:
            out.append(p.get("question", ""))
    return out
