"""Feedback API - Human-in-the-Loop (HITL) Approval & Feedback system.

Implements the full HITL state machine per doc §8.2:
  PENDING → APPROVED → dispatched to CARE
  PENDING → REJECTED → logged, no dispatch
  PENDING → TIMEOUT → AUTO_APPROVED (configurable, default 30min)

Includes:
  - Background timer thread for automatic timeout processing
  - SSE stream for real-time dashboard notifications
  - Full history endpoint for resolved actions
"""

import asyncio
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from caos.api.auth import require_api_key

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


# =============================================
# HITL State Machine
# =============================================

class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    AUTO_APPROVED = "AUTO_APPROVED"


class PendingAction(BaseModel):
    """An action waiting for human approval."""
    action_id: str
    event_id: str
    asset_id: str
    tenant_id: str
    decision_band: str
    risk_level: str
    action_type: str
    verdict_score: float
    justification: str
    reasoning_trace: list[str] = []
    state: ApprovalState = ApprovalState.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str | None = None
    resolved_by: str | None = None
    notes: str | None = None


# In-memory store (production: Firestore)
_pending_store: dict[str, PendingAction] = {}
_feedback_log: list[dict] = []
_store_lock = threading.Lock()

# Auto-approval timeout by risk level (seconds), 0 = never auto-approve
HITL_TIMEOUT_BY_RISK: dict[str, int] = {
    "LOW": 900,       # 15 min — low risk, auto-approve quickly
    "MEDIUM": 1800,   # 30 min — standard timeout
    "HIGH": 0,        # NEVER auto-approve — requires human decision
    "VETO": 0,        # NEVER auto-approve — safety-critical
}
HITL_TIMEOUT_DEFAULT = 1800  # fallback for unknown risk levels

# SSE event queue — listeners subscribe here
_sse_subscribers: list[asyncio.Queue] = []
_sse_lock = threading.Lock()


# =============================================
# SSE Event Broadcasting
# =============================================

def _broadcast_sse(event_type: str, data: dict) -> None:
    """Broadcast an event to all SSE subscribers (non-blocking)."""
    import json
    payload = json.dumps({"type": event_type, **data}, default=str)
    with _sse_lock:
        dead: list[asyncio.Queue] = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        # Clean up dead subscribers
        for q in dead:
            _sse_subscribers.remove(q)


# =============================================
# Core HITL Functions
# =============================================

def submit_for_approval(action_data: dict) -> PendingAction:
    """Submit an action for HITL approval. Returns the PendingAction record."""
    action_id = action_data.get("action_id", f"act_{uuid.uuid4().hex[:8]}")
    pending = PendingAction(
        action_id=action_id,
        event_id=action_data.get("event_id", ""),
        asset_id=action_data.get("asset_id", ""),
        tenant_id=action_data.get("tenant_id", ""),
        decision_band=action_data.get("decision_band", "SUGGEST"),
        risk_level=action_data.get("risk_level", "HIGH"),
        action_type=action_data.get("action_type", "notification"),
        verdict_score=action_data.get("verdict_score", 0.0),
        justification=action_data.get("justification", ""),
        reasoning_trace=action_data.get("reasoning_trace", []),
    )
    with _store_lock:
        _pending_store[action_id] = pending
    logger.info(
        "hitl_pending",
        action_id=action_id,
        asset_id=pending.asset_id,
        risk_level=pending.risk_level,
    )
    # Notify SSE subscribers
    _broadcast_sse("pending", {
        "action_id": action_id,
        "asset_id": pending.asset_id,
        "risk_level": pending.risk_level,
        "decision_band": pending.decision_band,
        "verdict_score": pending.verdict_score,
    })
    return pending


def resolve_pending(action_id: str, approved: bool, resolver: str, notes: str | None = None) -> PendingAction | None:
    """Resolve a pending action. Returns updated record or None if not found."""
    with _store_lock:
        pending = _pending_store.get(action_id)
        if not pending or pending.state != ApprovalState.PENDING:
            return None
        pending.state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        pending.resolved_at = datetime.now(timezone.utc).isoformat()
        pending.resolved_by = resolver
        pending.notes = notes
    logger.info(
        "hitl_resolved",
        action_id=action_id,
        state=pending.state.value,
        resolver=resolver,
    )
    # Notify SSE subscribers
    _broadcast_sse("resolved", {
        "action_id": action_id,
        "state": pending.state.value,
        "resolver": resolver,
    })
    # RLHF: Forward feedback for Bayesian weight update
    try:
        from caos.config import get_settings as _gs
        if _gs().rlhf_enabled:
            from caos.core.rlhf import get_rlhf_optimizer
            get_rlhf_optimizer().process_feedback(
                action_id=action_id,
                approved=approved,
                resolver=resolver,
            )
    except Exception as e:
        logger.error("rlhf_feedback_error", action_id=action_id, error=str(e))
    return pending


