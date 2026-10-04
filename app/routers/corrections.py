from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.schemas import CorrectionJobFailureOut, CorrectionJobOut, CorrectionJobStartIn
from app.security import require_admin
from app.services import corrections

router = APIRouter(prefix="/corrections", tags=["corrections"])


@router.post("/jobs", response_model=CorrectionJobOut, dependencies=[Depends(require_admin)])
async def start_correction_job(payload: CorrectionJobStartIn) -> CorrectionJobOut:
    # Async on purpose: the job is scheduled as a task on the running event loop.
    return CorrectionJobOut(**corrections.start_job(payload))


@router.get("/jobs/current", response_model=CorrectionJobOut | None)
def get_current_correction_job(response: Response) -> CorrectionJobOut | None:
    job = corrections.current_job()
    if job is None:
        response.status_code = 204
        return None
    return CorrectionJobOut(**job)


@router.get("/jobs/recent", response_model=list[CorrectionJobOut])
def list_recent_correction_jobs(limit: int = 20) -> list[CorrectionJobOut]:
    return [CorrectionJobOut(**job) for job in corrections.recent_jobs(limit)]


@router.get("/jobs/{job_id}", response_model=CorrectionJobOut)
def get_correction_job(job_id: UUID) -> CorrectionJobOut:
    return CorrectionJobOut(**corrections.get_job(job_id))


@router.post("/jobs/{job_id}/cancel", response_model=CorrectionJobOut, dependencies=[Depends(require_admin)])
def cancel_correction_job(job_id: UUID) -> CorrectionJobOut:
    return CorrectionJobOut(**corrections.cancel_job(job_id))


@router.get("/jobs/{job_id}/failures", response_model=list[CorrectionJobFailureOut])
def get_correction_job_failures(job_id: UUID) -> list[CorrectionJobFailureOut]:
    return [CorrectionJobFailureOut(**f) for f in corrections.job_failures(job_id)]


@router.get("/coverage")
def get_correction_coverage(user_id: UUID, document_id: UUID | None = None) -> dict:
    return corrections.coverage(user_id, document_id)
