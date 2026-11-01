"""LangGraph Warehouse Decision Assistant endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.agents.assistant_service import DecisionAssistantService
from backend.app.config import EnvSettings
from backend.app.dependencies import (
    EntityNotFoundError,
    get_assistant_service,
    get_read_db,
    get_settings,
    get_warehouse_repo,
)
from backend.app.repositories import WarehouseRepository
from backend.app.schemas.assistant import AssistantQueryRequest, AssistantQueryResponse

router = APIRouter(prefix="/assistant", tags=["Warehouse Decision Assistant"])


@router.post("/query", response_model=AssistantQueryResponse)
def query_decision_assistant(
    payload: AssistantQueryRequest,
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    assistant: DecisionAssistantService = Depends(get_assistant_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> AssistantQueryResponse:
    """Answer an operational warehouse question via the grounded 6-node LangGraph Decision Assistant."""
    if payload.warehouse_id and wh_repo.get_warehouse_detail(db, payload.warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", payload.warehouse_id)

    return assistant.answer_query(
        session=db,
        question=payload.question,
        warehouse_id=payload.warehouse_id,
        settings_override=settings,
    )
