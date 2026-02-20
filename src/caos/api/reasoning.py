"""Reasoning Store - Persistent storage for full CAOS reasoning chains.

Stores complete event processing results so the reasoning page
can display the full chain-of-thought for each event.
Persists to a JSON file to survive server restarts.
"""

import json
import structlog
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/reasoning", tags=["reasoning"])

# Persistence file path (project root / .caos_reasoning.json)
_PERSISTENCE_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".caos_reasoning.json"


class ReasoningEntry(BaseModel):
    """A complete reasoning chain for one event."""
    
    event_id: str
    timestamp: str
    
    # Trigger info
    trigger: dict[str, Any] = {}
    
    # Step 1 - Sense
    sense: dict[str, Any] = {}
    
    # Step 2 - Oracle (optional)
    oracle: dict[str, Any] = {}
    
    # Step 3 - Cortex
    cortex: dict[str, Any] = {}
    
    # Step 4 - Guardrails
    guardrails: dict[str, Any] = {}
    
    # Step 5 - Act
    act: dict[str, Any] = {}
    
    # Final result
    verdict_score: float | None = None
    decision: str | None = None
    risk_level: str | None = None
    reasoning_trace: list[str] = []
    processing_time_ms: float | None = None


class ReasoningListResponse(BaseModel):
    """Response with list of reasoning entries."""
    
    entries: list[ReasoningEntry]
    total: int


class ReasoningStore:
    """Persistent store for reasoning chains.
    
    Keeps entries in memory (deque) and persists to a JSON file
    so data survives uvicorn --reload and server restarts.
    """
    
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.entries: deque[ReasoningEntry] = deque(maxlen=max_size)
        self.lock = Lock()
        self._load_from_disk()
    
    def _load_from_disk(self) -> None:
        """Load entries from JSON file on startup."""
        try:
            if _PERSISTENCE_FILE.exists():
                data = json.loads(_PERSISTENCE_FILE.read_text(encoding="utf-8"))
                for item in data[-self.max_size:]:
                    self.entries.append(ReasoningEntry(**item))
                logger.info("reasoning_store_loaded", count=len(self.entries), path=str(_PERSISTENCE_FILE))
        except Exception as e:
            logger.warning("reasoning_store_load_error", error=str(e))
    
    def _save_to_disk(self) -> None:
        """Persist all entries to JSON file."""
        try:
            data = [entry.model_dump(mode="json") for entry in self.entries]
            _PERSISTENCE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("reasoning_store_save_error", error=str(e))
    
    def add(self, entry: ReasoningEntry) -> None:
        with self.lock:
            self.entries.append(entry)
            self._save_to_disk()
    
    def get_all(self, limit: int = 20) -> tuple[list[ReasoningEntry], int]:
        with self.lock:
            entries_list = list(self.entries)
        total = len(entries_list)
        entries_list.reverse()  # Newest first
        return entries_list[:limit], total

    def list_entries(self, limit: int = 50) -> list[ReasoningEntry]:
        """Return newest-first entries (convenience for dashboard stats)."""
        entries, _ = self.get_all(limit=limit)
        return entries
    
    def get_by_id(self, event_id: str) -> ReasoningEntry | None:
        with self.lock:
            for entry in self.entries:
                if entry.event_id == event_id:
                    return entry
        return None
    
    def clear(self) -> None:
        with self.lock:
            self.entries.clear()
            self._save_to_disk()


# Global instance
_reasoning_store: ReasoningStore | None = None


def get_reasoning_store() -> ReasoningStore:
    global _reasoning_store
    if _reasoning_store is None:
        _reasoning_store = ReasoningStore(max_size=100)
    return _reasoning_store


