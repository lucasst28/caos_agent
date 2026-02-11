"""Feedback API - Human feedback loop for CAOS decisions."""

import structlog
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


# === Request/Response Models ===


class FeedbackRequest(BaseModel):
    """Human feedback on a CAOS decision."""

    event_id: str = Field(..., description="Event that was evaluated")
    action_id: str = Field(..., description="Action that was proposed")
    approved: bool = Field(..., description="Whether the action was approved")
    reviewer: str = Field(..., description="Name/ID of the reviewer")
    reason: str | None = Field(default=None, description="Reason for decision")
    corrected_action: str | None = Field(
        default=None, description="Corrected action type if rejected"
    )


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""

    feedback_id: str
    event_id: str
    action_id: str
    approved: bool
    status: str = "recorded"
    created_at: str


class ApprovalRequest(BaseModel):
    """Request to approve or reject a pending action."""

    action_id: str = Field(..., description="Action ID to approve/reject")
    approved: bool = Field(..., description="Approve or reject")
    approver: str = Field(..., description="Approver name/ID")
    notes: str | None = Field(default=None)


class ApprovalResponse(BaseModel):
    """Response after processing approval."""

    action_id: str
    approved: bool
    status: str
    dispatched: bool = False


# === Endpoints ===


@router.post(
    "/",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback on a decision",
    description="Submit human feedback on a CAOS decision for the learning loop.",
)
async def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """Record human feedback on a CAOS decision."""
    import uuid

    feedback_id = f"fb_{uuid.uuid4().hex[:8]}"

    logger.info(
        "feedback_received",
        feedback_id=feedback_id,
        event_id=request.event_id,
        action_id=request.action_id,
        approved=request.approved,
        reviewer=request.reviewer,
    )

    # TODO: Store in Firestore for learning loop
    # TODO: If approved and was pending, dispatch to CARE

    return FeedbackResponse(
        feedback_id=feedback_id,
        event_id=request.event_id,
        action_id=request.action_id,
        approved=request.approved,
        status="recorded",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/approve",
    response_model=ApprovalResponse,
    summary="Approve or reject a pending action",
    description="Approve or reject an action that requires HITL approval.",
)
async def approve_action(request: ApprovalRequest) -> ApprovalResponse:
    """Approve or reject a pending action (HITL)."""
    logger.info(
        "approval_request",
        action_id=request.action_id,
        approved=request.approved,
        approver=request.approver,
    )

    if request.approved:
        # TODO: Dispatch to CARE via CareDispatcher
        logger.info("action_approved", action_id=request.action_id)
        return ApprovalResponse(
            action_id=request.action_id,
            approved=True,
            status="approved_and_dispatched",
            dispatched=True,
        )
    else:
        logger.info("action_rejected", action_id=request.action_id)
        return ApprovalResponse(
            action_id=request.action_id,
            approved=False,
            status="rejected",
            dispatched=False,
        )


@router.get(
    "/stats",
    response_model=dict[str, Any],
    summary="Get feedback statistics",
    description="Get statistics about human feedback and approval rates.",
)
async def get_feedback_stats() -> dict[str, Any]:
    """Get feedback statistics for the learning loop."""
    # TODO: Aggregate from Firestore
    return {
        "total_feedbacks": 0,
        "approval_rate": 0.0,
        "avg_response_time_minutes": 0.0,
        "by_decision": {
            "BLOCKED": {"total": 0, "approved": 0},
            "ALERT": {"total": 0, "approved": 0},
            "SUGGEST": {"total": 0, "approved": 0},
            "EXECUTE": {"total": 0, "approved": 0},
        },
    }
