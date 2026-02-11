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
        return {
            "guardrail_violations": [reflex_result.rule_id],
            "guardrails_checked": [reflex_result.rule_id],
            "verdict_score": -1.0,
            "decision_band": DecisionBand.BLOCKED,
            "risk_level": RiskLevel.VETO,
            "requires_human_approval": True,
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
        
        logger.info(
            "guardrails_node_approval_required",
            event_id=state["trigger"].event_id,
            violations=violations,
            warnings=warnings,
        )
    else:
        # Passed or only warnings
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
