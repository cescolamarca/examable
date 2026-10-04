from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import engine
from tests.sample_exams import write_sample_exams


@pytest.fixture(scope="session")
def _fresh_schema() -> None:
    """Start the session from an empty database so migrations run from scratch."""
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")


@pytest.fixture(scope="session")
def client(_fresh_schema: None) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_tables(client: TestClient) -> Iterator[None]:
    """Empty every data table between tests and re-seed tags/presets as startup does."""
    from app.services.tagging import seed_tags_and_presets

    with engine.begin() as conn:
        tables = conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'alembic_version'
                """
            )
        ).scalars().all()
        if tables:
            conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        seed_tags_and_presets(conn)
    yield


@pytest.fixture(scope="session")
def sample_exams(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return write_sample_exams(tmp_path_factory.mktemp("exams"))


@pytest.fixture
def user_id(client: TestClient) -> str:
    return client.get("/users/default").json()["id"]


def upload_and_process(client: TestClient, pdf: Path) -> str:
    with pdf.open("rb") as fh:
        response = client.post("/documents", files={"file": (pdf.name, fh, "application/pdf")})
    assert response.status_code == 200, response.text
    document_id = response.json()["document_id"]
    response = client.post(f"/documents/{document_id}/process")
    assert response.status_code == 200, response.text
    return document_id


@pytest.fixture
def ingested(client: TestClient, sample_exams: tuple[Path, Path]) -> tuple[str, str]:
    """Both sample sessions uploaded and processed (which also runs deduplication)."""
    first, second = sample_exams
    return upload_and_process(client, first), upload_and_process(client, second)
