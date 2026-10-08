from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from checks import Context, build_report, find_resources
from fetch import AnalyzeError, fetch_page, make_client, normalize_url, resource_sizes

app = FastAPI(title="Website Analyzer")


class AnalyzeRequest(BaseModel):
    url: str


@app.exception_handler(AnalyzeError)
async def handle_analyze_error(_: Request, exc: AnalyzeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest) -> dict:
    url = normalize_url(body.url)
    async with make_client() as client:
        page = await fetch_page(client, url)
        soup = BeautifulSoup(page.html, "html.parser", from_encoding=page.encoding)
        resources = find_resources(soup, page.url)
        sizes = await resource_sizes(client, list(resources))
    return build_report(Context(page.url, page.headers, soup, page.transfer_size, resources, sizes))
