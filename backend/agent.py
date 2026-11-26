"""LLM integration, session memory, agent tools and the LangGraph ReAct agent."""
import asyncio
import json
import logging
import os
import re
import time
import warnings
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from analyzer import SiteAnalysis, analyze_single
from checks import page_path
from fetch import AnalyzeError, normalize_url
from prompts import COMPOSER_PROMPT, SYSTEM_PROMPT
from rag import SiteIndex, clean_html, google_key, page_facts

warnings.filterwarnings("ignore", category=DeprecationWarning)
log = logging.getLogger("websage.agent")

# Tried in order: free-tier Gemini models are often "high demand" (503), so several fallbacks help.
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-3.5-flash,gemini-3.6-flash,gemini-3.1-flash-lite,gemini-3.5-flash-lite").split(",") if m.strip()]
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
SESSION_TOKEN_BUDGET = int(os.getenv("SESSION_TOKEN_BUDGET", "150000"))
MAX_AGENT_STEPS = 12  # LangGraph recursion limit (each tool call is two steps)
CATEGORY_WORDS = {
    "seo": ("seo", "search", "meta", "title", "description", "heading", "h1", "sitemap", "canonical", "google", "rank"),
    "accessibility": ("accessib", "alt", "screen reader", "label", "a11y", "lang", "blind"),
    "performance": ("perform", "slow", "speed", "fast", "size", "load", "heavy", "image size", "request"),
    "security": ("secur", "https", "hsts", "header", "csp", "clickjack", "frame"),
    "links": ("link", "broken", "404", "dead"),
}


# --- LLM integration ----------------------------------------------------------------------------


def make_llms() -> list[tuple[str, object]]:
    """Gemini first, Groq (Llama 3) as fallback, depending on which keys are configured."""
    llms = []
    if google_key():
        from langchain_google_genai import ChatGoogleGenerativeAI

        for model in GEMINI_MODELS:
            llms.append((f"Gemini ({model})", ChatGoogleGenerativeAI(
                model=model, google_api_key=google_key(), temperature=0.2, max_retries=0, timeout=25)))
    if os.getenv("GROQ_API_KEY"):
        from langchain_groq import ChatGroq

        llms.append((f"Groq ({GROQ_MODEL})", ChatGroq(model=GROQ_MODEL, temperature=0.2, max_retries=1, timeout=45)))
    return llms


LLMS = make_llms()


def llm_status() -> dict:
    return {"llm": [name for name, _ in LLMS], "agentic": bool(LLMS)}


def _text(msg) -> str:
    content = msg.content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return content or ""


def _tokens(msg) -> int:
    usage = getattr(msg, "usage_metadata", None) or {}
    return int(usage.get("total_tokens", 0))


# --- Session & memory ---------------------------------------------------------------------------


@dataclass
class SessionMemory:
    turns: list[tuple[str, str]] = field(default_factory=list)

    def add(self, role: str, text: str):
        self.turns.append((role, text))

    def as_messages(self, last_n: int = 6) -> list:
        return [HumanMessage(t) if r == "user" else AIMessage(t) for r, t in self.turns[-last_n:]]


@dataclass
class Session:
    id: str
    analysis: SiteAnalysis
    index: SiteIndex
    memory: SessionMemory = field(default_factory=SessionMemory)
    tool_cache: dict = field(default_factory=dict)
    answer_cache: dict = field(default_factory=dict)
    tokens_used: int = 0
    created: float = field(default_factory=time.time)
    summary: str = ""
    chat: list = field(default_factory=list)  # what the UI shows

    @property
    def report(self) -> dict:
        return self.analysis.report

    @property
    def site(self) -> str:
        return self.report["url"]


# --- Tools --------------------------------------------------------------------------------------


def _category_view(report: dict, cid: str) -> dict:
    c = next(c for c in report["categories"] if c["id"] == cid)
    return {
        "category": c["name"],
        "score": c["score"],
        "pages_analysed": report["pages"],
        "issues": [
            {k: i[k] for k in ("severity", "title", "detail", "fix", "pages", "examples") if i.get(k)}
            for i in c["issues"]
        ],
        "passed_checks": c["passed"],
        **({"metrics": c["metrics"]["stats"]} if c.get("metrics") else {}),
    }


