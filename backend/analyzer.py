"""Crawler + tool layer: fetches a short crawl of a site and runs every check on it.

The functions here are plain async Python so they can be used both by the analysis pipeline
(the supervisor in pipeline.py) and as tools the ReAct agent calls (agent.py).
"""
import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from checks import Context, SiteInfo, build_report, find_resources, page_path
from fetch import USER_AGENT, AnalyzeError, Page, fetch_page, make_client, resource_sizes

MAX_PAGES = 5  # homepage + up to 4 internal pages
MAX_LINKS = 60  # links probed for the broken-link check
LINK_TIMEOUT = 6.0
LINK_BUDGET = 15.0
LINK_CONCURRENCY = 12
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".mp4", ".mp3", ".xml", ".css", ".js")


@dataclass
class CrawledPage:
    url: str
    soup: BeautifulSoup
    ctx: Context

    @property
    def path(self) -> str:
        return page_path(self.url)


@dataclass
class SiteAnalysis:
    report: dict
    pages: list[CrawledPage]
    site: SiteInfo
    trace: list[dict] = field(default_factory=list)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def _clean(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def page_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _clean(urljoin(page_url, href))
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


async def _get_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=5.0)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None


async def load_robots(client: httpx.AsyncClient, origin: str) -> tuple[RobotFileParser, bool, list[str]]:
    parser = RobotFileParser()
    text = await _get_text(client, origin + "/robots.txt")
    parser.parse((text or "").splitlines())
    sitemaps = parser.site_maps() or []
    return parser, text is not None, sitemaps


async def find_sitemap(client: httpx.AsyncClient, origin: str, declared: list[str]) -> str | None:
    for url in [*declared, origin + "/sitemap.xml", origin + "/sitemap_index.xml"]:
        text = await _get_text(client, url)
        if text and ("<urlset" in text or "<sitemapindex" in text):
            return url
    return None


async def check_links(client: httpx.AsyncClient, links: list[tuple[str, str]]) -> tuple[int, list, int]:
    """Probe links; returns (checked, broken [(status, url, found_on)], unverified)."""
    semaphore = asyncio.Semaphore(LINK_CONCURRENCY)
    results: dict[str, str | int | None] = {}

    async def probe(url: str):
        async with semaphore:
            try:
                resp = await client.head(url, timeout=LINK_TIMEOUT)
                if resp.status_code in (405, 501) or resp.status_code >= 400:
                    resp = await client.get(url, timeout=LINK_TIMEOUT)
                results[url] = resp.status_code
            except AnalyzeError:
                results[url] = None  # private address etc.: not our business
            except httpx.TimeoutException:
                results[url] = "Timeout"
            except Exception:
                results[url] = "Unreachable"

    todo = links[:MAX_LINKS]
    tasks = [asyncio.create_task(probe(url)) for url, _ in todo]
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=LINK_BUDGET)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    broken, unverified = [], 0
    for url, found_on in todo:
        status = results.get(url)
        if status in (401, 403, 429, "Timeout"):
            unverified += 1
        elif isinstance(status, int) and status >= 400:
            broken.append((status, url, found_on))
        elif isinstance(status, str):
            broken.append((status, url, found_on))
    return len(results), broken, unverified


def _ctx(page: Page, soup: BeautifulSoup, resources=None, sizes=None) -> Context:
    return Context(page.url, page.headers, soup, page.transfer_size, resources or {}, sizes or {})


async def analyze_site(url: str, max_pages: int = MAX_PAGES) -> SiteAnalysis:
    """Crawl up to max_pages same-site pages (respecting robots.txt) and run every check."""
    trace = []

    def log(agent: str, text: str, started: float):
        trace.append({"agent": agent, "text": text, "ms": round((time.perf_counter() - started) * 1000)})

    async with make_client() as client:
        t = time.perf_counter()
        home_page = await fetch_page(client, url)
        home_soup = BeautifulSoup(home_page.html, "html.parser", from_encoding=home_page.encoding)
        origin = _origin(home_page.url)
        robots, robots_found, declared_sitemaps = await load_robots(client, origin)

        internal = [
            link for link in page_links(home_soup, home_page.url)
            if _origin(link) == origin
            and _clean(link) != _clean(home_page.url)
            and not urlsplit(link).path.lower().endswith(SKIP_EXTENSIONS)
            and robots.can_fetch(USER_AGENT, link)
        ]

        async def fetch_other(link: str):
            try:
                page = await fetch_page(client, link)
                return page, BeautifulSoup(page.html, "html.parser", from_encoding=page.encoding)
            except AnalyzeError:
                return None

        resources = find_resources(home_soup, home_page.url)
        sizes_task = asyncio.create_task(resource_sizes(client, list(resources)))
        others_raw = await asyncio.gather(*(fetch_other(link) for link in internal[: max_pages - 1]))
        others = [o for o in others_raw if o and _clean(o[0].url) != _clean(home_page.url)]
        log("Crawler Agent", f"Fetched {1 + len(others)} page(s) from {origin}"
            f"{' (robots.txt respected)' if robots_found else ''}", t)

        t = time.perf_counter()
        all_links: dict[str, str] = {}
        for page_url, soup in [(home_page.url, home_soup), *[(p.url, s) for p, s in others]]:
            for link in page_links(soup, page_url):
                all_links.setdefault(link, page_path(page_url))
        # internal links first: they're the ones the owner can fix
        ordered = sorted(all_links.items(), key=lambda kv: _origin(kv[0]) != origin)
        (checked, broken, unverified), sitemap_url, sizes = await asyncio.gather(
            check_links(client, ordered), find_sitemap(client, origin, declared_sitemaps), sizes_task
        )
        log("Link Checker Agent", f"Checked {checked} links, {len(broken)} broken; "
            f"sitemap {'found' if sitemap_url else 'not found'}", t)

    t = time.perf_counter()
    home = CrawledPage(home_page.url, home_soup, _ctx(home_page, home_soup, resources, sizes))
    other_pages = [CrawledPage(p.url, s, _ctx(p, s)) for p, s in others]
    site = SiteInfo(robots_found, sitemap_url, checked, broken, unverified)
    report = build_report(home.ctx, [p.ctx for p in other_pages], site)
    by_id = {c["id"]: c for c in report["categories"]}
    log("SEO & Content Agent", f"SEO score {by_id['seo']['score']}, {len(by_id['seo']['issues'])} issue(s)", t)
    log("Performance/Accessibility Agent",
        f"Accessibility {by_id['accessibility']['score']}, performance {by_id['performance']['score']}, "
        f"security {by_id['security']['score']}", t)
    return SiteAnalysis(report, [home, *other_pages], site, trace)


async def analyze_single(url: str) -> SiteAnalysis:
    """One page only (used by agent tools for pages outside the original crawl)."""
    return await analyze_site(url, max_pages=1)
