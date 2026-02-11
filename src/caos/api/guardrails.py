"""Guardrails API - Query and manage guardrail policies."""

import structlog
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from caos.safety.engine import GuardrailEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/guardrails", tags=["guardrails"])


# === Response Models ===


class GuardrailInfo(BaseModel):
    """Information about a single guardrail rule."""

    id: str
    category: str
    name: str
    condition: str
    action: str
    severity: str
    message: str


class GuardrailListResponse(BaseModel):
    """Response with list of guardrail rules."""

    guardrails: list[GuardrailInfo]
    total: int
    categories: dict[str, int] = Field(default_factory=dict)


class GuardrailStatsResponse(BaseModel):
    """Statistics about guardrail rules."""

    total_rules: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
    by_action: dict[str, int]


# === Endpoints ===


@router.get(
    "/",
    response_model=GuardrailListResponse,
    summary="List all guardrail rules",
    description="Get all configured guardrail rules with optional category filter.",
)
async def list_guardrails(
    category: str | None = Query(default=None, description="Filter by category"),
) -> GuardrailListResponse:
    """List all guardrail rules."""
    engine = GuardrailEngine()

    if category:
        rules = engine.get_rules_by_category(category.upper())
    else:
        rules = engine.rules

    guardrails = [
        GuardrailInfo(
            id=r.id,
            category=r.category,
            name=r.name,
            condition=r.condition,
            action=r.action.value,
            severity=r.severity.value,
            message=r.message,
        )
        for r in rules
    ]

    # Count by category
    categories: dict[str, int] = {}
    for r in rules:
        categories[r.category] = categories.get(r.category, 0) + 1

    return GuardrailListResponse(
        guardrails=guardrails,
        total=len(guardrails),
        categories=categories,
    )


@router.get(
    "/stats",
    response_model=GuardrailStatsResponse,
    summary="Get guardrail statistics",
)
async def get_guardrail_stats() -> GuardrailStatsResponse:
    """Get statistics about guardrail rules."""
    engine = GuardrailEngine()

    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_action: dict[str, int] = {}

    for r in engine.rules:
        by_category[r.category] = by_category.get(r.category, 0) + 1
        by_severity[r.severity.value] = by_severity.get(r.severity.value, 0) + 1
        by_action[r.action.value] = by_action.get(r.action.value, 0) + 1

    return GuardrailStatsResponse(
        total_rules=len(engine.rules),
        by_category=by_category,
        by_severity=by_severity,
        by_action=by_action,
    )
