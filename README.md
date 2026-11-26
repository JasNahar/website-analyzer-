---
title: WebSage
emoji: 🔎
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Agentic AI website analyzer with RAG follow-up Q&A
---

# WebSage – Agentic Website Analyzer

Enter a URL and WebSage crawls the site, runs SEO, accessibility, performance, security-header and
link-integrity checks, and writes an AI summary. Then ask follow-up questions in a chat: a ReAct agent
decides which tools to call and answers from the site's own content through RAG.

## Architecture

| Layer | Implementation |
|---|---|
| UI | React + Vite: home, `/report/:id` (issue list tagged SEO / Accessibility / Performance / Security / Links), `/chat/:id` (RAG Q&A) |
| API | FastAPI (`backend/main.py`), also serves the built UI |
| Supervisor pipeline | Crawler Agent → Link Checker Agent → SEO & Content Agent → Performance/Accessibility Agent → RAG/QA Agent → Report Composer Agent, each step logged in "Agent activity" |
| Crawler | Homepage + up to 4 internal pages, respects robots.txt, blocks private/internal addresses (SSRF guard) |
| Checks | 20 deterministic checks in `backend/checks.py`; findings from several pages are merged ("Missing meta description on 3 pages") |
| Agent | LangGraph `create_react_agent` with tools `scrape_page`, `seo_check`, `accessibility_check`, `performance_check`, `check_links`, `rag_search` (`backend/agent.py`) |
| LLM | Google Gemini (`gemini-3.5-flash`, then other Gemini models as fallbacks), optional Groq Llama 3.3 fallback |
| RAG | Clean HTML → 500-char chunks with 50 overlap → `gemini-embedding-001` → ChromaDB (in-memory, per site) → top-k retrieval. Page facts and every finding are indexed too |
| Memory | Last 6 chat turns per session, per-session tool and answer caches, per-session token budget |
| Prompts | `backend/prompts.py`: system prompt (grounding + prompt-injection rules) with a few-shot ReAct example, report-composer prompt |

Without an API key the app still works: the report uses a template summary, retrieval uses a local
hashed embedding, and chat uses a rule-based planner.

## Run locally

Requirements: Python 3.11+ and Node.js 20.19+.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
export GOOGLE_API_KEY=your-gemini-key                # optional; Windows: $env:GOOGLE_API_KEY="..."
uvicorn main:app --reload --port 8000

# second terminal
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

Or build everything into one container: `docker build -t websage . && docker run -p 7860:7860 -e GOOGLE_API_KEY=... websage`.

Tests (offline, no key needed): `pytest tests`.

## Configuration

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) | Enables the Gemini agent, summary and embeddings |
| `GROQ_API_KEY` | Optional Groq fallback |
| `GEMINI_MODELS` | Comma-separated model order, default `gemini-3.5-flash,gemini-3.6-flash,gemini-3.1-flash-lite,gemini-3.5-flash-lite` |
| `SESSION_TOKEN_BUDGET` | Max LLM tokens per analysed site (default 150000) |

## API

- `POST /api/analyze` `{"url": "example.com"}` → `{id, report, summary, trace, rag}`
- `GET /api/report/{id}` → same, plus chat history
- `POST /api/chat/{id}` `{"message": "..."}` → `{answer, steps, model, ms}`
- `GET /api/status` → configured LLM providers

## Limitations

- Performance is static (no headless browser): files loaded by JavaScript/CSS aren't counted.
- Sessions live in memory for 2 hours; restarting the server clears them.
- Some sites block automated requests; links answering 401/403/429 are reported as unverified, not broken.
- Free-tier Gemini models are sometimes overloaded; the agent then tries the next model, and as a last resort gives a rule-based answer.
