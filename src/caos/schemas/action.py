"""Action Schema - Output contract for CARE/Cloud Workflows.

This is the command sent to Google Cloud Workflows for physical execution.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from caos.schemas.enums import ActionType, DecisionBand


class ActionCommand(BaseModel):
    """The specific command to execute."""

    type: ActionType = Field(..., description="Type of action")
    target: str = Field(..., description="Target resource (e.g., compressor, valve)")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters",
    )


class ActionSchema(BaseModel):
    """The final command sent to Cloud Workflows for execution.
    
    Example output:
    {
        "action_id": "act_x1y2z3",
        "workflow_name": "EmergencyShutdownWorkflow",
        "created_at": "2026-01-28T10:00:01Z",
        "verdict_score": -0.34,
        "decision": "BLOCKED",
        "asset_id": "CHILLER-04",
        "tenant_id": "tenant_abc",
        "command": {
            "type": "shutdown",
            "target": "compressor",
            "params": {"graceful": false}
        },
        "guardrails_passed": ["PHYS_001", "FIN_001"],
        "guardrails_violated": [],
        "requires_approval": false,
        "estimated_cost": 0.00,
        "justification": "Temperatura crítica. Risco de dano ao ativo."
    }
    """

    action_id: str = Field(..., description="Unique action identifier")
    workflow_name: str = Field(..., description="Cloud Workflow to execute")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Verdict information
    verdict_score: float = Field(
        ..., ge=-1.0, le=1.0, description="Verdict score from -1.0 to +1.0"
    )
    decision: DecisionBand = Field(..., description="Final decision classification")
    
    # Target identification
    asset_id: str = Field(..., description="Target asset identifier")
    tenant_id: str = Field(..., description="Tenant identifier")
    
    # Command details
    command: ActionCommand = Field(..., description="The command to execute")
    
    # Guardrail validation
    guardrails_passed: list[str] = Field(
        default_factory=list,
        description="IDs of guardrails that passed validation",
    )
    guardrails_violated: list[str] = Field(
        default_factory=list,
        description="IDs of guardrails that were violated",
    )
    
    # Approval and cost
    requires_approval: bool = Field(
        default=False,
        description="Whether human approval is required",
    )
    estimated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated cost of the action in BRL",
    )
    
    # Audit trail
    justification: str = Field(..., description="LLM-generated reasoning for audit")
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM confidence in the proposed action",
    )
    reasoning_trace: list[str] = Field(
        default_factory=list,
        description="Chain-of-thought steps for debugging",
    )
