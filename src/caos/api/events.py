"""Events API - Manual event ingestion and trigger processing."""

import uuid
import structlog
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from caos.api.auth import require_api_key
from caos.core.brain import process_trigger
from caos.schemas.enums import DecisionBand, Severity, TriggerSource
from caos.schemas.trigger import TriggerContext, TriggerPayload

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


# === Request/Response Models ===


class ManualTriggerRequest(BaseModel):
    """Request body for manually triggering CAOS processing."""

    tenant_id: str = Field(..., description="Tenant identifier")
    asset_id: str = Field(..., description="Asset identifier (e.g., CHILLER-04)")
    severity: Severity = Field(default=Severity.MEDIUM)
    metric: str | None = Field(default=None, description="Metric name (e.g., temperature)")
    value: float | None = Field(default=None, description="Current metric value")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional data")
    location: str | None = Field(default=None)


class EventResponse(BaseModel):
    """Response after processing an event."""

    event_id: str
    verdict_score: float | None = None
    decision: str | None = None
    risk_level: str | None = None
    action_id: str | None = None
    requires_approval: bool = False
    guardrail_violations: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    processing_time_ms: float | None = None


class EventListResponse(BaseModel):
    """Response with list of recent events."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# === Endpoints ===


@router.post(
    "/trigger",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger manual CAOS processing",
    description="Manually inject an event into CAOS for processing. "
    "Useful for testing, co-pilot triggers, or API-based integrations.",
    dependencies=[Depends(require_api_key)],
)
async def trigger_event(request: ManualTriggerRequest) -> EventResponse:
    """Process a manually triggered event through the CAOS brain."""
    start_time = datetime.now(timezone.utc)
    event_id = f"evt_{uuid.uuid4().hex[:8]}"

    logger.info(
        "manual_trigger_received",
        event_id=event_id,
        tenant_id=request.tenant_id,
        asset_id=request.asset_id,
        severity=request.severity.value,
    )

    try:
        # Build trigger payload
        trigger_data = {
            "event_id": event_id,
            "source": TriggerSource.MANUAL.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": request.severity.value,
            "payload": request.payload,
            "context": {
                "tenant_id": request.tenant_id,
                "asset_id": request.asset_id,
                "location": request.location,
            },
            "metric": request.metric,
            "value": request.value,
        }

        # Process through brain
        final_state = await process_trigger(trigger_data)

        # Calculate processing time
        processing_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

        # Store full reasoning chain for the reasoning page
        from caos.api.reasoning import store_reasoning_from_state
        try:
            store_reasoning_from_state(final_state, processing_time)
        except Exception:
            pass  # Don't fail the request if reasoning store fails

        # Extract action info
        action = final_state.get("proposed_action")
        action_id = None
        if action:
            action_id = action.action_id if hasattr(action, "action_id") else str(action)

        decision_band = final_state.get("decision_band")
        decision_str = decision_band.value if isinstance(decision_band, DecisionBand) else str(decision_band) if decision_band else None

        risk_level = final_state.get("risk_level")
        risk_str = risk_level.value if hasattr(risk_level, "value") else str(risk_level) if risk_level else None

        return EventResponse(
            event_id=event_id,
            verdict_score=final_state.get("verdict_score"),
            decision=decision_str,
            risk_level=risk_str,
            action_id=action_id,
            requires_approval=final_state.get("requires_human_approval", False),
            guardrail_violations=final_state.get("guardrail_violations", []),
            reasoning=final_state.get("reasoning_trace", []),
            processing_time_ms=round(processing_time, 2),
        )

    except Exception as e:
        logger.error(
            "trigger_processing_error",
            event_id=event_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Processing failed: {str(e)}",
        )


@router.get(
    "/{event_id}",
    response_model=dict[str, Any],
    summary="Get event details",
    description="Retrieve details of a previously processed event.",
)
async def get_event(event_id: str) -> dict[str, Any]:
    """Get details of a processed event."""
    # Try reasoning store first
    from caos.api.reasoning import get_reasoning_store
    store = get_reasoning_store()
    entry = store.get_by_id(event_id)
    if entry:
        return entry.model_dump(mode="json")

    # TODO (E6): Implement Firestore lookup when GCP is available
    logger.info("get_event_not_found", event_id=event_id)
    from fastapi import HTTPException
    raise HTTPException(
        status_code=404,
        detail=f"Event {event_id} not found. Storage backend not yet available (planned E6).",
    )
