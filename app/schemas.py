from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OptionItem(BaseModel):
    id: str
    text: str


class SubpartItem(BaseModel):
    id: str
    prompt: str


class Quality(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    needs_review: bool = False
    warnings: list[str] = Field(default_factory=list)


class SourceLoc(BaseModel):
    page_start: int
    page_end: int
    bbox: list[float] | None = None


class QuestionOut(BaseModel):
    schema_version: str = "1.0"
    question_id: UUID
    document_id: UUID
    section: Literal["quiz", "teoria", "esercizio"]
    number_in_section: int
    question_type: Literal["multiple_choice", "open_text", "multi_part_open"]
    stem: str
    options: list[OptionItem] = Field(default_factory=list)
    subparts: list[SubpartItem] = Field(default_factory=list)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    solution: dict[str, Any] = Field(default_factory=dict)
    difficulty: float = Field(ge=0.0, le=1.0, default=0.5)
    language: str = "it"
    tags: list[str] = Field(default_factory=list)
    source_loc: SourceLoc
    quality: Quality


class UploadResponse(BaseModel):
    document_id: UUID
    source_uri: str
    status: str


class ParseResponse(BaseModel):
    document_id: UUID
    extracted: int
    extraction_method: str
    extraction_quality: float = Field(ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)
    multimodal_used: bool = False
    multimodal_updates: int = 0
    multimodal_warnings: list[str] = Field(default_factory=list)


class AttemptIn(BaseModel):
    user_id: UUID
    question_id: UUID
    is_correct: bool
    grade: int | None = Field(default=None, ge=0, le=5)
    latency_ms: int | None = Field(default=None, ge=0)
    answer_payload: dict[str, Any] = Field(default_factory=dict)
    simulation_id: UUID | None = None


class NextQuestionResponse(BaseModel):
    question_id: UUID
    due_reason: str


class CustomSimulationIn(BaseModel):
    multiple_choice_count: int = Field(default=0, ge=0, le=200)
    open_text_count: int = Field(default=0, ge=0, le=200)
    multi_part_open_count: int = Field(default=0, ge=0, le=200)
    tag: str | None = None
    tag_preset: str | None = None
    document_id: UUID | None = None
    tag_presets: list[str] = Field(default_factory=list)
    document_ids: list[UUID] = Field(default_factory=list)
    user_id: UUID | None = None
    only_reviewed_correct: bool = False
    exhaustive: bool = False
    priority_mode: Literal["none", "never_viewed", "frequently_mistaken"] = "none"
    randomize: bool = True


class TagOut(BaseModel):
    id: UUID
    name: str
    slug: str
    parent_id: UUID | None = None


class TagCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=120)
    parent_id: UUID | None = None


class QuestionTagSetIn(BaseModel):
    tags: list[str] = Field(default_factory=list)


class QuestionReviewSetIn(BaseModel):
    user_id: UUID
    status: Literal["correct", "wrong"]


class QuestionReviewOut(BaseModel):
    question_id: UUID
    user_id: UUID
    status: Literal["correct", "wrong"] | None = None
    first_seen_at: str | None = None
    reviewed_at: str | None = None


class QuestionCorrectionSetIn(BaseModel):
    user_id: UUID
    correct_option_id: str | None = None
    explanation_text: str | None = None
    answer_payload: dict[str, Any] = Field(default_factory=dict)


class AdminDeleteDocumentsIn(BaseModel):
    document_ids: list[UUID]


class CorrectionJobStartIn(BaseModel):
    user_id: UUID
    mode: Literal["document", "frequency"]
    document_id: UUID | None = None


class CorrectionJobOut(BaseModel):
    id: UUID
    user_id: UUID
    mode: Literal["document", "frequency"]
    document_id: UUID | None = None
    status: Literal["queued", "running", "done", "cancelled", "interrupted", "error"]
    total_questions: int
    processed_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    current_question_id: UUID | None = None
    cancel_requested: bool
    model: str
    batch_size: int
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str


class CorrectionJobFailureOut(BaseModel):
    question_id: UUID
    error: str
    stem_preview: str
