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
from caos.safety.runtime import (
    get_anti_flapping,
    get_asset_mutex,
    get_backpressure_guard,
    get_behavioral_anomaly,
    get_notification_router,
)
from caos.api.feedback import submit_for_approval
from caos.schemas.action import ActionCommand, ActionSchema
from caos.schemas.enums import ActionType, DecisionBand, RiskLevel
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)

# =============================================
# Risk Overrides (doc §4.1.3)
# Actions that ALWAYS receive a fixed risk level
# regardless of the calculated verdict
# =============================================
ALWAYS_HIGH_ACTIONS = {
    "delete", "firmware_update", "security_change", "admin_override",
    "factory_reset", "credential_rotate",
}
ALWAYS_LOW_ACTIONS = {
    "emergency_stop", "read", "status_check", "log_only", "notification",
    "observe",
}


def apply_risk_override(action_type: ActionType, risk_level: RiskLevel) -> RiskLevel:
    """Apply fixed risk overrides for safety-critical actions.
    
    Per documentation §4.1.3:
    - Destructive/privileged actions → always HIGH (require HITL)
    - Safety emergency / read-only → always LOW (allow autonomous)
    """
    action_name = action_type.value.lower()
    if action_name in ALWAYS_HIGH_ACTIONS:
        return RiskLevel.HIGH
    if action_name in ALWAYS_LOW_ACTIONS:
        return RiskLevel.LOW
    return risk_level


def determine_workflow(decision_band: DecisionBand, risk_level: RiskLevel, action_type: ActionType | None = None) -> str:
    """Determine which Cloud Workflow to execute."""
    if action_type == ActionType.OBSERVE:
        return "ObserveWorkflow"
    if decision_band == DecisionBand.BLOCKED:
        return "BlockedActionWorkflow"
    elif risk_level == RiskLevel.HIGH:
        return "HumanApprovalWorkflow"
    elif decision_band == DecisionBand.EXECUTE:
        return "AutoExecuteWorkflow"
    else:
        return "SuggestActionWorkflow"


def determine_action_type(state: JudgeState) -> ActionType:
    """Determine the type of action based on severity, risk, and decision band.
    
    Fallback-by-severity (doc §5.3.2):
    - CRITICAL + BLOCKED/ALERT → SHUTDOWN (imediato)
    - HIGH + any → MAINTENANCE (escalonar para humano)
    - MEDIUM → TICKET (registrar para análise)
    - LOW → NOTIFICATION (apenas notificar)
    
    Special overrides:
    - VETO risk → always NOTIFICATION (cannot act)
    - EXECUTE band + LOW risk → READ (observação)
    - LLM suggestion used when available and within allowed actions
    """
    severity = state["trigger"].severity
    risk_level = state.get("risk_level", RiskLevel.MEDIUM)
    decision_band = state.get("decision_band", DecisionBand.BLOCKED)
    
    # Operational root cause → OBSERVE (wait and re-check).
    # This takes precedence over VETO because OBSERVE is inherently
    # safe — it schedules a re-evaluation instead of acting.
    if state.get("operational_root_cause"):
        return ActionType.OBSERVE
    
    # VETO: system cannot act, only notify
    if risk_level == RiskLevel.VETO:
        return ActionType.NOTIFICATION
    
    # EXECUTE + LOW: just observe
    if decision_band == DecisionBand.EXECUTE and risk_level == RiskLevel.LOW:
        return ActionType.READ
    
    # --- Fix #1: Consider LLM-suggested action ---
    llm_analysis = state.get("llm_analysis")
    if llm_analysis and llm_analysis.get("action"):
        llm_action_str = llm_analysis["action"].strip().lower()
        # Map LLM action string → ActionType enum
        llm_action_map = {
            "shutdown": ActionType.SHUTDOWN,
            "desligamento": ActionType.SHUTDOWN,
            "desligar": ActionType.SHUTDOWN,
            "setpoint": ActionType.SETPOINT,
            "ajuste": ActionType.SETPOINT,
            "notification": ActionType.NOTIFICATION,
            "notificação": ActionType.NOTIFICATION,
            "notificar": ActionType.NOTIFICATION,
            "ticket": ActionType.TICKET,
            "maintenance": ActionType.MAINTENANCE,
            "manutenção": ActionType.MAINTENANCE,
            "restart": ActionType.SETPOINT,
            "reiniciar": ActionType.SETPOINT,
            "nenhuma": ActionType.READ,
            "none": ActionType.READ,
            "observe": ActionType.OBSERVE,
            "observar": ActionType.OBSERVE,
            "aguardar": ActionType.OBSERVE,
            "monitorar": ActionType.OBSERVE,
            "reavaliar": ActionType.OBSERVE,
        }
        llm_action = llm_action_map.get(llm_action_str)
        
        if llm_action is not None:
            # Skip allowed_actions validation for inherently safe actions
            safe_action = llm_action.value.lower() in ALWAYS_LOW_ACTIONS
            
            # Validate against Atlas allowed_actions (only for risky actions)
            atlas = state.get("atlas_context") or {}
            allowed = atlas.get("allowed_actions", [])
            if safe_action or not allowed or llm_action.value in allowed:
                # Safety check: LLM cannot recommend SHUTDOWN for LOW severity
                if llm_action == ActionType.SHUTDOWN and severity.value == "LOW":
                    llm_action = ActionType.SETPOINT
                return llm_action
    
    # Severity-based fallback (original logic)
    if severity.value == "CRITICAL":
        if decision_band in (DecisionBand.BLOCKED, DecisionBand.ALERT):
            return ActionType.SHUTDOWN
        return ActionType.MAINTENANCE
    
    if severity.value == "HIGH":
        return ActionType.MAINTENANCE
    
    if severity.value == "MEDIUM":
        return ActionType.TICKET
    
    # LOW severity
    return ActionType.NOTIFICATION


