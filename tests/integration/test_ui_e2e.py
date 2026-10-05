"""End-to-end check of the UI in a real browser (skipped when Playwright is not installed)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(client: TestClient) -> Iterator[str]:
    """The app served over HTTP on a free port (the `client` fixture already ran the migrations)."""
    from app.main import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(server: str) -> Iterator:
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - depends on the environment
            pytest.skip(f"Chromium is not available: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.errors = errors
        yield page
        browser.close()
    assert errors == [], f"JavaScript errors: {errors}"


def test_upload_curate_and_study(page, server: str, sample_exams: tuple[Path, Path]) -> None:
    expect = playwright_api.expect

    # Documents: upload both sessions at once; they are processed one after the other.
    page.goto(f"{server}/ingest")
    page.set_input_files("#file-input", [str(p) for p in sample_exams])
    expect(page.locator(".upload .badge--success")).to_have_count(2, timeout=30_000)
    expect(page.locator("tr[data-doc]")).to_have_count(2)
    expect(page.locator("#stats")).to_contain_text("7")  # unique questions after deduplication

    # Question bank: search, open a question and set its answer key.
    page.goto(f"{server}/bank")
    page.fill("input[name=q]", "orientato alla connessione")
    expect(page.locator(".qcard")).to_have_count(1)
    page.click("[data-toggle]")
    page.click("[data-key='b']")
    expect(page.locator(".toast")).to_contain_text("Risposta B salvata")
    expect(page.locator(".qcard .badge--success")).to_contain_text("Risposta B")

    # Study: the exam preset, answered from the keyboard, ends on the summary.
    page.goto(f"{server}/")
    page.click("[data-preset='exam']")
    expect(page.locator(".question__stem")).to_be_visible()
    for _ in range(20):
        if page.locator(".summary").count():
            break
        if page.locator(".option:not([disabled])").count():
            page.keyboard.press("b")
        elif page.locator("[data-action='reveal']").count():
            page.keyboard.press("Enter")
            page.keyboard.press("1")
        page.locator("[data-action='next']:not([disabled])").first.click()
    expect(page.locator(".summary h1")).to_contain_text("giuste")

    # The simulation is in the history with its score.
    page.click("[data-action='exit']")
    expect(page.locator("#history .list-item")).to_have_count(1)
