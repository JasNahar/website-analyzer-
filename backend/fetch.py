"""Network side: URL validation, fetching the page, and measuring its resources."""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

PAGE_TIMEOUT = 10.0  # seconds for the whole HTML download
MAX_HTML_BYTES = 5 * 1024 * 1024
RESOURCE_TIMEOUT = 5.0  # per resource
RESOURCE_BUDGET = 15.0  # for all resources together
MAX_RESOURCES = 100
RESOURCE_CONCURRENCY = 10
HTML_TYPES = ("text/html", "application/xhtml+xml")
USER_AGENT = "Mozilla/5.0 (compatible; WebsiteAnalyzer/1.0)"


class AnalyzeError(Exception):
    """A failure to show the user as-is: HTTP status, machine-readable code, readable message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Page:
    url: str  # final URL after redirects
    headers: httpx.Headers
    html: bytes
    encoding: str | None
    transfer_size: int  # bytes on the wire (compressed)


def normalize_url(raw: str) -> str:
    """Add https:// when no scheme is given; reject anything that isn't a plausible http(s) URL."""
    url = raw.strip()
    if not url:
        raise AnalyzeError(400, "invalid_url", "Enter a URL to analyze.")
    if "://" not in url:
        url = "https://" + url
    invalid = AnalyzeError(400, "invalid_url", f"“{raw.strip()}” isn't a valid web address.")
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError for a malformed port
    except ValueError:
        raise invalid from None
    if parts.scheme.lower() not in ("http", "https"):
        raise AnalyzeError(400, "invalid_url", "Only http:// and https:// addresses can be analyzed.")
    if not parts.hostname or not re.fullmatch(r"[\w.-]+|[0-9a-f:.]+", parts.hostname):
        raise invalid
    return url


async def _guard(request: httpx.Request) -> None:
    """Runs before every request, including redirects and resource probes.

    Refuses private and local addresses so the analyzer can't be used to reach internal services.
    """
    host = request.url.host
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        raise AnalyzeError(502, "unreachable", f"The domain {host} couldn't be found. Check the spelling.") from None
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise AnalyzeError(
                400, "invalid_url", f"{host} points to a private or local network address, which can't be analyzed."
            )


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=10,
        timeout=PAGE_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_guard]},
    )


async def fetch_page(client: httpx.AsyncClient, url: str) -> Page:
    try:
        async with asyncio.timeout(PAGE_TIMEOUT):
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    hint = " The site may be blocking automated requests." if resp.status_code in (401, 403, 429) else ""
                    raise AnalyzeError(
                        502, "http_error", f"The site responded with HTTP {resp.status_code} {resp.reason_phrase}.{hint}"
                    )
                content_type = resp.headers.get("content-type", "")
                if content_type.split(";")[0].strip().lower() not in HTML_TYPES:
                    raise AnalyzeError(
                        422, "not_html",
                        f"Expected an HTML page, but the server sent “{content_type or 'no content type'}”.",
                    )
                html = bytearray()
                async for chunk in resp.aiter_bytes():
                    html += chunk
                    if len(html) > MAX_HTML_BYTES:
                        raise AnalyzeError(422, "too_large", "The page's HTML is larger than 5 MB, which is too big to analyze.")
                return Page(str(resp.url), resp.headers, bytes(html), resp.charset_encoding, resp.num_bytes_downloaded)
    except AnalyzeError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise AnalyzeError(504, "timeout", f"The site didn't finish responding within {PAGE_TIMEOUT:g} seconds.") from None
    except httpx.TooManyRedirects:
        raise AnalyzeError(502, "unreachable", "The site redirected too many times (probably a redirect loop).") from None
    except httpx.InvalidURL:
        raise AnalyzeError(400, "invalid_url", f"“{url}” isn't a valid web address.") from None
    except httpx.HTTPError as exc:
        reason = (
            "its SSL certificate couldn't be verified"
            if "CERTIFICATE_VERIFY_FAILED" in str(exc)
            else "the server refused or dropped the connection"
        )
        raise AnalyzeError(502, "unreachable", f"Couldn't connect to the site: {reason}.") from None


async def resource_sizes(client: httpx.AsyncClient, urls: list[str]) -> dict[str, int | None]:
    """Transfer size in bytes for each URL, or None when it couldn't be measured in time."""
    semaphore = asyncio.Semaphore(RESOURCE_CONCURRENCY)

    async def probe(url: str) -> int | None:
        async with semaphore:
            try:
                head = await client.head(url, timeout=RESOURCE_TIMEOUT)
                length = head.headers.get("content-length", "")
                if head.is_success and length.isdigit():
                    return int(length)
                async with client.stream("GET", url, timeout=RESOURCE_TIMEOUT) as resp:
                    if not resp.is_success:
                        return None
                    async for _ in resp.aiter_raw():
                        pass
                    return resp.num_bytes_downloaded
            except Exception:  # any failure just means "unmeasured"
                return None

    tasks = {url: asyncio.create_task(probe(url)) for url in urls[:MAX_RESOURCES]}
    if not tasks:
        return {}
    done, pending = await asyncio.wait(tasks.values(), timeout=RESOURCE_BUDGET)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return {url: tasks[url].result() if tasks.get(url) in done else None for url in urls}
