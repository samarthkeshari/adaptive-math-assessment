"""Backend API for the Adaptive Math Assessment Agent; also serves the frontend.

Run from the project root with a single worker (memory is held in-process, backed by JSONL):
    uvicorn backend.main:app
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.engine import topics
from backend.engine.agent import AgentError, AssessmentAgent
from backend.llm import get_provider
from backend.storage.memory import Memory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", PROJECT_ROOT / "frontend"))
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "agent"):  # tests may inject their own
        app.state.agent = AssessmentAgent(get_provider(), Memory(DATA_DIR))
    yield


app = FastAPI(title="Adaptive Math Assessment Agent", lifespan=lifespan)
# The frontend is plain static files; index.html loads them from /static/.
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.exception_handler(AgentError)
async def _agent_error(_: Request, e: AgentError) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=e.status)


def agent() -> AssessmentAgent:
    return app.state.agent


class StartRequest(BaseModel):
    student_name: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=2, max_length=120)
    grade: str = Field(min_length=1, max_length=20)
    num_questions: int = Field(default=10, ge=3, le=25)
    time_limit_minutes: int | None = Field(default=None, ge=1, le=180)  # None = no session limit
    reattempts: int = Field(default=0, ge=0, le=3)  # extra tries before the answer is shown


class AnswerRequest(BaseModel):
    idx: int
    answer: str = Field(default="", max_length=500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/config")
def config() -> dict:
    return agent().llm.describe()


@app.get("/api/topics")
def list_topics(grade: str | None = None) -> dict:
    """Predefined topics for a grade (all grades if omitted) plus topics students have added."""
    return {"predefined": topics.predefined_for(grade),
            "custom": sorted({t["topic"] for t in agent().memory.custom_topics}, key=str.lower)}


@app.post("/api/tests")
async def start_test(req: StartRequest) -> dict:
    sid = await agent().start(req.student_name.strip(), " ".join(req.topic.split()), req.grade.strip(),
                              req.num_questions, req.time_limit_minutes, req.reattempts)
    return {"test_id": sid}


@app.post("/api/tests/{test_id}/next")
async def next_question(test_id: str) -> dict:
    return await agent().next_item(test_id)


@app.post("/api/tests/{test_id}/answer")
async def submit_answer(test_id: str, req: AnswerRequest) -> dict:
    return await agent().submit(test_id, req.idx, req.answer)


@app.post("/api/tests/{test_id}/finish")
async def finish_test(test_id: str) -> dict:
    return await agent().finish(test_id)


@app.get("/api/tests/{test_id}")
def get_test(test_id: str) -> dict:
    a = agent()
    return a.detail(a.session(test_id))


@app.post("/api/tests/{test_id}/items/{idx}/visual")
async def visual_explanation(test_id: str, idx: int) -> dict:
    return await agent().visual(test_id, idx)


@app.delete("/api/tests/{test_id}")
async def delete_test(test_id: str) -> dict:
    return await agent().delete(test_id)


@app.post("/api/tests/{test_id}/pause")
async def pause_test(test_id: str) -> dict:
    return await agent().pause(test_id)


@app.get("/api/students")
def list_students() -> dict:
    return {"students": agent().memory.list_students()}


@app.get("/api/students/{name}/tests")
def student_tests(name: str) -> dict:
    return {"student_name": name, "tests": agent().history(name)}


@app.get("/api/students/{name}/questions")
def student_questions(name: str) -> dict:
    """Every answered question (answers, solutions, explanations) across all sessions."""
    return agent().question_history(name)


@app.get("/api/students/{name}/progress")
def student_progress(name: str) -> dict:
    return agent().progress(name)


@app.post("/api/students/{name}/progress/summary")
async def student_progress_summary(name: str) -> dict:
    return await agent().write_progress_summary(name)
