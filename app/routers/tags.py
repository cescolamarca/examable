from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.database import engine
from app.schemas import TagCreateIn
from app.security import require_admin
from app.services.errors import InvalidRequestError
from app.services.tagging import auto_tag_document, manual_tag_slug

router = APIRouter(tags=["tags"])


@router.get("/tags")
def list_tags(query: str | None = None, limit: int = 200) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.id, t.name, t.slug, t.parent_id
                FROM tags t
                WHERE CAST(:q AS TEXT) IS NULL OR t.slug ILIKE :q OR t.name ILIKE :q
                ORDER BY t.slug
                LIMIT :limit
                """
            ),
            {"q": f"%{query.strip()}%" if query and query.strip() else None, "limit": max(1, min(limit, 500))},
        ).mappings()
        return [dict(r) for r in rows]


@router.post("/tags")
def create_tag(payload: TagCreateIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise InvalidRequestError("Tag name required")
    slug = manual_tag_slug(payload.slug or name)
    if not slug:
        raise InvalidRequestError("Invalid tag slug")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO tags (name, slug, parent_id)
                VALUES (:name, :slug, :parent_id)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id
                RETURNING id, name, slug, parent_id
                """
            ),
            {"name": name, "slug": slug, "parent_id": str(payload.parent_id) if payload.parent_id else None},
        ).mappings().one()
    return dict(row)


@router.get("/tag-presets")
def list_tag_presets() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT p.id, p.name, p.slug, p.description,
                       COALESCE(jsonb_agg(t.slug ORDER BY t.slug) FILTER (WHERE t.slug IS NOT NULL), '[]'::jsonb) AS tags
                FROM tag_presets p
                LEFT JOIN tag_preset_tags pt ON pt.preset_id = p.id
                LEFT JOIN tags t ON t.id = pt.tag_id
                GROUP BY p.id, p.name, p.slug, p.description
                ORDER BY p.name
                """
            )
        ).mappings()
        return [dict(r) for r in rows]


@router.post("/tagging/recompute/document/{document_id}", dependencies=[Depends(require_admin)])
def recompute_document_tags(document_id: UUID, use_ai: bool = False) -> dict:
    with engine.begin() as conn:
        stats = auto_tag_document(conn, str(document_id), use_ai=use_ai)
    return {"document_id": str(document_id), **stats}