def build_tools(session: Session) -> list:
    def crawled(target: str):
        return next((p for p in session.analysis.pages if p.url.rstrip("/") == target.rstrip("/")), None)

    async def report_for(url: str) -> dict:
        """Stored report for the analysed site, or a fresh single-page analysis for another URL (cached)."""
        try:
            target = normalize_url(url.strip().strip("\"'")) if url and url.strip() else ""
        except AnalyzeError:
            target = ""
        if not target or crawled(target):
            return session.report  # already covers every crawled page
        key = ("analysis", target)
        if key not in session.tool_cache:
            result = await analyze_single(target)
            session.tool_cache[key] = result
            for page in result.pages:
                await session.index.ingest_page(page.url, page.path, page.soup)
        return session.tool_cache[key].report

    async def run(name: str, cid: str, url: str) -> str:
        try:
            report = await report_for(url)
        except AnalyzeError as exc:
            return json.dumps({"error": exc.message})
        return json.dumps(_category_view(report, cid))

    @tool
    async def seo_check(url: str = "") -> str:
        """Run on-page SEO checks (title, meta description, headings, canonical, Open Graph, sitemap, robots.txt).
        Leave url empty for the analysed site; pass a full URL for another page."""
        return await run("seo_check", "seo", url)

    @tool
    async def accessibility_check(url: str = "") -> str:
        """Run accessibility checks (image alt text, form labels, page language). Leave url empty for the analysed site."""
        return await run("accessibility_check", "accessibility", url)

    @tool
    async def performance_check(url: str = "") -> str:
        """Measure page weight, number of requests and large images, plus security headers. Leave url empty for the analysed site."""
        perf = json.loads(await run("performance_check", "performance", url))
        sec = json.loads(await run("performance_check", "security", url))
        return json.dumps({"performance": perf, "security_headers": sec})

    @tool
    async def check_links(url: str = "") -> str:
        """Check links on the analysed pages for broken ones (4xx/5xx/unreachable). Leave url empty for the analysed site."""
        return await run("check_links", "links", url)

    @tool
    async def scrape_page(url: str) -> str:
        """Fetch a page and return its title, headings and visible text. Use for pages that weren't crawled yet;
        the page is also added to the RAG index."""
        try:
            target = normalize_url(url)
        except AnalyzeError as exc:
            return json.dumps({"error": exc.message})
        key = ("scrape", target)
        if key not in session.tool_cache:
            page = crawled(target)
            if page is None:
                try:
                    await report_for(target)
                except AnalyzeError as exc:
                    return json.dumps({"error": exc.message})
                page = session.tool_cache[("analysis", target)].pages[0]
            session.tool_cache[key] = (
                f"<page_content>{page_facts(page.path, page.soup)}\n\n{clean_html(page.soup)[:3000]}</page_content>"
            )
        return session.tool_cache[key]

    @tool
    async def rag_search(query: str) -> str:
        """Semantic search over the analysed site's pages and the analysis findings (RAG). Returns the most relevant
        chunks with the page they came from. Use for questions about the site's content or about specific findings."""
        hits = await session.index.search(query, k=5)
        return "<page_content>" + json.dumps(hits, ensure_ascii=False) + "</page_content>"

    return [scrape_page, seo_check, accessibility_check, performance_check, check_links, rag_search]


# --- Agent --------------------------------------------------------------------------------------


def _steps_from(messages: list) -> tuple[list[dict], int]:
    steps, tokens = [], 0
    for msg in messages:
        if isinstance(msg, AIMessage):
            tokens += _tokens(msg)
            thought = _text(msg).strip()
            if msg.tool_calls:
                if thought:
                    steps.append({"type": "thought", "text": thought[:300]})
                for call in msg.tool_calls:
                    arg = next(iter(call["args"].values()), "") if call["args"] else ""
                    steps.append({"type": "action", "tool": call["name"], "input": str(arg)[:120]})
        elif isinstance(msg, ToolMessage):
            text = _text(msg)
            text = re.sub(r"</?page_content>", "", text)
            steps.append({"type": "observation", "tool": msg.name, "text": text[:240] + ("…" if len(text) > 240 else "")})
    return steps, tokens


async def run_agent(session: Session, question: str) -> dict:
    from langgraph.prebuilt import create_react_agent

    tools = build_tools(session)
    history = session.memory.as_messages()
    prompt = SYSTEM_PROMPT.format(site=session.site)
    last_error = None
    for name, llm in LLMS:
        agent = create_react_agent(llm, tools, prompt=prompt)
        try:
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [*history, HumanMessage(question)]},
                              config={"recursion_limit": MAX_AGENT_STEPS}),
                timeout=60,
            )
        except Exception as exc:  # provider down, quota, timeout: try the next provider
            log.warning("Agent with %s failed: %s", name, exc)
            last_error = exc
            continue
        new = result["messages"][len(history) + 1:]
        steps, tokens = _steps_from(new)
        answer = _text(new[-1]).strip() if new else ""
        return {"answer": answer or "I couldn't produce an answer.", "steps": steps, "tokens": tokens, "model": name}
    raise RuntimeError(f"All LLM providers failed: {last_error}")


# --- Offline fallback (no API key) -------------------------------------------------------------


