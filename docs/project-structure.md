# Project structure

```
backend/
  main.py       FastAPI app: endpoints, sessions, supervisor pipeline, static UI
  analyzer.py   crawler, robots.txt, sitemap and broken-link checks
  checks.py     page checks, multi-page merging, scoring
  fetch.py      URL validation, SSRF guard, page + resource fetching
  rag.py        cleaning, chunking, embeddings, ChromaDB index
  agent.py      LLM clients, memory, agent tools, LangGraph ReAct agent, offline fallback
  prompts.py    system / composer prompts
frontend/src/
  App.jsx       routing (/ , /report/:id, /chat/:id)
  Home.jsx      URL entry + progress
  ReportPage.jsx  report screen (wireframe Fig 6.2)
  ChatPage.jsx    RAG Q&A screen (wireframe Fig 6.3)
tests/          offline pytest suite
Dockerfile      one container for UI + API (Hugging Face Spaces, port 7860)
```
