"""Страница подбора локально, без паролей: данные — через гостевые запросы рабочего сервера.

    uvicorn app.podbor.dev:app --port 8091      → http://127.0.0.1:8091/podbor
    PODBOR_REMOTE=https://… — другой сервер (по умолчанию рабочий)
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from ..main import PodborFeedback, PodborIn, PodborTextIn
from .cli import warranty_brands
from .engine import Engine, draft
from .journal import Journal
from .sources import Remote

REMOTE = os.environ.get("PODBOR_REMOTE", "https://109.73.199.217")
PAGE = Path(__file__).resolve().parent.parent / "static" / "podbor.html"

app = FastAPI(docs_url=None, redoc_url=None)
src = Remote(REMOTE)
CACHE = Path(os.environ.get("PODBOR_CACHE") or Path(__file__).resolve().parents[2] / ".podbor-cache")  # server/.podbor-cache
engine = Engine(src, CACHE, warranty_brands())
journal = Journal(CACHE / "journal")   # как на сервере: реплики, ответы и оценки — в .podbor-cache/journal


@app.get("/podbor")
async def page():
    return FileResponse(PAGE, media_type="text/html; charset=utf-8")


@app.post("/v1/podbor")
async def podbor(body: PodborIn):
    res = await engine.run(body.text, body.vehicle, body.memory, body.analogs)
    res["text"] = draft(res, body.numbers, body.analogs)
    journal.turn(body.dialog or "", body.turn or 0, body.text, res)
    return res


@app.post("/v1/podbor/feedback")
async def feedback(body: PodborFeedback):
    journal.feedback(body.dialog, body.turn, body.good, body.comment)
    return {"ok": True}


@app.post("/v1/podbor/text")
async def podbor_text(body: PodborTextIn):
    return {"text": draft(body.result, body.numbers, body.analogs)}


@app.post("/v1/laximo/{method}")
async def laximo(method: str, params: dict[str, str]):
    r = await src.http.post(f"/v1/laximo/{method}", json=params)   # схема узла и её разметка
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.get("/")
async def root():
    return Response(status_code=307, headers={"Location": "/podbor"})


@app.on_event("shutdown")
async def close():
    await src.close()