def _guess_category(question: str) -> str | None:
    q = question.lower()
    scores = {cid: sum(w in q for w in words) for cid, words in CATEGORY_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


async def fallback_answer(session: Session, question: str) -> dict:
    """Rule-based planner used when no LLM key is configured: pick a tool by keywords, then answer from
    tool output + retrieved chunks. Keeps the app useful offline, but the LLM agent is much better."""
    steps = []
    cid = _guess_category(question)
    hits = await session.index.search(question, k=4)
    steps.append({"type": "action", "tool": "rag_search", "input": question[:120]})
    steps.append({"type": "observation", "tool": "rag_search",
                  "text": f"{len(hits)} chunks, top from {', '.join(str(h['source']) for h in hits[:3])}"})
    lines = []
    if cid:
        view = _category_view(session.report, cid)
        tool_name = {"seo": "seo_check", "accessibility": "accessibility_check", "performance": "performance_check",
                     "security": "performance_check", "links": "check_links"}[cid]
        steps.insert(0, {"type": "action", "tool": tool_name, "input": session.site})
        steps.insert(1, {"type": "observation", "tool": tool_name,
                         "text": f"score {view['score']}, {len(view['issues'])} issue(s)"})
        if view["issues"]:
            lines.append(f"Your {view['category'].lower()} score is {view['score']}/100 mainly because:")
            for i in view["issues"][:4]:
                pages = f" ({', '.join(i['pages'][:4])})" if i.get("pages") else ""
                lines.append(f"- {i['title']}{pages}. Fix: {i['fix']}")
        else:
            lines.append(f"Your {view['category'].lower()} score is {view['score']}/100: no issues were found.")
    relevant = [h for h in hits if h["kind"] in ("content", "facts") and h["score"] > 0.2]
    if relevant:
        lines.append("")
        lines.append("Most relevant content found on the site:")
        for h in relevant[:2]:
            lines.append(f"- {h['source']}: “{h['text'][:220]}…”")
    if not lines:
        lines.append(
            f"I couldn't find anything about that in {session.site}. I can only answer questions about this website's "
            "content and its SEO, accessibility, performance, security and links."
        )
    lines.append("")
    if not LLMS:
        lines.append("_(Offline mode: answers are rule-based. Add a GOOGLE_API_KEY for full AI answers.)_")
    return {"answer": "\n".join(lines), "steps": steps, "tokens": 0, "model": "offline"}


async def answer(session: Session, question: str) -> dict:
    key = question.strip().lower()
    if key in session.answer_cache:  # TC-08: same question twice -> no redundant tool calls
        cached = dict(session.answer_cache[key])
        cached["steps"] = [{"type": "thought", "text": "Answered from this session's cache (same question asked before)."}]
        cached["cached"] = True
        return cached
    if LLMS and session.tokens_used < SESSION_TOKEN_BUDGET:
        try:
            result = await run_agent(session, question)
        except RuntimeError as exc:
            log.error("%s", exc)
            result = await fallback_answer(session, question)
            result["answer"] = "_The AI models are busy right now, so this is a rule-based answer. Try again in a minute for a full AI answer._\n\n" + result["answer"]
    else:
        result = await fallback_answer(session, question)
        if LLMS:
            result["answer"] = "This session has used its AI token budget.\n\n" + result["answer"]
    session.tokens_used += result["tokens"]
    session.memory.add("user", question)
    session.memory.add("assistant", result["answer"])
    if result["model"] != "offline" or not LLMS:  # don't pin a temporarily degraded answer
        session.answer_cache[key] = result
    return result


# --- Report composer ----------------------------------------------------------------------------


def _template_summary(report: dict) -> str:
    cats = sorted(report["categories"], key=lambda c: c["score"])
    weakest = cats[0]
    issues = sorted(
        (i for c in report["categories"] for i in c["issues"]),
        key=lambda i: {"high": 0, "medium": 1, "low": 2}[i["severity"]],
    )
    text = (
        f"The site scores {report['score']}/100 overall across {len(report['pages'])} analysed page(s). "
        f"The weakest area is {weakest['name']} at {weakest['score']}/100."
    )
    if issues:
        text += "\n" + "\n".join(f"- {i['title']}: {i['fix'].split('. ')[0].rstrip('.')}." for i in issues[:3])
    return text


async def compose_summary(session: Session) -> tuple[str, str]:
    report = session.report
    compact = {
        "site": report["url"], "overall": report["score"], "pages": report["pages"],
        "categories": [{"name": c["name"], "score": c["score"],
                        "issues": [f"[{i['severity']}] {i['title']}" for i in c["issues"]]} for c in report["categories"]],
    }
    prompt = COMPOSER_PROMPT.format(site=report["url"], findings=json.dumps(compact))
    for name, llm in LLMS:
        try:
            msg = await asyncio.wait_for(llm.ainvoke(prompt), timeout=30)
            session.tokens_used += _tokens(msg)
            text = _text(msg).strip()
            if text:
                return text, name
        except Exception as exc:
            log.warning("Composer with %s failed: %s", name, exc)
    return _template_summary(report), "template"
