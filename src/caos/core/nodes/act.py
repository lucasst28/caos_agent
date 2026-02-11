"""Act Node - Dispatch to CARE.

The final node that prepares and dispatches the action to Cloud Workflows.
This is where the decision becomes an executable command.

Before returning, Layer 2 (Audit) validates the proposed action for
consistency (e.g., BLOCKED with positive verdict, missing justification).
"""

import structlog
import uuid
from datetime import datetime, timezone
from typing import Any

from caos.safety.layers import get_audit_layer
from caos.schemas.action import ActionCommand, ActionSchema
from caos.schemas.enums import ActionType, DecisionBand, RiskLevel
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


def determine_workflow(decision_band: DecisionBand, risk_level: RiskLevel) -> str:
    """Determine which Cloud Workflow to execute."""
    if decision_band == DecisionBand.BLOCKED:
        return "BlockedActionWorkflow"
    elif risk_level == RiskLevel.HIGH:
        return "HumanApprovalWorkflow"
    elif decision_band == DecisionBand.EXECUTE:
        return "AutoExecuteWorkflow"
    else:
        return "SuggestActionWorkflow"


def determine_action_type(state: JudgeState) -> ActionType:
    """Determine the type of action based on context."""
    severity = state["trigger"].severity
    risk_level = state.get("risk_level", RiskLevel.MEDIUM)
    
    if risk_level == RiskLevel.VETO:
        return ActionType.NOTIFICATION  # Can't do anything, just notify
    
    if severity.value in ["CRITICAL", "HIGH"]:
        return ActionType.SHUTDOWN
    
    return ActionType.NOTIFICATION


async def act_node(state: JudgeState) -> dict[str, Any]:
    """Act Node: Prepare and Dispatch Action.
    
    Finalizes the decision and creates the ActionSchema
    to be sent to CARE via Cloud Workflows.
    
    Returns:
        Updated state with proposed_action
    """
    logger.info(
        "act_node_start",
        event_id=state["trigger"].event_id,
        decision_band=state.get("decision_band"),
    )
    
    # Get classifications
    decision_band = state.get("decision_band", DecisionBand.BLOCKED)
    risk_level = state.get("risk_level", RiskLevel.HIGH)
    
    # Determine workflow and action type
    workflow_name = determine_workflow(decision_band, risk_level)
    action_type = determine_action_type(state)
    
    # Build the command
    command = ActionCommand(
        type=action_type,
        target=state["trigger"].context.asset_id,
        params={
            "graceful": decision_band != DecisionBand.BLOCKED,
            "reason": state.get("reasoning_trace", ["No reasoning available"])[0][:200],
        },
    )
    
    # Estimate cost from Oracle financial_impact (if available)
    oracle = state.get("oracle_forecast")
    estimated_cost = 0.0
    if oracle and oracle.get("financial_impact"):
        estimated_cost = float(oracle["financial_impact"])
    
    # Build the action schema
    action = ActionSchema(
        action_id=f"act_{uuid.uuid4().hex[:8]}",
        workflow_name=workflow_name,
        created_at=datetime.now(timezone.utc),
        verdict_score=state.get("verdict_score", 0.0),
        decision=decision_band,
        asset_id=state["trigger"].context.asset_id,
        tenant_id=state["trigger"].context.tenant_id,
        command=command,
        guardrails_passed=state.get("guardrails_checked", []),
        guardrails_violated=state.get("guardrail_violations", []),
        requires_approval=state.get("requires_human_approval", True),
        estimated_cost=estimated_cost,
        justification=f"Decisão: {decision_band.value}. Risco: {risk_level.value}.",
        confidence=abs(state.get("verdict_score", 0.0)),
        reasoning_trace=state.get("reasoning_trace", []),
    )
    
    logger.info(
        "act_node_complete",
        event_id=state["trigger"].event_id,
        action_id=action.action_id,
        workflow=workflow_name,
        action_type=action_type.value,
        requires_approval=action.requires_approval,
    )
    
    result: dict[str, Any] = {
        "proposed_action": action,
        "processing_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # === Layer 2: Audit — final consistency check ===
    audit = get_audit_layer()
    # Build a temporary merged view for auditing (state + proposed action)
    audit_state = {**state, "proposed_action": action}
    audit_result = audit.check(audit_state)
    
    if not audit_result.approved:
        logger.warning(
            "audit_layer_issues",
            event_id=state["trigger"].event_id,
            action_id=action.action_id,
            issues=audit_result.issues,
            latency_ms=audit_result.latency_ms,
        )
        result["audit_issues"] = audit_result.issues
    else:
        logger.info(
            "audit_layer_passed",
            event_id=state["trigger"].event_id,
            latency_ms=audit_result.latency_ms,
        )
    
    return result
