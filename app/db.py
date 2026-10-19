"""SQLite 연결과 스키마 (명세 10절). 정답률은 저장하지 않고 조회 때 계산한다."""
import sqlite3
from datetime import datetime

from . import config, taxonomy


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, grade TEXT,
  daily_goal INTEGER DEFAULT 10,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS units (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, ord INTEGER
);
CREATE TABLE IF NOT EXISTS error_types (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, example TEXT
);
CREATE TABLE IF NOT EXISTS problems (
  id TEXT PRIMARY KEY, unit TEXT REFERENCES units(code),
  source TEXT,            -- workbook | ai | ai_review (약점 복습용 자동 생성)
  seq INTEGER,            -- 단원 안 출제 순서
  answer_form TEXT,       -- expanded | factored | number
  difficulty INTEGER,
  status TEXT,            -- draft | verified | unsuitable | retired
  status_reason TEXT,     -- 검증 실패 이슈 코드, 부적합 사유
  prompt_version TEXT,    -- 자료를 만든 프롬프트 버전
  path TEXT,              -- problems/<unit>/<id>.json (problems 폴더 기준 상대경로)
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS problem_error_types (   -- 오답 선택지에 들어 있는 실수유형 (노출 집계용)
  problem_id TEXT REFERENCES problems(id), error_type TEXT REFERENCES error_types(code),
  PRIMARY KEY (problem_id, error_type)
);
CREATE TABLE IF NOT EXISTS review_sessions (       -- 명세 외 추가: 복습 1회(10문제) 상태 보관
  id INTEGER PRIMARY KEY,
  learner_id INTEGER REFERENCES learners(id),
  problem_ids TEXT,       -- JSON 배열
  started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS attempts (              -- 문제 1개 풀이 1회
  id INTEGER PRIMARY KEY,
  learner_id INTEGER REFERENCES learners(id),
  problem_id TEXT REFERENCES problems(id),
  mode TEXT,              -- normal | review
  result TEXT,            -- first | retry | wrong
  used_steps INTEGER,     -- 계산단계(c)를 본 뒤 답했는지 (1이면 정답률에서 오답 처리)
  score INTEGER,
  started_at TEXT, ended_at TEXT,
  review_session_id INTEGER REFERENCES review_sessions(id)   -- 명세 외 추가
);
CREATE TABLE IF NOT EXISTS attempt_events (        -- 문제 안에서 누른 모든 선택
  id INTEGER PRIMARY KEY,
  attempt_id INTEGER REFERENCES attempts(id),
  kind TEXT,              -- hint | intent | steps | answer | step_help
  value TEXT,             -- 고른 답, 단계 번호 등
  error_type TEXT,        -- 오답일 때만
  at TEXT
);
CREATE TABLE IF NOT EXISTS weak_areas (            -- 약점 판정 이력
  id INTEGER PRIMARY KEY,
  learner_id INTEGER REFERENCES learners(id),
  kind TEXT,              -- unit | error_type
  code TEXT,
  started_at TEXT, cleared_at TEXT,
  review_ready INTEGER    -- 복습 문제 자동 준비 완료 여부
);
CREATE TABLE IF NOT EXISTS weekly_reports (
  id INTEGER PRIMARY KEY,
  learner_id INTEGER REFERENCES learners(id),
  week_start TEXT,
  stats_json TEXT,        -- 코드가 계산한 숫자
  body TEXT,              -- 보고서 본문
  discord_sent INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_calls (
  id INTEGER PRIMARY KEY,
  learner_id INTEGER,     -- 실시간 호출만 (일일 한도 집계용)
  attempt_id INTEGER,
  problem_id TEXT,
  purpose TEXT,           -- generate | repair | convert | coach | step_help | report (+ newproblem | judge_*)
  provider TEXT, model TEXT, prompt_version TEXT,
  prompt TEXT,            -- 실제로 보낸 프롬프트 전문
  response TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  ms INTEGER,
  ok INTEGER,             -- 0이면 실패 (시간 초과, 누설 검사 탈락 등)
  fail_reason TEXT,
  helpful INTEGER,        -- 1 도움됨 / 0 별로 / NULL 미평가
  at TEXT
);
CREATE TABLE IF NOT EXISTS step_help_cache (
  problem_id TEXT, step_no INTEGER, level INTEGER, text TEXT,
  PRIMARY KEY (problem_id, step_no, level)
);
CREATE INDEX IF NOT EXISTS ix_attempts_learner ON attempts(learner_id, ended_at);
CREATE INDEX IF NOT EXISTS ix_events_attempt ON attempt_events(attempt_id);
CREATE INDEX IF NOT EXISTS ix_aicalls_learner ON ai_calls(learner_id, at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_report_week ON weekly_reports(learner_id, week_start);
"""


def connect() -> sqlite3.Connection:
    p = config.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for code, name, ord_ in taxonomy.UNITS:
        conn.execute(
            "INSERT INTO units(code,name,ord) VALUES(?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, ord=excluded.ord",
            (code, name, ord_),
        )
    for code, name, example in taxonomy.ERROR_TYPES:
        conn.execute(
            "INSERT INTO error_types(code,name,example) VALUES(?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, example=excluded.example",
            (code, name, example),
        )
    conn.commit()


def add_learner(conn: sqlite3.Connection, name: str, grade: str = "중3", daily_goal: int | None = None) -> int:
    goal = daily_goal or int(config.cfg()["goal"]["daily"])
    cur = conn.execute(
        "INSERT INTO learners(name,grade,daily_goal,created_at) VALUES(?,?,?,?)",
        (name, grade, goal, now()),
    )
    conn.commit()
    return cur.lastrowid


if __name__ == "__main__":
    c = connect()
    init(c)
    print("DB 초기화 완료:", config.db_path())
