"""Page checks grouped by category, plus scoring.

Every check takes a Context and returns one issue (covering all affected elements) or None.
"""
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup

KB, MB = 1024, 1024 * 1024

# Scoring: each category starts at 100 and loses this much per issue; overall = average of categories.
PENALTY = {"high": 30, "medium": 15, "low": 5}

# Thresholds. Pairs are (medium above, high above) unless noted.
TITLE_LENGTH = (30, 60)  # allowed range in characters
DESCRIPTION_LENGTH = (70, 160)  # allowed range in characters
PAGE_SIZE = (2 * MB, 5 * MB)
REQUESTS = (50, 100)
IMAGE_SIZE = (200 * KB, 1 * MB)
HSTS_MIN_AGE = 180 * 24 * 3600
OG_REQUIRED = ("og:title", "og:type", "og:image", "og:url")
UNLABELLED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
MAX_EXAMPLES = 5


@dataclass
class Context:
    url: str  # final URL after redirects
    headers: Mapping[str, str]  # case-insensitive (httpx.Headers)
    soup: BeautifulSoup
    html_size: int  # bytes transferred for the HTML
    resources: dict[str, str]  # absolute URL -> "script" | "stylesheet" | "image" | "other"
    sizes: dict[str, int | None]  # absolute URL -> bytes, None if unmeasured

    @cached_property
    def total_size(self) -> int:
        return self.html_size + sum(size for size in self.sizes.values() if size)

    @cached_property
    def images(self) -> list[tuple[str, int]]:
        """Measured images, largest first."""
        found = [(url, size) for url, size in self.sizes.items() if size and self.resources[url] == "image"]
        return sorted(found, key=lambda item: item[1], reverse=True)

    @property
    def request_count(self) -> int:
        return 1 + len(self.resources)


def find_resources(soup: BeautifulSoup, page_url: str) -> dict[str, str]:
    """Files referenced directly in the HTML, as absolute URL -> kind. JS- and CSS-loaded files aren't visible here."""
    base = soup.find("base", href=True)
    base_url = urljoin(page_url, base["href"]) if base else page_url
    found: dict[str, str] = {}

    def add(ref: str, kind: str) -> None:
        if ref.strip():
            url = urljoin(base_url, ref.strip()).split("#")[0]
            if url.startswith(("http://", "https://")):
                found.setdefault(url, kind)

    for script in soup.find_all("script", src=True):
        add(script["src"], "script")
    for link in soup.find_all("link", href=True):
        rel = {r.lower() for r in link.get("rel", [])}
        if "stylesheet" in rel:
            add(link["href"], "stylesheet")
        elif rel & {"icon", "apple-touch-icon"} or ("preload" in rel and link.get("as") == "image"):
            add(link["href"], "image")
        elif "preload" in rel:
            add(link["href"], "other")
    for img in soup.find_all("img"):
        first_srcset = (img.get("srcset") or "").strip().split(" ")[0].rstrip(",")
        add(img.get("src") or first_srcset, "image")
    return found


# --- Shared helpers ---------------------------------------------------------------------------


def issue(severity: str, title: str, detail: str, fix: str, examples=()) -> dict:
    return {"severity": severity, "title": title, "detail": detail, "fix": fix, "examples": list(examples)[:MAX_EXAMPLES]}


def _text(el) -> str:
    return " ".join(el.get_text().split()) if el else ""


