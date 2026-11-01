"""Pydantic v2 schemas for the controlled LangGraph Warehouse Decision Assistant."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AssistantQueryRequest(BaseModel):
    """Natural-language operational question submitted to the Warehouse Decision Assistant."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Operational warehouse question (e.g., 'Why was SKU-000001 rejected from LOC-Z003-000001?').",
    )
    warehouse_id: str | None = Field(default=None, description="Optional warehouse context filter.")


class AssistantTraceStep(BaseModel):
    """Single audited node step in the LangGraph decision assistant execution trace."""

    node_name: str
    summary: str
    record_ids: list[str] = Field(default_factory=list)


class AssistantSourceReference(BaseModel):
    """Verified relational database or artifact record cited by the Decision Assistant."""

    entity_type: str
    entity_id: str
    source_table: str
    summary: str


class AssistantQueryResponse(BaseModel):
    """Grounded structured output returned by the LangGraph Warehouse Decision Assistant."""

    question: str
    classified_intent: str
    execution_mode: str
    answer: str
    key_findings: list[str]
    recommended_actions: list[str] = Field(default_factory=list)
    supporting_metrics: dict[str, Any]
    cited_entity_ids: list[str]
    source_references: list[AssistantSourceReference] = Field(default_factory=list)
    grounding_verified: bool
    trace: list[AssistantTraceStep]
