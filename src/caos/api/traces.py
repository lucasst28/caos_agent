"""Traces API - Query cognitive traces from LangSmith."""

import structlog
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/traces", tags=["traces"])


# === Response Models ===


class TraceStep(BaseModel):
    """A single step in the reasoning trace."""

    node: str = Field(..., description="Node that executed this step")
    timestamp: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    duration_ms: float | None = None


class TraceResponse(BaseModel):
    """Full trace for an event."""

    event_id: str
    run_id: str | None = None
    status: str = "completed"
    steps: list[TraceStep] = Field(default_factory=list)
    verdict_score: float | None = None
    decision: str | None = None
    total_duration_ms: float | None = None


class TraceListResponse(BaseModel):
    """Response with list of recent traces."""

    traces: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


# === Endpoints ===


@router.get(
    "/",
    response_model=TraceListResponse,
    summary="List recent traces",
    description="Query recent cognitive traces from LangSmith.",
)
async def list_traces(
    tenant_id: str | None = Query(default=None, description="Filter by tenant"),
    asset_id: str | None = Query(default=None, description="Filter by asset"),
    decision: str | None = Query(default=None, description="Filter by decision band"),
    limit: int = Query(default=20, le=100, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
) -> TraceListResponse:
    """List recent cognitive traces."""
    logger.info(
        "list_traces",
        tenant_id=tenant_id,
        asset_id=asset_id,
        decision=decision,
    )

    # TODO: Query from LangSmith API or Firestore
    return TraceListResponse(
        traces=[],
        total=0,
        page=offset // limit + 1,
        page_size=limit,
    )


@router.get(
    "/{event_id}",
    response_model=TraceResponse,
    summary="Get trace for an event",
    description="Get the full cognitive trace (chain-of-thought) for a specific event.",
)
async def get_trace(event_id: str) -> TraceResponse:
    """Get the cognitive trace for an event."""
    logger.info("get_trace", event_id=event_id)

    # TODO (E6): Query from LangSmith API when API key is configured
    return TraceResponse(
        event_id=event_id,
        status="unavailable",
        steps=[],
    )


@router.get(
    "/{event_id}/reasoning",
    response_model=dict[str, Any],
    summary="Get human-readable reasoning",
    description="Get a human-friendly explanation of the decision reasoning.",
)
async def get_reasoning(event_id: str) -> dict[str, Any]:
    """Get human-readable decision reasoning."""
    logger.info("get_reasoning", event_id=event_id)

    # TODO (E6): Format from stored trace when Firestore is available
    return {
        "event_id": event_id,
        "status": "unavailable",
        "detail": "Reasoning query not yet implemented. Requires Firestore/LangSmith (planned for E6).",
    }