def _clip(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tag(el) -> str:
    """The element's opening tag on one line, e.g. <img src="logo.png">."""
    attrs = "".join(f' {k}="{" ".join(v) if isinstance(v, list) else v}"' for k, v in el.attrs.items())
    return _clip(f"<{el.name}{attrs}>")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _bytes(n: int) -> str:
    if n < KB:
        return f"{n} B"
    return f"{n / KB:.0f} KB" if n < MB else f"{n / MB:.1f} MB"


def _tier(value: float, limits: tuple) -> str | None:
    medium, high = limits
    return "high" if value > high else "medium" if value > medium else None


def _meta(soup: BeautifulSoup, attr: str, key: str) -> str:
    """Content of the first <meta {attr}="{key}">, whitespace-normalized; "" if missing."""
    tag = soup.find("meta", attrs={attr: lambda v: v is not None and v.strip().lower() == key})
    return " ".join((tag.get("content") or "").split()) if tag else ""


def _length_issue(what: str, text: str, limits: tuple, fix: str) -> dict | None:
    low, high = limits
    if low <= len(text) <= high:
        return None
    too = "short" if len(text) < low else "long"
    return issue("low", f"{what} is too {too}", f"It's {len(text)} characters; aim for {low}–{high}.", fix, [text])


# --- SEO -----------------------------------------------------------------------------------------


def check_title(ctx: Context):
    title = _text(next((t for t in ctx.soup.find_all("title") if not t.find_parent("svg")), None))
    if not title:
        return issue(
            "high", "Missing page title",
            "The page has no <title>, so search results and browser tabs have nothing meaningful to show.",
            f"Add a unique, descriptive <title> of {TITLE_LENGTH[0]}–{TITLE_LENGTH[1]} characters inside <head>.",
        )
    return _length_issue(
        "Title", title, TITLE_LENGTH,
        f"Rewrite the title to {TITLE_LENGTH[0]}–{TITLE_LENGTH[1]} characters: specific enough to describe the page, "
        "short enough not to be cut off in search results.",
    )


def check_description(ctx: Context):
    description = _meta(ctx.soup, "name", "description")
    if not description:
        return issue(
            "medium", "Missing meta description",
            "Without one, search engines pick arbitrary text from the page for the result snippet.",
            f'Add <meta name="description" content="…"> with a {DESCRIPTION_LENGTH[0]}–{DESCRIPTION_LENGTH[1]} '
            "character summary of the page.",
        )
    return _length_issue(
        "Meta description", description, DESCRIPTION_LENGTH,
        f"Rewrite it as a {DESCRIPTION_LENGTH[0]}–{DESCRIPTION_LENGTH[1]} character summary that tells searchers "
        "what they'll find on the page.",
    )


def check_h1(ctx: Context):
    h1s = ctx.soup.find_all("h1")
    if not h1s:
        return issue(
            "medium", "No H1 heading",
            "The H1 tells search engines and screen reader users what the page is about.",
            "Mark up the page's main heading as a single <h1>.",
        )
    if len(h1s) > 1:
        return issue(
            "low", f"{len(h1s)} H1 headings",
            "Several H1s make it unclear which one is the page's main topic.",
            "Keep one <h1> for the main topic and change the others to <h2>–<h6>.",
            [_text(h) or _tag(h) for h in h1s],
        )


def check_heading_order(ctx: Context):
    headings = ctx.soup.find_all(re.compile(r"^h[1-6]$"))
    skips = [
        f"{a.name} “{_clip(_text(a), 40)}” → {b.name} “{_clip(_text(b), 40)}”"
        for a, b in zip(headings, headings[1:])
        if int(b.name[1]) > int(a.name[1]) + 1
    ]
    if skips:
        return issue(
            "low", f"Heading levels skipped {_plural(len(skips), 'time')}",
            "Jumping levels (for example h2 straight to h4) breaks the outline that search engines and screen "
            "readers use to understand the page.",
            "Nest headings one level at a time (h1 → h2 → h3). Use CSS, not a smaller heading tag, to change how a "
            "heading looks.",
            skips,
        )


def check_canonical(ctx: Context):
    links = ctx.soup.find_all("link", rel="canonical")
    if not links:
        return issue(
            "low", "No canonical tag",
            "Without it, search engines may treat URL variants (tracking parameters, trailing slashes) as duplicate "
            "pages and split ranking between them.",
            f'Add <link rel="canonical" href="{ctx.url}"> (or whichever URL is the preferred one) inside <head>.',
        )
    if len(links) > 1:
        return issue(
            "medium", f"{len(links)} canonical tags",
            "When canonical tags conflict, search engines ignore all of them.",
            'Keep exactly one <link rel="canonical">.',
            [_tag(link) for link in links],
        )
    if not links[0].get("href", "").strip():
        return issue(
            "medium", "Empty canonical tag",
            "The canonical tag has no URL, so search engines ignore it.",
            "Set href to the page's preferred absolute URL.",
            [_tag(links[0])],
        )


def check_open_graph(ctx: Context):
    missing = [prop for prop in OG_REQUIRED if not _meta(ctx.soup, "property", prop)]
    if not missing:
        return None
    none_present = len(missing) == len(OG_REQUIRED)
    return issue(
        "medium" if none_present else "low",
        "No Open Graph tags" if none_present else f"Missing Open Graph tags: {', '.join(missing)}",
        "Social networks and chat apps use these to build link previews. Without them, shared links show a bare "
        "or wrong title and no image.",
        f'Add <meta property="…" content="…"> inside <head> for: {", ".join(missing)}.',
    )


# --- Accessibility -------------------------------------------------------------------------------


def check_img_alt(ctx: Context):
    missing = [
        img for img in ctx.soup.find_all("img")
        if not img.has_attr("alt") and img.get("role") not in ("presentation", "none") and img.get("aria-hidden") != "true"
    ]
    if missing:
        return issue(
            "high", f"{_plural(len(missing), 'image')} missing alt text",
            "Screen readers announce these images by file name or skip them, so their meaning is lost.",
            'Add an alt attribute describing each image. Use alt="" for purely decorative images so screen readers '
            "skip them.",
            [_tag(img) for img in missing],
        )


def check_form_labels(ctx: Context):
    soup = ctx.soup
    labelled_ids = {label["for"] for label in soup.find_all("label", attrs={"for": True})}
    fields = [
        field for field in soup.find_all(["input", "select", "textarea"])
        if field.get("type", "text").lower() not in UNLABELLED_INPUT_TYPES
    ]
    unlabelled = [
        field for field in fields
        if not (
            field.get("id") in labelled_ids
            or field.find_parent("label")
            or any(field.get(attr, "").strip() for attr in ("aria-label", "aria-labelledby", "title"))
        )
    ]
    if unlabelled:
        return issue(
            "high", f"{_plural(len(unlabelled), 'form field')} without a label",
            "Screen reader users hear only “edit text” with no idea what to enter. Placeholder text doesn't count as "
            "a label.",
            'Add a <label for="field-id"> for each field, or wrap the field in a <label>. If there\'s no room for '
            "visible text, use aria-label.",
            [_tag(field) for field in unlabelled],
        )


def check_lang(ctx: Context):
    html = ctx.soup.find("html")
    if html is None or not html.get("lang", "").strip():
        return issue(
            "medium", "Missing lang attribute",
            "Screen readers use it to choose the right pronunciation, and browsers use it for translation and "
            "hyphenation.",
            'Declare the page language on the root element, for example <html lang="en">.',
        )


# --- Performance ---------------------------------------------------------------------------------


def check_page_size(ctx: Context):
    severity = _tier(ctx.total_size, PAGE_SIZE)
    if severity:
        return issue(
            severity, f"Page weighs {_bytes(ctx.total_size)}",
            f"Heavy pages load slowly, especially on mobile connections. Aim for under {_bytes(PAGE_SIZE[0])}.",
            "Resize and compress images, serve WebP or AVIF, minify and split JavaScript bundles, and lazy-load "
            "anything below the fold.",
        )


def check_requests(ctx: Context):
    severity = _tier(ctx.request_count, REQUESTS)
    if severity:
        return issue(
            severity, f"{ctx.request_count} requests",
            "Every file is a separate request, and each one adds delay, especially on mobile networks.",
            "Bundle scripts and stylesheets, remove unused third-party tags, inline or sprite small icons, and "
            'lazy-load offscreen images with loading="lazy".',
        )


def check_large_images(ctx: Context):
    large = [(url, size) for url, size in ctx.images if size > IMAGE_SIZE[0]]
    if large:
        return issue(
            _tier(large[0][1], IMAGE_SIZE),
            f"{_plural(len(large), 'image')} over {_bytes(IMAGE_SIZE[0])}",
            "Images are usually the heaviest part of a page.",
            "Resize images to the size they're displayed at, convert them to WebP or AVIF, compress them (quality "
            '75–85 usually looks identical), and add loading="lazy" to images below the fold.',
            [f"{_bytes(size)} · {url}" for url, size in large],
        )


def performance_metrics(ctx: Context) -> dict:
    unmeasured = sum(1 for size in ctx.sizes.values() if size is None)
    note = (
        "Counts only files referenced directly in the HTML. Files loaded later by JavaScript or CSS "
        "(such as fonts and background images) aren't included."
    )
    if unmeasured:
        note += f" {_plural(unmeasured, 'file')} couldn't be measured and {'is' if unmeasured == 1 else 'are'} left out of the total size."
    return {
        "stats": [
            {"label": "Total page size", "value": _bytes(ctx.total_size)},
            {"label": "Requests", "value": str(ctx.request_count)},
            {"label": "HTML size", "value": _bytes(ctx.html_size)},
        ],
        "largest_images": [{"url": url, "size": _bytes(size)} for url, size in ctx.images[:MAX_EXAMPLES]],
        "note": note,
    }


# --- Security headers ----------------------------------------------------------------------------


def check_hsts(ctx: Context):
    if not ctx.url.startswith("https://"):
        return issue(
            "high", "Site isn't served over HTTPS",
            "Traffic can be read or altered in transit, and browsers label the page “Not secure”.",
            "Install a TLS certificate (free from Let's Encrypt), redirect all HTTP traffic to HTTPS, then add a "
            "Strict-Transport-Security header.",
        )
    value = ctx.headers.get("strict-transport-security")
    if not value:
        return issue(
            "medium", "Missing Strict-Transport-Security header",
            "Without HSTS, an attacker on the same network can downgrade a visitor's first request to plain HTTP.",
            "Send the header Strict-Transport-Security: max-age=31536000; includeSubDomains",
        )
    match = re.search(r"max-age\s*=\s*\"?(\d+)", value, re.IGNORECASE)
    if not match or int(match.group(1)) < HSTS_MIN_AGE:
        return issue(
            "low", "HSTS max-age is too short",
            "With a short max-age, browsers forget the HTTPS-only rule quickly.",
            f"Set max-age to at least {HSTS_MIN_AGE} (180 days); 31536000 (one year) is typical.",
            [value],
        )


def check_csp(ctx: Context):
    if ctx.headers.get("content-security-policy") or _meta(ctx.soup, "http-equiv", "content-security-policy"):
        return None
    if ctx.headers.get("content-security-policy-report-only"):
        return issue(
            "low", "Content-Security-Policy is report-only",
            "Violations are reported but not blocked, so the policy doesn't protect visitors yet.",
            "Once the reports look clean, rename the header to Content-Security-Policy to enforce it.",
        )
    return issue(
        "medium", "Missing Content-Security-Policy header",
        "A CSP limits where scripts, styles and other content can load from, which blunts cross-site scripting "
        "(XSS) attacks.",
        "Start with Content-Security-Policy-Report-Only: default-src 'self' to see what breaks, allow the sources "
        "you need, then switch to the enforcing Content-Security-Policy header.",
    )


def check_frame_options(ctx: Context):
    # CSP frame-ancestors replaces X-Frame-Options (it only works as a header, not in a <meta> tag).
    if "frame-ancestors" in ctx.headers.get("content-security-policy", "").lower():
        return None
    value = ctx.headers.get("x-frame-options")
    if not value:
        return issue(
            "medium", "Missing X-Frame-Options header",
            "Other sites can embed this page in a hidden iframe and trick visitors into clicking things "
            "(clickjacking).",
            "Send X-Frame-Options: DENY, or SAMEORIGIN if you frame your own pages. The modern equivalent is the CSP "
            "directive frame-ancestors 'self'.",
        )
    if value.strip().upper() not in ("DENY", "SAMEORIGIN"):
        return issue(
            "low", "Invalid X-Frame-Options value",
            "Browsers only understand DENY and SAMEORIGIN (ALLOW-FROM is obsolete), so the page can still be framed.",
            "Use DENY or SAMEORIGIN. To allow specific sites, use CSP frame-ancestors instead.",
            [value],
        )


# --- Report --------------------------------------------------------------------------------------

CATEGORIES = [
    ("seo", "SEO", [
        ("Title", check_title),
        ("Meta description", check_description),
        ("Single H1", check_h1),
        ("Heading order", check_heading_order),
        ("Canonical tag", check_canonical),
        ("Open Graph tags", check_open_graph),
    ], None),
    ("accessibility", "Accessibility", [
        ("Image alt text", check_img_alt),
        ("Form labels", check_form_labels),
        ("Page language", check_lang),
    ], None),
    ("performance", "Performance", [
        ("Page size", check_page_size),
        ("Number of requests", check_requests),
        ("Image sizes", check_large_images),
    ], performance_metrics),
    ("security", "Security headers", [
        ("HTTPS and HSTS", check_hsts),
        ("Content-Security-Policy", check_csp),
        ("Clickjacking protection", check_frame_options),
    ], None),
]


def build_report(ctx: Context) -> dict:
    categories = []
    for category_id, name, checks, metrics in CATEGORIES:
        results = [(label, check(ctx)) for label, check in checks]
        issues = sorted((found for _, found in results if found), key=lambda i: -PENALTY[i["severity"]])
        categories.append({
            "id": category_id,
            "name": name,
            "score": max(0, 100 - sum(PENALTY[i["severity"]] for i in issues)),
            "issues": issues,
            "passed": [label for label, found in results if not found],
            "metrics": metrics(ctx) if metrics else None,
        })
    overall = round(sum(c["score"] for c in categories) / len(categories))
    return {"url": ctx.url, "score": overall, "categories": categories}
