from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.schemas import AttemptIn, NextQuestionResponse
from app.services import study

router = APIRouter(tags=["study"])


@router.get("/users/default")
def get_default_user() -> dict:
    return study.get_or_create_default_user()


@router.post("/attempts")
def create_attempt(payload: AttemptIn) -> dict[str, str]:
    study.record_attempt(payload)
    return {"status": "saved"}


@router.get("/attempts/stats/{user_id}")
def get_attempt_stats(user_id: UUID) -> list[dict]:
    return study.attempt_stats(user_id)


@router.get("/study/next/{user_id}", response_model=NextQuestionResponse)
def next_question(
    user_id: UUID,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
    exclude_question_id: UUID | None = None,
    exclude_question_ids: str | None = None,
    prefer_new: bool = False,
    shuffle_new: bool = False,
    review_filter: str = "all",
) -> NextQuestionResponse:
    return study.next_question(
        user_id,
        document_id=document_id,
        tag=tag,
        tag_preset=tag_preset,
        question_type=question_type,
        exclude_question_id=exclude_question_id,
        exclude_question_ids=exclude_question_ids,
        prefer_new=prefer_new,
        shuffle_new=shuffle_new,
        review_filter=review_filter,
    )


@router.get("/reviews/stats/{user_id}")
def get_review_stats(
    user_id: UUID,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
) -> dict:
    return study.correction_stats(
        user_id, document_id=document_id, tag=tag, tag_preset=tag_preset, question_type=question_type
    )


@router.get("/study/summary/{user_id}")
def get_study_summary(user_id: UUID, document_id: UUID | None = None, tag_preset: str | None = None) -> dict:
    return study.summary(user_id, document_id=document_id, tag_preset=tag_preset)