async def act_node(state: JudgeState) -> dict[str, Any]:
    """Act Node: Prepare and Dispatch Action.
    
    Finalizes the decision and creates the ActionSchema
    to be sent to CARE via Cloud Workflows.
    
    Returns:
        Updated state with proposed_action
    """
    # Short-circuit if sense_node already blocked this request
    if state.get("sense_blocked"):
        action = ActionSchema(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            workflow_name="BlockedActionWorkflow",
            created_at=datetime.now(timezone.utc),
            verdict_score=-1.0,
            decision=DecisionBand.BLOCKED,
            asset_id=state["trigger"].context.asset_id,
            tenant_id=state["trigger"].context.tenant_id,
            command=ActionCommand(type=ActionType.NOTIFICATION, target=state["trigger"].context.asset_id, params={}),
            guardrails_passed=[],
            guardrails_violated=state.get("guardrail_violations", []),
            requires_approval=True,
            estimated_cost=0.0,
            justification=state.get("error", "Bloqueado na entrada"),
            confidence=0.0,
            reasoning_trace=state.get("reasoning_trace", []),
        )
        # Release mutex if held
        mutex = get_asset_mutex()
        mutex.release(state["trigger"].context.asset_id)
        # Release backpressure slot
        bp = get_backpressure_guard()
        bp.release()
        return {"proposed_action": action}

    logger.info(
        "act_node_start",
        event_id=state["trigger"].event_id,
        decision_band=state.get("decision_band"),
    )
    
    # Get classifications
    decision_band = state.get("decision_band", DecisionBand.BLOCKED)
    risk_level = state.get("risk_level", RiskLevel.HIGH)
    
    # Determine workflow and action type
    action_type = determine_action_type(state)
    workflow_name = determine_workflow(decision_band, risk_level, action_type)

    
    # Apply risk overrides for safety-critical actions (doc §4.1.3)
    original_risk = risk_level
    risk_level = apply_risk_override(action_type, risk_level)
    if risk_level != original_risk:
        logger.info(
            "risk_override_applied",
            action_type=action_type.value,
            original_risk=original_risk.value,
            overridden_risk=risk_level.value,
        )
        # Re-determine workflow after override
        workflow_name = determine_workflow(decision_band, risk_level, action_type)
    
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
    
    # Determine if human approval is needed
    # Actions in ALWAYS_LOW_ACTIONS are inherently safe → no approval
    requires_approval = state.get("requires_human_approval", True)
    if action_type.value.lower() in ALWAYS_LOW_ACTIONS:
        requires_approval = False
    
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
        requires_approval=requires_approval,
        estimated_cost=estimated_cost,
        justification=f"Decisão: {decision_band.value}. Risco: {risk_level.value}.",
        confidence=abs(state.get("verdict_score", 0.0)),
        reasoning_trace=state.get("reasoning_trace", []),
    )
    
    # Sync back to state so EventResponse picks up the resolved value
    state["requires_human_approval"] = action.requires_approval
    logger.info(
        "act_node_complete",
        event_id=state["trigger"].event_id,
        action_id=action.action_id,
        workflow=workflow_name,
        action_type=action_type.value,
        requires_approval=action.requires_approval,
    )
    
    # === WORM: Persist immutable verdict record (doc §9) ===
    try:
        from caos.observability.worm import get_worm_storage
        worm = get_worm_storage()
        worm.append({
            "action_id": action.action_id,
            "event_id": state["trigger"].event_id,
            "tenant_id": state["trigger"].context.tenant_id,
            "asset_id": state["trigger"].context.asset_id,
            "verdict_score": state.get("verdict_score", 0.0),
            "decision_band": decision_band.value,
            "risk_level": risk_level.value,
            "action_type": action_type.value,
            "justification": action.justification,
            "guardrails_passed": action.guardrails_passed,
            "guardrails_violated": action.guardrails_violated,
            "requires_approval": action.requires_approval,
            "reasoning_trace": state.get("reasoning_trace", []),
            "severity": state["trigger"].severity.value,
            "is_fast_track": state.get("is_fast_track", False),
        })
    except Exception as e:
        logger.error("worm_append_error", action_id=action.action_id, error=str(e))
    
    # === RLHF: Experience already recorded by oracle_node with accurate risk data.
    # act_node only logs the final action outcome for traceability (no duplicate recording). ===
    logger.debug(
        "rlhf_experience_skip_act",
        action_id=action.action_id,
        reason="oracle_node already recorded with correct risk dimensions",
    )
    
    # === OBSERVE: Schedule re-evaluation instead of acting (doc §OBSERVE) ===
    if action_type == ActionType.OBSERVE:
        try:
            from caos.core.observer import get_observer, ObservationTask
            observer = get_observer()
            obs_task = ObservationTask(
                observation_id=f"obs_{uuid.uuid4().hex[:8]}",
                event_id=state["trigger"].event_id,
                asset_id=state["trigger"].context.asset_id,
                tenant_id=state["trigger"].context.tenant_id,
                metric=state["trigger"].metric or "temperature",
                original_value=state["trigger"].value or 0.0,
                threshold=state.get("atlas_context", {}).get("max_operating_temp"),
                delay_minutes=30,
                original_reasoning=state.get("reasoning_trace", []),
            )
            observer.schedule(obs_task)
            state.get("reasoning_trace", []).append(
                f"👁️ OBSERVE: Reavaliação agendada em {obs_task.delay_minutes}min "
                f"(obs_id: {obs_task.observation_id})"
            )
            logger.info(
                "observe_scheduled",
                action_id=action.action_id,
                observation_id=obs_task.observation_id,
                delay_minutes=obs_task.delay_minutes,
            )
        except Exception as e:
            logger.error("observe_schedule_failed", error=str(e))

    # === HITL: Submit for approval if needed (doc §8.2) ===
    elif action.requires_approval:
        submit_for_approval({
            "action_id": action.action_id,
            "event_id": state["trigger"].event_id,
            "asset_id": state["trigger"].context.asset_id,
            "tenant_id": state["trigger"].context.tenant_id,
            "decision_band": decision_band.value,
            "risk_level": risk_level.value,
            "action_type": action_type.value,
            "verdict_score": state.get("verdict_score", 0.0),
            "justification": action.justification,
            "reasoning_trace": state.get("reasoning_trace", []),
        })
        logger.info("hitl_submitted", action_id=action.action_id)
    
    result: dict[str, Any] = {
        "proposed_action": action,
        "processing_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # === SO_001: Anti-Flapping — detect decision oscillation ===
    anti_flap = get_anti_flapping()
    flap_violation = anti_flap.check(
        asset_id=state["trigger"].context.asset_id,
        decision_band=decision_band.value,
        action_type=action_type.value,
    )
    if flap_violation:
        result.setdefault("audit_issues", [])
        result["audit_issues"].append(flap_violation["description"])
        logger.warning("anti_flapping_in_act", violation=flap_violation)

    # === SO_008: Behavioral Anomaly — detect unusual action frequency ===
    anomaly = get_behavioral_anomaly()
    anomaly_violation = anomaly.record(state["trigger"].context.asset_id)
    if anomaly_violation:
        result.setdefault("audit_issues", [])
        result["audit_issues"].append(anomaly_violation["description"])
        logger.warning("behavioral_anomaly_in_act", violation=anomaly_violation)
    
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
    
    # === ROB_001: Release Mutex for asset ===
    mutex = get_asset_mutex()
    mutex.release(state["trigger"].context.asset_id)

    # === ROB_005: Release Backpressure slot ===
    bp = get_backpressure_guard()
    bp.release()

    # === COMM: Route notifications to appropriate channels ===
    if action.requires_approval or decision_band in (DecisionBand.ALERT, DecisionBand.BLOCKED):
        router = get_notification_router()
        sev = state["trigger"].severity.value if hasattr(state["trigger"].severity, 'value') else str(state["trigger"].severity)
        router.route(
            severity=sev,
            asset_id=state["trigger"].context.asset_id,
            message=action.justification or "",
            tenant_id=state["trigger"].context.tenant_id,
            action_id=action.action_id,
        )
    
    return result
