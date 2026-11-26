"""RAG pipeline: clean -> chunk -> embed -> store in ChromaDB -> retrieve top-k.

Each analyzed site gets its own Chroma collection (in-memory, dropped when the session expires,
so scraped content isn't persisted). Three kinds of documents are indexed:
  * page content chunks (500 chars, 50 overlap),
  * one "page facts" document per page (title, description, headings, images without alt, ...),
  * one document per analysis finding, so questions like "why is my SEO score low?" retrieve the
    exact issues the tools found.

Embeddings come from Gemini (gemini-embedding-001) when GOOGLE_API_KEY is set. Without a key, or
if the API fails, a local hashed bag-of-words embedding is used so the app still works offline.
"""
import hashlib
import logging
import math
import os
import re
import uuid

import chromadb
from bs4 import BeautifulSoup

log = logging.getLogger("websage.rag")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MAX_CHUNKS_PER_PAGE = 60
LOCAL_DIM = 768
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")

_client = chromadb.EphemeralClient()


def google_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


# --- Cleaning & chunking ------------------------------------------------------------------------


def clean_html(soup: BeautifulSoup) -> str:
    soup = BeautifulSoup(str(soup), "html.parser")  # copy: don't mutate the analysed tree
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on whitespace into ~size-char windows with overlap, never cutting a word."""
    text = re.sub(r"\s+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            end = space if space > start else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        while start < len(text) and text[start - 1] != " ":
            start += 1  # begin the next chunk on a word boundary
    return [c for c in chunks if c]


def page_facts(path: str, soup: BeautifulSoup) -> str:
    title = soup.title.get_text(" ", strip=True) if soup.title else "(none)"
    desc = soup.find("meta", attrs={"name": "description"})
    desc = (desc.get("content") or "").strip() if desc else ""
    headings = [f"{h.name}: {h.get_text(' ', strip=True)[:80]}" for h in soup.find_all(re.compile(r"^h[1-3]$"))][:15]
    imgs = soup.find_all("img")
    no_alt = [img.get("src", "?")[:100] for img in imgs if not img.has_attr("alt")]
    links = len(soup.find_all("a", href=True))
    words = len(clean_html(soup).split())
    return (
        f"Page facts for {path}. Title: {title}. Meta description: {desc or 'missing'}. "
        f"Headings: {'; '.join(headings) or 'none'}. Images: {len(imgs)} total, {len(no_alt)} without alt text"
        f"{' (' + ', '.join(no_alt[:8]) + ')' if no_alt else ''}. Links: {links}. Word count: {words}."
    )


def finding_docs(report: dict) -> list[tuple[str, dict]]:
    docs = [(
        f"Overall analysis of {report['url']}: overall score {report['score']}/100. Pages analysed: "
        f"{', '.join(report['pages'])}. Category scores: "
        + ", ".join(f"{c['name']} {c['score']}/100 ({len(c['issues'])} issues)" for c in report["categories"]),
        {"kind": "summary", "url": report["url"]},
    )]
    for c in report["categories"]:
        if not c["issues"]:
            docs.append((f"{c['name']} ({c['score']}/100): no issues found. Passed checks: {', '.join(c['passed'])}.",
                         {"kind": "finding", "category": c["id"], "url": report["url"]}))
        for i in c["issues"]:
            text = (
                f"Finding in {c['name']} (category score {c['score']}/100), severity {i['severity']}: {i['title']}. "
                f"{i['detail']} How to fix: {i['fix']}"
            )
            if i.get("pages"):
                text += f" Affected pages: {', '.join(i['pages'])}."
            if i.get("examples"):
                text += f" Examples: {' | '.join(i['examples'])}"
            docs.append((text, {"kind": "finding", "category": c["id"], "url": report["url"]}))
    return docs


# --- Embeddings ---------------------------------------------------------------------------------


STOPWORDS = set(
    "a an the and or but if of to in on at by for with from as is are was were be been it its this that these those "
    "i you he she we they my your our their me us what which who whom where when why how do does did can could "
    "should would will there here about into than then so not no yes all any some".split()
)


def _local_embed(texts: list[str]) -> list[list[float]]:
    """Hashed unigram+bigram TF vectors, L2-normalised. Crude but deterministic and dependency-free."""
    out = []
    for text in texts:
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]
        vec = [0.0] * LOCAL_DIM
        for token in words + [f"{a}_{b}" for a, b in zip(words, words[1:])]:
            h = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "little")
            vec[h % LOCAL_DIM] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


class Embedder:
    def __init__(self):
        self.name = "local-hash"
        self._gemini = None
        if google_key():
            try:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings

                self._gemini = GoogleGenerativeAIEmbeddings(model=GEMINI_EMBED_MODEL, google_api_key=google_key())
                self.name = GEMINI_EMBED_MODEL.removeprefix("models/")
            except Exception as exc:  # pragma: no cover
                log.warning("Gemini embeddings unavailable: %s", exc)

    async def documents(self, texts: list[str]) -> list[list[float]]:
        if self._gemini:
            try:
                return await self._gemini.aembed_documents(texts)
            except Exception as exc:
                log.warning("Gemini embedding failed, falling back to local: %s", exc)
                self._gemini, self.name = None, "local-hash"
        return _local_embed(texts)

    async def query(self, text: str) -> list[float]:
        if self._gemini:
            try:
                return await self._gemini.aembed_query(text)
            except Exception as exc:
                log.warning("Gemini query embedding failed: %s", exc)
        return _local_embed([text])[0]


# --- Store --------------------------------------------------------------------------------------


class SiteIndex:
    """A per-site vector index backed by an in-memory ChromaDB collection."""

    def __init__(self):
        self.embedder = Embedder()
        self.collection = _client.create_collection(
            f"site-{uuid.uuid4().hex}", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        self.indexed_urls: set[str] = set()

    @property
    def count(self) -> int:
        return self.collection.count()

    async def _add(self, docs: list[tuple[str, dict]]):
        if not docs:
            return
        texts = [d for d, _ in docs]
        before = self.embedder.name
        vectors = []
        for start in range(0, len(texts), 90):  # API batch limit is 100
            vectors += await self.embedder.documents(texts[start:start + 90])
        if self.embedder.name != before or len({len(v) for v in vectors}) > 1:
            # Gemini failed part-way: switch the whole index to local embeddings so dimensions match.
            vectors = _local_embed(texts)
            if self.count:
                old = self.collection.get(include=["documents", "metadatas"])
                self.drop()
                self.collection = _client.create_collection(
                    f"site-{uuid.uuid4().hex}", embedding_function=None, metadata={"hnsw:space": "cosine"}
                )
                self.collection.add(ids=old["ids"], documents=old["documents"],
                                    embeddings=_local_embed(old["documents"]), metadatas=old["metadatas"])
        self.collection.add(
            ids=[uuid.uuid4().hex for _ in docs], documents=texts, embeddings=vectors, metadatas=[m for _, m in docs]
        )

    async def ingest_page(self, url: str, path: str, soup: BeautifulSoup):
        if url in self.indexed_urls:
            return 0
        self.indexed_urls.add(url)
        chunks = chunk_text(clean_html(soup))[:MAX_CHUNKS_PER_PAGE]
        docs = [(page_facts(path, soup), {"kind": "facts", "url": url, "path": path})]
        docs += [(c, {"kind": "content", "url": url, "path": path}) for c in chunks]
        await self._add(docs)
        return len(docs)

    async def ingest_report(self, report: dict):
        await self._add(finding_docs(report))

    async def search(self, query: str, k: int = 4) -> list[dict]:
        if not self.count:
            return []
        qvec = await self.embedder.query(query)
        res = self.collection.query(query_embeddings=[qvec], n_results=min(k, self.count))
        return [
            {"text": doc, "source": meta.get("path") or meta.get("kind"), "kind": meta.get("kind"),
             "score": round(1 - dist, 3)}
            for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def drop(self):
        try:
            _client.delete_collection(self.collection.name)
        except Exception:
            pass
