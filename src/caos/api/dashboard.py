"""Dashboard API — Aggregated endpoints for the CAOS Supervisor Dashboard.

Provides /stats, /clients, /circuit-breakers, /rules and /streams
so the existing frontend JS works with real CAOS data instead of 404s.
"""

import time
import structlog
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["dashboard"])

_start_time = time.time()


# =============================================
# /stats — Overview metrics cards
# =============================================

@router.get("/stats", summary="Dashboard aggregated stats")
async def dashboard_stats() -> dict[str, Any]:
    """Return aggregated stats for the overview cards.
    
    Maps CAOS metrics + reasoning store data into the shape
    the dashboard JS expects.
    """
    from caos.observability.metrics import get_metrics
    from caos.api.reasoning import get_reasoning_store

    m = get_metrics()
    store = get_reasoning_store()
    data = m.to_dict()

    verdicts = data.get("verdicts", {})
    total = data["requests"]["total"]
    errors = data["requests"]["errors"]

    # "Validated" = EXECUTE + SUGGEST (actions that go through)
    validated = verdicts.get("EXECUTE", 0) + verdicts.get("SUGGEST", 0)
    # "Rejected" = BLOCKED + ALERT (flagged/stopped)
    rejected = verdicts.get("BLOCKED", 0) + verdicts.get("ALERT", 0)

    uptime_secs = time.time() - _start_time

    # Estimate events/min from recent entries
    recent_entries = store.list_entries(limit=50)
    events_per_minute = 0
    if recent_entries:
        now = datetime.now(timezone.utc)
        timestamps = []
        for e in recent_entries:
            try:
                ts = datetime.fromisoformat(e.timestamp) if isinstance(e.timestamp, str) else e.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                timestamps.append(ts)
            except Exception:
                pass
        if len(timestamps) >= 2:
            span_seconds = (max(timestamps) - min(timestamps)).total_seconds()
            if span_seconds > 0:
                events_per_minute = round(len(timestamps) / (span_seconds / 60), 1)

    return {
        "validated": validated,
        "rejected": rejected,
        "total": total,
        "errors": errors,
        "events_per_minute": events_per_minute,
        "uptime_seconds": round(uptime_secs),
        "avg_latency_ms": data["requests"]["avg_latency_ms"],
        "verdicts": verdicts,
        "guardrails": data["guardrails"],
        "oracle": data["oracle"],
    }


# =============================================
# /clients — Tenant list
# =============================================

@router.get("/clients", summary="List available tenants/clients")
async def list_clients() -> dict[str, Any]:
    """Return the list of tenants configured in tenants.json."""
    try:
        from caos.safety.engine import GuardrailEngine
        engine = GuardrailEngine()
        tenants = list(engine._tenant_profiles.keys()) if hasattr(engine, '_tenant_profiles') else []
    except Exception:
        tenants = []

    if not tenants:
        tenants = ["viva_demo"]

    return {
        "clients": tenants,
        "default": tenants[0] if tenants else "viva_demo",
    }


# =============================================
# /circuit-breakers — Map from guardrail engine
# =============================================

@router.get("/circuit-breakers", summary="Circuit breaker states")
async def circuit_breakers() -> dict[str, Any]:
    """Return circuit breaker status from the guardrail engine."""
    try:
        from caos._registry import get as reg_get
        cb = reg_get("circuit_breaker")
        if cb:
            # CircuitBreaker stores state
            return {
                "guardrail_engine": {
                    "state": cb._state if hasattr(cb, '_state') else "closed",
                    "failure_count": cb._failure_count if hasattr(cb, '_failure_count') else 0,
                    "max_failures": cb._max_failures if hasattr(cb, '_max_failures') else 5,
                    "last_failure": None,
                }
            }
    except Exception:
        pass

    return {
        "guardrail_engine": {
            "state": "closed",
            "failure_count": 0,
            "max_failures": 5,
            "last_failure": None,
        }
    }


# =============================================
# /rules — Guardrail rules as validation rules
# =============================================

@router.get("/rules", summary="Validation rules for dashboard")
async def get_rules() -> dict[str, Any]:
    """Return guardrail rules in the format the dashboard expects."""
    try:
        from caos.safety.engine import GuardrailEngine
        engine = GuardrailEngine()
        rules = []
        for r in engine.rules:
            rules.append({
                "id": r.id,
                "name": r.name,
                "type": r.category.lower(),
                "description": r.message,
                "severity": r.severity.value if hasattr(r.severity, 'value') else str(r.severity),
                "active": True,
            })
        return {"rules": rules, "total": len(rules)}
    except Exception as e:
        logger.warning("rules_load_error", error=str(e))
        return {"rules": [], "total": 0}


# =============================================
# /streams — Minimal stream info
# =============================================

@router.get("/streams", summary="Stream status for dashboard")
async def get_streams() -> dict[str, Any]:
    """Return a minimal streams payload so the dashboard renders."""
    from caos.observability.metrics import get_metrics
    m = get_metrics()
    total = m.request_count

    return {
        "streams": {
            "caos.triggers": {
                "name": "caos.triggers",
                "length": total,
                "consumers": 1,
                "last_id": "-",
                "status": "active" if total > 0 else "idle",
            },
            "caos.verdicts": {
                "name": "caos.verdicts",
                "length": total,
                "consumers": 1,
                "last_id": "-",
                "status": "active" if total > 0 else "idle",
            },
        },
        "total_streams": 2,
    }


# =============================================
# /hitl/pending — HITL for dashboard
# =============================================

@router.get("/hitl/pending", summary="Pending HITL approvals for dashboard")
async def hitl_pending_dashboard() -> dict[str, Any]:
    """Return pending HITL actions with extra formatting for dashboard."""
    from caos.api.feedback import get_pending_actions, check_timeouts, _pending_store, _store_lock, _feedback_log

    check_timeouts()
    pending = get_pending_actions()

    with _store_lock:
        all_actions = list(_pending_store.values())
        total_feedbacks = len(_feedback_log)

    return {
        "pending": [p.model_dump() for p in pending],
        "pending_count": len(pending),
        "total_tracked": len(all_actions),
        "total_feedbacks": total_feedbacks,
        "history": [
            a.model_dump() for a in all_actions
            if a.state.value != "PENDING"
        ][-20:],  # Last 20 resolved
    }