def check_timeouts() -> list[str]:
    """Check for timed-out pending actions and auto-approve them.
    
    Uses per-risk-level timeouts: HIGH and VETO never auto-approve.
    Returns list of auto-approved action_ids.
    """
    now = datetime.now(timezone.utc)
    auto_approved = []
    with _store_lock:
        for action_id, pending in _pending_store.items():
            if pending.state != ApprovalState.PENDING:
                continue
            # Determine timeout for this action's risk level
            timeout = HITL_TIMEOUT_BY_RISK.get(
                pending.risk_level.upper(), HITL_TIMEOUT_DEFAULT
            )
            if timeout <= 0:
                # This risk level NEVER auto-approves
                continue
            created = datetime.fromisoformat(pending.created_at)
            elapsed = (now - created).total_seconds()
            if elapsed >= timeout:
                pending.state = ApprovalState.AUTO_APPROVED
                pending.resolved_at = now.isoformat()
                pending.resolved_by = "system_timeout"
                pending.notes = (
                    f"Auto-approved after {elapsed:.0f}s "
                    f"(timeout={timeout}s for risk={pending.risk_level})"
                )
                auto_approved.append(action_id)
                logger.warning(
                    "hitl_auto_approved",
                    action_id=action_id,
                    risk_level=pending.risk_level,
                    elapsed_seconds=elapsed,
                    timeout_seconds=timeout,
                )
    # Notify SSE subscribers for each auto-approval
    for action_id in auto_approved:
        _broadcast_sse("auto_approved", {
            "action_id": action_id,
            "risk_level": _pending_store.get(action_id, PendingAction(
                action_id="", event_id="", asset_id="", tenant_id="",
                decision_band="", risk_level="", action_type="",
                verdict_score=0.0, justification="",
            )).risk_level,
        })
    return auto_approved


def get_pending_actions(asset_id: str | None = None) -> list[PendingAction]:
    """Get all pending actions, optionally filtered by asset."""
    with _store_lock:
        actions = list(_pending_store.values())
    if asset_id:
        actions = [a for a in actions if a.asset_id == asset_id]
    return [a for a in actions if a.state == ApprovalState.PENDING]


def get_resolved_actions(
    limit: int = 50,
    offset: int = 0,
    state_filter: str | None = None,
) -> tuple[list[PendingAction], int]:
    """Get resolved actions (APPROVED, REJECTED, AUTO_APPROVED) with pagination."""
    terminal_states = {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.AUTO_APPROVED}
    with _store_lock:
        resolved = [a for a in _pending_store.values() if a.state in terminal_states]
    if state_filter:
        resolved = [a for a in resolved if a.state.value == state_filter.upper()]
    total = len(resolved)
    # Sort newest first
    resolved.sort(key=lambda a: a.resolved_at or "", reverse=True)
    return resolved[offset:offset + limit], total


# =============================================
# Background Timeout Timer
# =============================================

_timer_thread: threading.Thread | None = None
_timer_stop = threading.Event()


def start_hitl_timer(interval_seconds: float = 30.0) -> None:
    """Start background thread that checks for HITL timeouts periodically."""
    global _timer_thread

    def _timer_loop():
        logger.info("hitl_timer_started", interval=interval_seconds)
        while not _timer_stop.is_set():
            try:
                auto_approved = check_timeouts()
                if auto_approved:
                    logger.info("hitl_timer_batch", count=len(auto_approved))
            except Exception as e:
                logger.error("hitl_timer_error", error=str(e))
            _timer_stop.wait(timeout=interval_seconds)
        logger.info("hitl_timer_stopped")

    _timer_stop.clear()
    _timer_thread = threading.Thread(target=_timer_loop, daemon=True, name="hitl-timer")
    _timer_thread.start()


def stop_hitl_timer() -> None:
    """Stop the background timeout timer."""
    global _timer_thread
    _timer_stop.set()
    if _timer_thread and _timer_thread.is_alive():
        _timer_thread.join(timeout=5.0)
    _timer_thread = None


# =============================================
# Request/Response Models
# =============================================

class FeedbackRequest(BaseModel):
    """Human feedback on a CAOS decision."""
    event_id: str = Field(..., description="Event that was evaluated")
    action_id: str = Field(..., description="Action that was proposed")
    approved: bool = Field(..., description="Whether the action was approved")
    reviewer: str = Field(..., description="Name/ID of the reviewer")
    reason: str | None = Field(default=None, description="Reason for decision")
    corrected_action: str | None = Field(default=None, description="Corrected action type if rejected")


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
    state: str
    dispatched: bool = False
    elapsed_seconds: float | None = None


class HistoryResponse(BaseModel):
    """Paginated list of resolved actions."""
    actions: list[PendingAction]
    total: int
    limit: int
    offset: int


# =============================================
# Endpoints
# =============================================

