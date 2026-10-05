"""Server-rendered pages; the UI itself is vanilla JS talking to the JSON API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
@router.get("/study", response_class=HTMLResponse)
def study_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "study.html", {"page": "study"})


@router.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "documents.html", {"page": "documents"})


@router.get("/bank", response_class=HTMLResponse)
def bank_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "bank.html", {"page": "bank"})
