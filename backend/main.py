"""FastAPI orchestration layer.

POST /api/analyze        run the supervisor pipeline, returns {id, report, summary, trace}
GET  /api/report/{id}    the stored report for a session
POST /api/chat/{id}      ask the ReAct agent a follow-up question (RAG-grounded)
GET  /api/status         which LLM providers are configured
Everything else serves the built React app.
"""
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import Session, answer, compose_summary, llm_status
from analyzer import analyze_site
from fetch import AnalyzeError, normalize_url
from rag import SiteIndex

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("websage")

SESSION_TTL = 2 * 3600
MAX_SESSIONS = 100
SESSIONS: dict[str, Session] = {}

app = FastAPI(title="WebSage - Agentic Website Analyzer")


class AnalyzeRequest(BaseModel):
    url: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


@app.exception_handler(AnalyzeError)
async def handle_analyze_error(_: Request, exc: AnalyzeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


def _expire():
    now = time.time()
    for sid in [s for s, sess in SESSIONS.items() if now - sess.created > SESSION_TTL]:
        SESSIONS.pop(sid).index.drop()
    while len(SESSIONS) >= MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda s: SESSIONS[s].created)
        SESSIONS.pop(oldest).index.drop()


def _get(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if not session:
        raise AnalyzeError(404, "not_found", "This report has expired or doesn't exist. Analyze the site again.")
    return session


def _public(session: Session) -> dict:
    return {
        "id": session.id,
        "report": session.report,
        "summary": session.summary,
        "trace": session.analysis.trace,
        "rag": {"chunks": session.index.count, "embedding": session.index.embedder.name},
        "chat": session.chat,
        "tokens_used": session.tokens_used,
    }


@app.get("/api/status")
async def status() -> dict:
    return llm_status()


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest) -> dict:
    _expire()
    started = time.perf_counter()
    url = normalize_url(body.url)
    analysis = await analyze_site(url)

    t = time.perf_counter()
    index = SiteIndex()
    for page in analysis.pages:
        await index.ingest_page(page.url, page.path, page.soup)
    await index.ingest_report(analysis.report)
    analysis.trace.append({
        "agent": "RAG/QA Agent",
        "text": f"Chunked and embedded {len(analysis.pages)} page(s) into {index.count} vectors "
                f"({index.embedder.name}) in ChromaDB",
        "ms": round((time.perf_counter() - t) * 1000),
    })

    session = Session(id=secrets.token_hex(3), analysis=analysis, index=index)
    t = time.perf_counter()
    session.summary, composer = await compose_summary(session)
    analysis.trace.append({"agent": "Report Composer Agent", "text": f"Wrote the executive summary ({composer})",
                           "ms": round((time.perf_counter() - t) * 1000)})
    analysis.trace.insert(0, {"agent": "Supervisor", "text": "Planned: crawl → link check → SEO/content → "
                              "performance/accessibility → RAG indexing → report", "ms": 0})
    SESSIONS[session.id] = session
    log.info("Analyzed %s in %.1fs (session %s)", url, time.perf_counter() - started, session.id)
    return _public(session)


@app.get("/api/report/{session_id}")
async def get_report(session_id: str) -> dict:
    return _public(_get(session_id))


@app.post("/api/chat/{session_id}")
async def chat(session_id: str, body: ChatRequest) -> dict:
    session = _get(session_id)
    started = time.perf_counter()
    result = await answer(session, body.message.strip())
    reply = {
        "answer": result["answer"],
        "steps": result["steps"],
        "model": result["model"],
        "cached": result.get("cached", False),
        "ms": round((time.perf_counter() - started) * 1000),
    }
    session.chat += [{"role": "user", "text": body.message.strip()}, {"role": "assistant", **reply}]
    log.info("Chat %s: %d ms, %d tokens total", session_id, reply["ms"], session.tokens_used)
    return {**reply, "tokens_used": session.tokens_used}


# --- Static frontend (production build) ---------------------------------------------------------

DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parent.parent / "frontend" / "dist"))
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        file = (DIST / path).resolve()
        if path and file.is_file() and DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
