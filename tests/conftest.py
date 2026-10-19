"""테스트 공통 준비: 임시 DB·문제 폴더, 실제 API 키 차단, 가짜 LLM."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, llm  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("TUTOR_PROBLEMS", str(tmp_path / "problems"))
    monkeypatch.setenv("TUTOR_INPUT", str(tmp_path / "input"))
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DISCORD_WEBHOOK_URL", "PARENT_PIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "_ENV", {})   # ~/claude/key.env 의 진짜 키가 테스트에 새지 않게 막는다
    config.reload()
    yield


@pytest.fixture
def conn():
    c = db.connect()
    db.init(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    from pipeline import seed
    res = seed.seed(conn, log=lambda *_: None)
    assert all(p["status"] == "verified" for p in res), [(p["id"], p.get("issues")) for p in res if p["status"] != "verified"]
    return conn


@pytest.fixture
def learner(seeded):
    return db.add_learner(seeded, "하늘")


class FakeLLM:
    """llm.call 대체. handlers[task] 가 llm.Result 를 돌려준다. 없으면 no_api_key 처럼 실패."""

    def __init__(self):
        self.handlers = {}
        self.calls = []

    def __call__(self, conn, task, system, user, **kw):
        self.calls.append((task, system, user, kw))
        h = self.handlers.get(task)
        if h is None:
            return llm.Result(False, fail_reason="no_api_key")
        r = h(system, user, kw)
        cur = conn.execute(
            "INSERT INTO ai_calls(learner_id,attempt_id,problem_id,purpose,provider,model,prompt_version,prompt,response,"
            "input_tokens,output_tokens,ms,ok,fail_reason,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kw.get("learner_id"), kw.get("attempt_id"), kw.get("problem_id"), task, "fake", "fake-model", "t",
             user, r.text, 10, 5, 1, 1 if r.ok else 0, r.fail_reason or None, db.now()))
        conn.commit()
        r.call_id = cur.lastrowid
        return r


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "call", f)
    return f


def ok(text="", data=None):
    return llm.Result(True, text=text, data=data)