@router.post(
    "/",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback on a decision",
    dependencies=[Depends(require_api_key)],
)
async def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """Record human feedback on a CAOS decision for the learning loop."""
    feedback_id = f"fb_{uuid.uuid4().hex[:8]}"
    
    # Log to feedback store
    entry = {
        "feedback_id": feedback_id,
        "event_id": request.event_id,
        "action_id": request.action_id,
        "approved": request.approved,
        "reviewer": request.reviewer,
        "reason": request.reason,
        "corrected_action": request.corrected_action,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _store_lock:
        _feedback_log.append(entry)

    logger.info("feedback_received", **{k: v for k, v in entry.items() if k != "created_at"})

    # If this feedback is for a pending action, resolve it
    pending = resolve_pending(request.action_id, request.approved, request.reviewer, request.reason)
    
    # --- RLHF Learning Loop Trigger ---
    try:
        from caos.config import get_settings as _gs
        # Always try to learn if configured
        if _gs().rlhf_enabled:
            from caos.core.rlhf import get_rlhf_optimizer
            get_rlhf_optimizer().process_feedback(
                action_id=request.action_id,
                approved=request.approved,
                resolver=request.reviewer
            )
    except Exception as e:
        logger.error("rlhf_learning_failed", error=str(e))

    return FeedbackResponse(
        feedback_id=feedback_id,
        event_id=request.event_id,
        action_id=request.action_id,
        approved=request.approved,
        status="recorded_and_learned",
        created_at=entry["created_at"],
    )


@router.post(
    "/approve",
    response_model=ApprovalResponse,
    summary="Approve or reject a pending action",
    dependencies=[Depends(require_api_key)],
)
async def approve_action(request: ApprovalRequest) -> ApprovalResponse:
    """Approve or reject an action that requires HITL approval."""
    result = resolve_pending(request.action_id, request.approved, request.approver, request.notes)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {request.action_id} not found or already resolved",
        )

    dispatched = False
    if result.state == ApprovalState.APPROVED:
        # Dispatch to CARE
        try:
            from caos.integration.care import get_care_dispatcher
            dispatcher = get_care_dispatcher()
            # Build minimal action dict for dispatch
            await dispatcher.dispatch({
                "action_id": result.action_id,
                "asset_id": result.asset_id,
                "action_type": result.action_type,
                "approved_by": request.approver,
            })
            dispatched = True
        except Exception as e:
            logger.error("hitl_dispatch_failed", action_id=result.action_id, error=str(e))

    elapsed = None
    if result.resolved_at and result.created_at:
        created = datetime.fromisoformat(result.created_at)
        resolved = datetime.fromisoformat(result.resolved_at)
        elapsed = (resolved - created).total_seconds()

    return ApprovalResponse(
        action_id=result.action_id,
        approved=request.approved,
        state=result.state.value,
        dispatched=dispatched,
        elapsed_seconds=elapsed,
    )


@router.get(
    "/pending",
    response_model=list[PendingAction],
    summary="List pending HITL approvals",
)
async def list_pending(asset_id: str | None = None) -> list[PendingAction]:
    """List all actions awaiting HITL approval."""
    # First check for timeouts
    check_timeouts()
    return get_pending_actions(asset_id)


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Get resolved action history",
    description="List all resolved actions (APPROVED, REJECTED, AUTO_APPROVED) with pagination.",
)
async def action_history(
    state: str | None = Query(default=None, description="Filter by state (APPROVED, REJECTED, AUTO_APPROVED)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> HistoryResponse:
    """Get the full history of resolved HITL actions."""
    actions, total = get_resolved_actions(limit=limit, offset=offset, state_filter=state)
    return HistoryResponse(actions=actions, total=total, limit=limit, offset=offset)


@router.get(
    "/stream",
    summary="SSE stream for real-time HITL notifications",
    description="Server-Sent Events stream. Events: pending, resolved, auto_approved.",
)
async def sse_stream():
    """Real-time SSE stream for HITL events (dashboard integration)."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(queue)

    async def event_generator():
        try:
            # Send initial heartbeat
            yield "event: connected\ndata: {\"status\": \"ok\"}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            with _sse_lock:
                if queue in _sse_subscribers:
                    _sse_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/stats",
    response_model=dict[str, Any],
    summary="Get feedback statistics",
)
async def get_feedback_stats() -> dict[str, Any]:
    """Get feedback and HITL statistics."""
    with _store_lock:
        total = len(_pending_store)
        by_state = {}
        for p in _pending_store.values():
            by_state[p.state.value] = by_state.get(p.state.value, 0) + 1
        total_feedback = len(_feedback_log)
        approved_count = sum(1 for f in _feedback_log if f.get("approved"))

    return {
        "total_actions_tracked": total,
        "by_state": by_state,
        "total_feedbacks": total_feedback,
        "approval_rate": approved_count / total_feedback if total_feedback > 0 else 0.0,
        "hitl_timeout_seconds": HITL_TIMEOUT_SECONDS,
        "timer_active": _timer_thread is not None and _timer_thread.is_alive(),
    }

