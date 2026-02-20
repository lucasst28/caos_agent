"""Guardrails Node - Safety Validation.

The "immune system" that validates proposed actions against safety rules.
This is the last line of defense before execution.

Guardrails are checked at 3 moments (Defense in Depth):
1. In the Verdict formula (penalty in denominator)
2. In the LLM context (instructions)
3. Here: Post-check before execution
"""

import structlog
from typing import Any

from caos.safety.engine import GuardrailEngine
from caos.safety.layers import get_reflex_layer, get_audit_layer
from caos.schemas.enums import DecisionBand, GuardrailAction, RiskLevel
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)

def get_guardrail_engine() -> GuardrailEngine:
    """Get the guardrail engine singleton (via registry)."""
    from caos._registry import get, put
    engine = get("guardrail_engine")
    if engine is None:
        engine = GuardrailEngine()
        put("guardrail_engine", engine)
    return engine


async def guardrails_node(state: JudgeState) -> dict[str, Any]:
    """Guardrails Node: Safety Validation.
    
    Executes a 3-layer defense:
    1. Layer 0 - Reflex: Instant hard-coded checks (< 10ms)
    2. Layer 1 - Full policy check via GuardrailEngine
    3. (Later) Layer 2 - Audit will be called after Act node
    
    If any BLOCKING guardrail is violated:
    - Verdict is forced to -1.0 (VETO)
    - Risk level becomes VETO
    
    Returns:
        Updated state with guardrail results
    """
    # Short-circuit if sense_node already blocked this request
    if state.get("sense_blocked"):
        return {}

    logger.info(
        "guardrails_node_start",
        event_id=state["trigger"].event_id,
        current_verdict=state.get("verdict_score"),
    )
    
    # === Layer 0: Reflex (instant checks) ===
    reflex = get_reflex_layer()
    reflex_result = reflex.check(state)
    
    if reflex_result.triggered:
        logger.warning(
            "guardrails_reflex_triggered",
            event_id=state["trigger"].event_id,
            rule_id=reflex_result.rule_id,
            latency_ms=reflex_result.latency_ms,
        )
        from caos.observability.metrics import get_metrics
        get_metrics().record_guardrail(reflex_result.rule_id)

        # Escalation path: if LLM already suggested a coherent non-destructive
        # action, escalate to HIGH (requires approval) instead of VETO (blocks
        # everything). This preserves the LLM's nuanced reasoning.
        llm_analysis = state.get("llm_analysis")
        llm_action = (llm_analysis.get("action", "") if llm_analysis else "").strip().lower()
        destructive_actions = {"shutdown", "desligamento", "desligar", ""}
        if llm_action and llm_action not in destructive_actions:
            logger.info(
                "guardrails_reflex_escalated",
                event_id=state["trigger"].event_id,
                rule_id=reflex_result.rule_id,
                llm_action=llm_action,
                escalation="HIGH (was VETO)",
            )
            return {
                "guardrail_violations": [reflex_result.rule_id],
                "guardrails_checked": [reflex_result.rule_id],
                "risk_level": RiskLevel.HIGH,
                "requires_human_approval": True,
                "reasoning_trace": [
                    f"⚠️ REFLEX ESCALADO: {reflex_result.message} → Escalado para HIGH (LLM sugeriu '{llm_action}')",
                    *state.get("reasoning_trace", []),
                ],
            }

        # Original VETO for cases without LLM context or destructive actions
        return {
            "guardrail_violations": [reflex_result.rule_id],
            "guardrails_checked": [reflex_result.rule_id],
            "verdict_score": -1.0,
            "decision_band": DecisionBand.BLOCKED,
            "risk_level": RiskLevel.VETO,
            "requires_human_approval": True,
            "recycle_count": state.get("recycle_count", 0) + 1,
            "reasoning_trace": [
                f"REFLEX VETO: {reflex_result.message}",
                *state.get("reasoning_trace", []),
            ],
        }
    
    # === Layer 1: Full policy check ===
    engine = get_guardrail_engine()
    check_result = engine.check_all(state)
    
    violations = [v.rule_id for v in check_result.violations]
    warnings = [w.rule_id for w in check_result.warnings]
    
    # Record guardrail violation metrics
    if violations:
        from caos.observability.metrics import get_metrics
        metrics = get_metrics()
        for rule_id in violations:
            metrics.record_guardrail(rule_id)
    
    result: dict[str, Any] = {
        "guardrail_violations": violations,
        "guardrails_checked": check_result.checked,
        "recycle_count": state.get("recycle_count", 0) + (1 if not check_result.passed else 0),
        # Fix #4: Store actual penalty for downstream use
        "guardrail_penalty": check_result.penalty,
    }
    
    if not check_result.passed:
        # VETO - force block
        result["verdict_score"] = -1.0
        result["decision_band"] = DecisionBand.BLOCKED
        result["risk_level"] = RiskLevel.VETO
        result["requires_human_approval"] = True
        
        violation_messages = [v.message for v in check_result.violations if v.message]
        result["reasoning_trace"] = [
            f"GUARDRAIL VETO: {', '.join(violations)}",
            *violation_messages[:3],  # Top 3 messages
            *state.get("reasoning_trace", []),
        ]
        
        logger.warning(
            "guardrails_node_veto",
            event_id=state["trigger"].event_id,
            violations=violations,
            penalty=check_result.penalty,
        )
    elif check_result.final_action == GuardrailAction.REQUIRE_APPROVAL:
        # Require approval but don't veto
        result["requires_human_approval"] = True
        
        # --- Fix #5: Recalculate V with guardrail penalty ---
        if check_result.penalty > 0 and state.get("verdict_score") is not None:
            old_v = state["verdict_score"]
            # V_new = V_old / (1 + penalty)
            new_v = old_v / (1 + check_result.penalty)
            result["verdict_score"] = new_v
            # Re-classify decision band with penalized verdict
            from caos.core.judge import JudgeEngine
            judge = JudgeEngine.from_rlhf()
            new_band = judge.classify_decision_band(new_v)
            rd = state.get("risk_dimensions")
            if rd is not None:
                new_risk = judge.classify_risk_level(new_v, rd)
            else:
                new_risk = state.get("risk_level", RiskLevel.HIGH)
            result["decision_band"] = new_band
            result["risk_level"] = new_risk
            
            trace = state.get("reasoning_trace", [])
            trace.append(
                f"⛑️ Guardrail penalty: {check_result.penalty:.3f} → "
                f"V ajustado: {old_v:.3f} → {new_v:.3f} "
                f"({', '.join(violations)})"
            )
            result["reasoning_trace"] = trace
        
        logger.info(
            "guardrails_node_approval_required",
            event_id=state["trigger"].event_id,
            violations=violations,
            warnings=warnings,
            penalty=check_result.penalty,
        )
    else:
        # Passed or only warnings — still apply penalty if any
        risk_level = state.get("risk_level", RiskLevel.MEDIUM)
        result["requires_human_approval"] = risk_level in [RiskLevel.HIGH, RiskLevel.VETO]
        
        if warnings:
            logger.info(
                "guardrails_node_warnings",
                event_id=state["trigger"].event_id,
                warnings=warnings,
            )
        else:
            logger.info(
                "guardrails_node_pass",
                event_id=state["trigger"].event_id,
                checked_count=len(check_result.checked),
            )
    
    return result
