"""Trigger Payload schema - Input contract from Sentinel/Oracle.

This is the data that "wakes up" CAOS via Pub/Sub.
All agents (Sentinel, Oracle, Co-Pilot) must respect this contract.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from caos.schemas.enums import Severity, TriggerSource


class TriggerContext(BaseModel):
    """Metadata for traceability (Tenant, Asset ID, Location)."""

    tenant_id: str = Field(..., description="Tenant identifier")
    asset_id: str = Field(..., description="Asset identifier (e.g., CHILLER-04)")
    location: str | None = Field(default=None, description="Physical location")
    model: str | None = Field(default=None, description="Equipment model")


class TriggerPayload(BaseModel):
    """The data that triggers CAOS processing.
    
    Example Sentinel payload:
    {
        "event_id": "evt_a1b2c3d4",
        "source": "sentinel",
        "timestamp": "2026-01-28T10:00:00Z",
        "severity": "CRITICAL",
        "payload": {"sensor": "chiller-01", "temp": 95.5, "metric": "temperature"},
        "context": {"tenant_id": "tenant_abc", "asset_id": "CHILLER-04"}
    }
    """

    event_id: str = Field(..., description="Unique UUID for this event")
    source: TriggerSource = Field(..., description="Origin of the trigger")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Severity = Field(default=Severity.LOW)
    
    # Flexible payload for different sources
    # Sentinel: {"sensor": "chiller-01", "temp": 95.5}
    # Oracle: {"forecast_metric": "failure_prob", "value": 0.89}
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific data payload",
    )
    
    context: TriggerContext = Field(..., description="Traceability metadata")
    
    # Optional fields for enriched triggers
    metric: str | None = Field(default=None, description="Metric that triggered the alert")
    value: float | None = Field(default=None, description="Current value of the metric")
    threshold_violated: float | None = Field(
        default=None, description="Threshold that was violated"
    )