def store_reasoning_from_state(final_state: dict[str, Any], processing_time_ms: float) -> None:
    """Extract reasoning data from the final JudgeState and store it.
    
    Called after brain.process_trigger() completes.
    """
    store = get_reasoning_store()
    
    trigger = final_state.get("trigger")
    if not trigger:
        return
    
    event_id = trigger.event_id if hasattr(trigger, "event_id") else str(trigger.get("event_id", "unknown"))
    
    # Extract trigger info
    trigger_info = {
        "event_id": event_id,
        "source": trigger.source.value if hasattr(trigger, "source") else "unknown",
        "severity": trigger.severity.value if hasattr(trigger, "severity") else "unknown",
        "metric": trigger.metric if hasattr(trigger, "metric") else None,
        "value": trigger.value if hasattr(trigger, "value") else None,
        "asset_id": trigger.context.asset_id if hasattr(trigger, "context") else "unknown",
        "tenant_id": trigger.context.tenant_id if hasattr(trigger, "context") else "unknown",
        "location": trigger.context.location if hasattr(trigger, "context") else None,
        "timestamp": str(trigger.timestamp) if hasattr(trigger, "timestamp") else None,
    }
    
    # Extract sense (Atlas + Sentinel) info
    atlas_ctx = final_state.get("atlas_context") or {}
    sentinel_alert = final_state.get("sentinel_alert")
    sense_info = {
        "atlas_context": {
            "current_state": atlas_ctx.get("current_state", {}),
            "max_operating_temp": atlas_ctx.get("max_operating_temp"),
            "contract_tier": atlas_ctx.get("contract_tier"),
            "allowed_actions": atlas_ctx.get("allowed_actions", []),
            "under_maintenance": atlas_ctx.get("under_maintenance", False),
            "manual_excerpts": atlas_ctx.get("manual_excerpts", []),
            "simulation_data": atlas_ctx.get("simulation_data", {}),
        },
        "sentinel_alert": _safe_dict(sentinel_alert) if sentinel_alert else None,
    }
    
    # Extract Oracle info
    oracle_forecast = final_state.get("oracle_forecast")
    is_fast_track = final_state.get("is_fast_track", False)
    oracle_info = {
        "bypassed": is_fast_track,
        "reason": "fast_track (severidade alta/baixa)" if is_fast_track else None,
        "forecast": _safe_dict(oracle_forecast) if oracle_forecast else None,
    }
    
    # Extract Cortex info
    risk_dims = final_state.get("risk_dimensions")
    cortex_info = {
        "risk_dimensions": {
            "physical": risk_dims.physical if risk_dims else 0,
            "financial": risk_dims.financial if risk_dims else 0,
            "contractual": risk_dims.contractual if risk_dims else 0,
            "communication": risk_dims.communication if risk_dims else 0,
        } if risk_dims else {},
        "atlas_score": final_state.get("atlas_score"),
        "oracle_score": final_state.get("oracle_score"),
        "severity_score": final_state.get("severity_score"),
        "verdict_score": final_state.get("verdict_score"),
        "decision_band": _enum_val(final_state.get("decision_band")),
        "risk_level": _enum_val(final_state.get("risk_level")),
        "llm_analysis": final_state.get("llm_analysis"),
    }
    
    # Extract Guardrails info
    guardrails_info = {
        "violations": final_state.get("guardrail_violations", []),
        "checked": final_state.get("guardrails_checked", []),
        "passed": len(final_state.get("guardrail_violations", [])) == 0,
    }
    
    # Extract Act info
    action = final_state.get("proposed_action")
    act_info = {}
    if action:
        act_info = {
            "action_id": action.action_id if hasattr(action, "action_id") else None,
            "workflow_name": action.workflow_name if hasattr(action, "workflow_name") else None,
            "command_type": action.command.type.value if hasattr(action, "command") and action.command else None,
            "command_target": action.command.target if hasattr(action, "command") and action.command else None,
            "requires_approval": action.requires_approval if hasattr(action, "requires_approval") else None,
            "estimated_cost": action.estimated_cost if hasattr(action, "estimated_cost") else 0,
            "justification": action.justification if hasattr(action, "justification") else None,
        }
    
    decision_band = final_state.get("decision_band")
    risk_level = final_state.get("risk_level")
    
    entry = ReasoningEntry(
        event_id=event_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        trigger=trigger_info,
        sense=sense_info,
        oracle=oracle_info,
        cortex=cortex_info,
        guardrails=guardrails_info,
        act=act_info,
        verdict_score=final_state.get("verdict_score"),
        decision=_enum_val(decision_band),
        risk_level=_enum_val(risk_level),
        reasoning_trace=final_state.get("reasoning_trace", []),
        processing_time_ms=processing_time_ms,
    )
    
    store.add(entry)
    logger.info("reasoning_stored", event_id=event_id)


def _enum_val(v: Any) -> str | None:
    """Extract .value from enum or convert to str."""
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _safe_dict(obj: Any) -> dict[str, Any]:
    """Convert an object to dict safely."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return {"value": str(obj)}


# === API Endpoints ===

@router.get(
    "/",
    response_model=ReasoningListResponse,
    summary="List all reasoning chains",
)
async def list_reasoning(
    limit: int = Query(default=20, ge=1, le=100),
) -> ReasoningListResponse:
    """List all stored reasoning chains (newest first)."""
    store = get_reasoning_store()
    entries, total = store.get_all(limit=limit)
    return ReasoningListResponse(entries=entries, total=total)


@router.get(
    "/{event_id}",
    response_model=ReasoningEntry,
    summary="Get reasoning chain for an event",
)
async def get_reasoning(event_id: str) -> ReasoningEntry:
    """Get the full reasoning chain for a specific event."""
    store = get_reasoning_store()
    entry = store.get_by_id(event_id)
    if not entry:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Reasoning not found for event {event_id}")
    return entry


@router.delete(
    "/",
    summary="Clear reasoning store",
)
async def clear_reasoning() -> dict[str, str]:
    """Clear all stored reasoning chains."""
    store = get_reasoning_store()
    store.clear()
    return {"status": "ok", "message": "Reasoning store cleared"}
