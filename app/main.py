"""FastAPI 진입점과 학습 API (명세 11절).

실행: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, learn, parent, problems
from .parent import get_conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init(conn)
    problems.reindex_all(conn)   # problems/ 의 JSON 중 DB에 없는 것을 색인
    conn.close()
    yield


app = FastAPI(title="AI 가정교사", lifespan=lifespan)
app.include_router(parent.router)


@app.exception_handler(learn.LearnError)
async def learn_error(_, e: learn.LearnError):
    return JSONResponse({"detail": e.msg}, status_code=e.status)


# ---- 화면 ----------------------------------------------------------------

@app.get("/", include_in_schema=False)
def page_learn():
    return FileResponse(config.web_dir() / "learn.html")


@app.get("/parent", include_in_schema=False)
def page_parent():
    return FileResponse(config.web_dir() / "parent.html")


app.mount("/static", StaticFiles(directory=config.web_dir()), name="static")


# ---- 학습 API ------------------------------------------------------------

class AttemptIn(BaseModel):
    learner: int
    mode: str = "normal"
    unit: str | None = None


class AnswerIn(BaseModel):
    value: str


class StepHelpIn(BaseModel):
    step: int
    level: int = 1


class RatingIn(BaseModel):
    helpful: int


@app.get("/api/learners")
def api_learners(conn=Depends(get_conn)):
    return learn.learners(conn)


@app.get("/api/home")
def api_home(learner: int, conn=Depends(get_conn)):
    return learn.home(conn, learner)


@app.post("/api/attempts")
def api_start(body: AttemptIn, conn=Depends(get_conn)):
    return learn.start_attempt(conn, body.learner, body.mode, body.unit)


@app.post("/api/attempts/{aid}/hint")
def api_hint(aid: int, conn=Depends(get_conn)):
    return learn.hint(conn, aid)


@app.post("/api/attempts/{aid}/intent")
def api_intent(aid: int, conn=Depends(get_conn)):
    return learn.intent(conn, aid)


@app.post("/api/attempts/{aid}/steps")
def api_steps(aid: int, conn=Depends(get_conn)):
    return learn.steps(conn, aid)


@app.post("/api/attempts/{aid}/choices")
def api_choices(aid: int, conn=Depends(get_conn)):
    return learn.choices(conn, aid)


@app.post("/api/attempts/{aid}/answer")
def api_answer(aid: int, body: AnswerIn, conn=Depends(get_conn)):
    out = learn.answer(conn, aid, body.value)
    from pipeline import generate
    for wid in out.pop("_new_weak", []):   # 약점이 처음 생기면 복습 문제를 백그라운드로 준비
        generate.start_background(generate.prepare_review, wid, name=f"review-{wid}")
    return out


@app.post("/api/attempts/{aid}/step-help")
def api_step_help(aid: int, body: StepHelpIn, conn=Depends(get_conn)):
    return learn.step_help(conn, aid, body.step, body.level)


@app.post("/api/ai-calls/{cid}/rating")
def api_rating(cid: int, body: RatingIn, conn=Depends(get_conn)):
    learn.rate_call(conn, cid, body.helpful)
    return {"ok": True}
