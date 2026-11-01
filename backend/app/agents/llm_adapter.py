"""Optional Groq / OpenAI-compatible LLM provider adapter with strict Pydantic structured outputs.

Gracefully disables itself when credentials are absent so the core application never requires API keys.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.app.config import EnvSettings, get_env_settings

AllowedIntent = Literal[
    "SKU_REJECTION_OR_CANDIDATE_EXPLANATION",
    "FAST_MOVER_RELOCATION_ANALYSIS",
    "TOP_EXCEPTIONS_ANALYSIS",
    "REPLENISHMENT_WORKLOAD_ANALYSIS",
    "PICKING_SAVINGS_ANALYSIS",
    "WAREHOUSE_CAPACITY_UTILIZATION",
    "DEMAND_FORECAST_AND_SURGE_ANALYSIS",
    "LATEST_OPTIMIZATION_AND_EXECUTIVE_SUMMARY",
]

ALLOWED_INTENTS: set[str] = {
    "SKU_REJECTION_OR_CANDIDATE_EXPLANATION",
    "FAST_MOVER_RELOCATION_ANALYSIS",
    "TOP_EXCEPTIONS_ANALYSIS",
    "REPLENISHMENT_WORKLOAD_ANALYSIS",
    "PICKING_SAVINGS_ANALYSIS",
    "WAREHOUSE_CAPACITY_UTILIZATION",
    "DEMAND_FORECAST_AND_SURGE_ANALYSIS",
    "LATEST_OPTIMIZATION_AND_EXECUTIVE_SUMMARY",
}


class LLMIntentOutput(BaseModel):
    """Pydantic-validated intent classification output from an optional LLM."""

    intent: AllowedIntent
    extracted_sku_id: str | None = None
    extracted_location_id: str | None = None
    extracted_warehouse_id: str | None = None


class LLMSynthesisOutput(BaseModel):
    """Pydantic-validated grounded narrative synthesis from an optional LLM."""

    answer: str = Field(..., min_length=10, max_length=3000)
    key_findings: list[str] = Field(default_factory=list)
    cited_entity_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_dict(raw_text: str) -> dict[str, Any]:
    """Parse JSON object from raw LLM text or fenced markdown block."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = _JSON_BLOCK_RE.search(cleaned)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("LLM response did not contain a valid JSON object.")


class LLMProviderAdapter:
    """Optional LangChain Groq/OpenAI chat adapter with automatic zero-key fallback."""

    def __init__(
        self,
        settings: EnvSettings | None = None,
        chat_model: BaseChatModel | None = None,
        provider_label_override: str | None = None,
    ) -> None:
        self.settings = settings or get_env_settings()
        self._injected_model = chat_model
        self._provider_label_override = provider_label_override
        self._resolved_model: BaseChatModel | None = None
        self._provider_name: str = "NONE"
        self._unavailable_reason: str | None = None
        self._initialize_model()

    def _initialize_model(self) -> None:
        if self._injected_model is not None:
            self._resolved_model = self._injected_model
            self._provider_name = (self._provider_label_override or "INJECTED_LLM").upper()
            return

        provider = (self.settings.llm_provider or "none").lower().strip()
        if provider in {"none", "", "disabled", "fallback"}:
            self._unavailable_reason = "llm_provider_disabled"
            return

        if provider == "groq":
            api_key = (self.settings.groq_api_key or os.getenv("GROQ_API_KEY", "")).strip()
            if not api_key:
                self._unavailable_reason = "missing_groq_api_key"
                return
            from langchain_groq import ChatGroq

            self._resolved_model = ChatGroq(
                model=self.settings.llm_model,
                api_key=api_key,  # type: ignore[arg-type]
                temperature=self.settings.llm_temperature,
                max_retries=1,
            )
            self._provider_name = "GROQ"
            return

        if provider in {"openai", "openai_compatible"}:
            api_key = (self.settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
            if not api_key:
                self._unavailable_reason = "missing_openai_api_key"
                return
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {
                "model": self.settings.llm_model,
                "api_key": api_key,
                "temperature": self.settings.llm_temperature,
                "max_retries": 1,
            }
            base_url = (self.settings.openai_base_url or os.getenv("OPENAI_BASE_URL", "")).strip()
            if base_url:
                kwargs["base_url"] = base_url
            self._resolved_model = ChatOpenAI(**kwargs)
            self._provider_name = "OPENAI"
            return

        self._unavailable_reason = f"unsupported_provider:{provider}"

    @property
    def is_available(self) -> bool:
        return self._resolved_model is not None

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def classify_intent_structured(self, question: str) -> LLMIntentOutput | None:
        """Optionally classify operational intent via LLM, validated strictly with Pydantic."""
        if self._resolved_model is None:
            return None

        sys_prompt = (
            "You are the SlotWise AI Warehouse Decision Assistant intent router. "
            "Return ONLY a JSON object matching the schema: "
            '{"intent": "<ONE_OF_ALLOWED>", "extracted_sku_id": null|"SKU-000001", '
            '"extracted_location_id": null|"LOC-Z001-000001", "extracted_warehouse_id": null|"WH-001"}. '
            f"Allowed intents: {sorted(ALLOWED_INTENTS)}."
        )
        try:
            resp = self._resolved_model.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=question)])
            raw_content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
            parsed = _extract_json_dict(raw_content)
            return LLMIntentOutput.model_validate(parsed)
        except Exception:
            return None

    def synthesize_grounded_response(
        self,
        question: str,
        intent: str,
        deterministic_findings: list[str],
        deterministic_actions: list[str],
        supporting_metrics: dict[str, Any],
        verified_entity_ids: list[str],
    ) -> LLMSynthesisOutput | None:
        """Synthesize a Pydantic-validated explanation strictly from SQL-verified operational facts."""
        if self._resolved_model is None:
            return None

        grounding_payload = {
            "question": question,
            "classified_intent": intent,
            "verified_findings": deterministic_findings,
            "verified_recommended_actions": deterministic_actions,
            "supporting_metrics": supporting_metrics,
            "allowed_entity_ids": verified_entity_ids,
        }
        sys_prompt = (
            "You are the SlotWise AI Warehouse Decision Assistant. "
            "CRITICAL ANTI-FABRICATION RULE: You MUST answer strictly using the numbers and entity IDs "
            "present in the provided JSON payload. NEVER invent or guess SKU IDs, Location IDs, Warehouse IDs, "
            "Optimization Run IDs, Exception IDs, or KPI numbers. "
            "Return ONLY a valid JSON object matching this exact Pydantic schema: "
            '{"answer": "<concise grounded answer>", "key_findings": ["..."], '
            '"cited_entity_ids": ["..."], "recommended_actions": ["..."]}.'
        )
        try:
            # First try native structured output if supported, falling back to direct JSON invocation
            resp = self._resolved_model.invoke(
                [
                    SystemMessage(content=sys_prompt),
                    HumanMessage(content=json.dumps(grounding_payload, default=str)),
                ]
            )
            raw_content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
            parsed = _extract_json_dict(raw_content)
            return LLMSynthesisOutput.model_validate(parsed)
        except Exception:
            return None
