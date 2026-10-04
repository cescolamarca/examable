from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.schemas import CustomSimulationIn, SimulationFromQuestionsIn
from app.services import simulations

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/custom")
def create_custom_simulation(payload: CustomSimulationIn) -> dict:
    return simulations.create_custom(payload)


@router.post("/from-questions")
def create_simulation_from_questions(payload: SimulationFromQuestionsIn) -> dict:
    return simulations.create_from_questions(payload)


@router.get("")
def list_simulations(user_id: UUID, limit: int = 20) -> list[dict]:
    return simulations.list_for_user(user_id, limit)


@router.get("/{simulation_id}")
def get_simulation(simulation_id: UUID) -> dict:
    return simulations.get(simulation_id)


@router.delete("/{simulation_id}")
def delete_simulation(simulation_id: UUID) -> dict:
    simulations.delete(simulation_id)
    return {"deleted": True, "id": str(simulation_id)}
