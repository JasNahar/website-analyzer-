# Website Analyzer

Enter a URL, get a scored report of SEO, accessibility, performance and security-header issues, each with a severity and a suggested fix.

## Run it

**Requirements:** Python 3.11+ and Node.js 20.19+. Node can be a system install, or it can live inside the backend venv (as it does on this machine).

One-time setup:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install nodeenv; nodeenv -p     # only if Node isn't installed system-wide
cd ..\frontend
npm install
```

Every time, in two terminals, activate the venv first (`backend\.venv\Scripts\Activate.ps1`) so `node` and `npm` are on the PATH:

```powershell
# terminal 1
cd backend
uvicorn main:app --reload --port 8000

# terminal 2
cd frontend
npm run dev
```

Open http://localhost:5173. Vite forwards `/api` requests to the backend, so no CORS setup is needed.

## How it works

`POST /api/analyze` with `{"url": "example.com"}`:

1. `fetch.py` checks the URL (adding `https://` if there's no scheme), downloads the HTML, then measures every script, stylesheet, image and preloaded file the HTML references (HEAD requests, falling back to GET).
2. `checks.py` runs 15 checks over the parsed page and response headers. Each check returns at most one issue, covering every affected element.
3. Each category starts at 100 and loses **30 / 15 / 5** points per high / medium / low issue. The overall score is the average of the four categories. Penalties and thresholds are constants at the top of `checks.py`.

| Category | Checks |
|---|---|
| SEO | Title length, meta description, single H1, heading order, canonical tag, Open Graph tags |
| Accessibility | Image alt text, form field labels, `lang` attribute |
| Performance | Total page size, number of requests, large images |
| Security headers | HTTPS + HSTS, Content-Security-Policy, X-Frame-Options / `frame-ancestors` |

Failures return `{"error": {"code", "message"}}`:

| Code | Status | When |
|---|---|---|
| `invalid_url` | 400 | Malformed URL, non-http(s) scheme, or a private/local address |
| `unreachable` | 502 | DNS failure, refused connection, bad SSL certificate, redirect loop |
| `http_error` | 502 | The site answered 4xx/5xx |
| `timeout` | 504 | The HTML didn't finish downloading in 10 s |
| `not_html` | 422 | The response isn't `text/html` |
| `too_large` | 422 | The HTML is over 5 MB |

## Limitations

- **Performance is static.** No browser runs, so files loaded by JavaScript or CSS (fonts, background images, lazy-loaded bundles) aren't counted. Request counts and page size are a lower bound. At most 100 files are measured, within 15 seconds.
- **Private addresses are refused** (localhost, 10.x, 192.168.x, cloud metadata IPs, …) on every request, including redirects, so the server can't be used to probe internal networks. As a result, you can't analyze a site running on your own machine.
- **Some sites block automated requests** (for example with a 403). The error message says so.
- **Title and description length is counted in characters.** Search engines actually truncate by pixel width.
