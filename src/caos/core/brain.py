"""Brain Module - LangGraph StateGraph Orchestration.

This is the cognitive orchestrator that coordinates all nodes
in the CAOS decision loop.

Architecture:
    - Oracle: Full reasoning engine (LLM + CODE risk + verdict)
    - CAOS Verifier (cortex): LLM Auditor + Checklist — audits Oracle's decision
    - Guardrails: Safety validation (3-layer defense)

Flow:
    Trigger → Sense → Oracle → CAOS Verifier ─┬─ (APPROVED) → Guardrails → Act
                ↑                               │
                ├── (REJECTED + feedback) ──────┘
                ↑                              
                └──────── Recycle (if guardrail veto) ───┘
    
    Two feedback loops:
    1. CAOS → Oracle: When CAOS rejects (max 1 retry)
    2. Guardrails → Oracle: When guardrails veto (max 1 recycle)
"""

import structlog
from typing import Literal

from langgraph.graph import END, StateGraph

from caos.core.nodes.sense import sense_node
from caos.core.nodes.oracle import oracle_node
from caos.core.nodes.cortex import cortex_node  # CAOS Verifier
from caos.core.nodes.guardrails import guardrails_node
from caos.core.nodes.act import act_node
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


def should_recycle(state: JudgeState) -> Literal["oracle", "act"]:
    """Conditional edge: decide if we need to re-plan after guardrail check.
    
    If guardrails vetoed the action AND we haven't recycled yet,
    return to Oracle for re-planning with tighter constraints.
    
    Safety caps:
    - Max 1 recycle to prevent livelock
    - Global pipeline pass cap: verifier_retry + recycle ≤ 2 total Oracle calls
      (prevents compound loops: retry → verifier → guardrail veto → recycle → ...)
    """
    recycle_count = state.get("recycle_count", 0)
    verifier_retry_count = state.get("verifier_retry_count", 0)
    violations = state.get("guardrail_violations", [])
    risk_level = state.get("risk_level")
    
    # Never recycle if sense_node already blocked
    if state.get("sense_blocked"):
        return "act"
    
    # Global pipeline cap: total Oracle invocations ≤ 2
    total_oracle_passes = verifier_retry_count + recycle_count + 1  # +1 for initial
    if total_oracle_passes >= 2:
        logger.warning(
            "brain_global_pass_cap",
            total_oracle_passes=total_oracle_passes,
            verifier_retry_count=verifier_retry_count,
            recycle_count=recycle_count,
        )
        return "act"
    
    # Hard safety cap — never recycle more than once
    if recycle_count > 1:
        logger.warning(
            "brain_recycle_cap_reached",
            recycle_count=recycle_count,
        )
        return "act"
    
    # Recycle if: vetoed + first attempt + violations exist
    if (violations
            and recycle_count < 1
            and risk_level is not None
            and risk_level.value == "VETO"):
        logger.info(
            "brain_recycle",
            recycle_count=recycle_count,
            violations=violations,
        )
        return "oracle"
    
    return "act"


def should_retry_after_verifier(state: JudgeState) -> Literal["oracle", "guardrails"]:
    """Conditional edge: decide if CAOS rejection should route back to Oracle.
    
    When the CAOS Verifier rejects the Oracle's decision:
    - If this is the first attempt (verifier_retry_count < 1): route to Oracle with feedback
    - If already retried: proceed to Guardrails (accept current decision)
    
    Max 1 retry to prevent infinite loops between CAOS and Oracle.
    """
    # Never retry if blocked
    if state.get("sense_blocked"):
        return "guardrails"
    
    verification_passed = state.get("verification_passed", True)
    verifier_retry_count = state.get("verifier_retry_count", 0)
    verifier_feedback = state.get("verifier_feedback")
    
    if (not verification_passed 
            and verifier_retry_count < 1
            and verifier_feedback):
        logger.info(
            "brain_caos_retry",
            verifier_retry_count=verifier_retry_count,
            feedback_length=len(verifier_feedback) if verifier_feedback else 0,
        )
        return "oracle"
    
    return "guardrails"


def create_brain() -> StateGraph:
    """Create the CAOS Brain StateGraph.
    
    The graph represents the cognitive flow:
    
    ```
       ┌─────────┐
       │ trigger │
       └────┬────┘
            ▼
       ┌─────────┐
       │  sense  │  ← Data Fusion (Atlas + Sentinel)
       └────┬────┘
            ▼
       ┌─────────────────┐
       │     oracle      │  ← Full Reasoning (LLM + CODE + Verdict)
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │    verifier     │  ← CAOS Checklist Audit
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │   guardrails    │  ← Safety Validation (3-layer)
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │       act       │  ← Dispatch to CARE
       └────────┬────────┘
                ▼
            ┌───────┐
            │  END  │
            └───────┘
    ```
    
    Returns:
        Compiled StateGraph ready for execution
    """
    # Create the graph with JudgeState
    workflow = StateGraph(JudgeState)
    
    # Add all nodes
    workflow.add_node("sense", sense_node)
    workflow.add_node("oracle", oracle_node)          # Reasoning engine
    workflow.add_node("verifier", cortex_node)        # CAOS Verifier (LLM + checklist)
    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node("act", act_node)
    
    # Set entry point
    workflow.set_entry_point("sense")
    
    # Add edges — Oracle ALWAYS runs (no conditional bypass)
    workflow.add_edge("sense", "oracle")
    
    # Oracle → Verifier
    workflow.add_edge("oracle", "verifier")
    
    # Verifier → Guardrails OR back to Oracle (if CAOS rejected)
    workflow.add_conditional_edges(
        "verifier",
        should_retry_after_verifier,
        {
            "oracle": "oracle",       # CAOS rejected → retry Oracle with feedback
            "guardrails": "guardrails",  # CAOS approved → proceed to guardrails
        },
    )
    
    # Guardrails → Act (or recycle to Oracle if vetoed)
    workflow.add_conditional_edges(
        "guardrails",
        should_recycle,
        {
            "oracle": "oracle",    # Recycle through Oracle → Verifier again
            "act": "act",
        },
    )
    
    # Act → END
    workflow.add_edge("act", END)
    
    logger.info("brain_created", nodes=["sense", "oracle", "verifier", "guardrails", "act"])
    
    return workflow.compile()


def get_brain() -> StateGraph:
    """Get or create the CAOS Brain (via registry)."""
    from caos._registry import get, put
    brain = get("brain")
    if brain is None:
        brain = create_brain()
        put("brain", brain)
    return brain


async def process_trigger(trigger_data: dict) -> JudgeState:
    """Process a trigger through the brain.
    
    This is the main entry point for processing events.
    
    Args:
        trigger_data: The trigger payload as a dict
        
    Returns:
        The final JudgeState with all decisions made
    """
    import time
    from caos.observability.metrics import get_metrics
    from caos.schemas.trigger import TriggerPayload
    
    metrics = get_metrics()
    t0 = time.perf_counter()
    
    # Parse trigger
    trigger = TriggerPayload.model_validate(trigger_data)
    
    # Create initial state
    initial_state: JudgeState = {
        "trigger": trigger,
    }
    
    logger.info(
        "brain_processing_start",
        event_id=trigger.event_id,
        source=trigger.source.value,
        severity=trigger.severity.value,
    )
    
    try:
        # Run the brain with SO_007 execution timeout (5 min hard cap)
        import asyncio
        brain = get_brain()
        try:
            final_state = await asyncio.wait_for(
                brain.ainvoke(initial_state),
                timeout=300.0,  # 5 minutes
            )
        except asyncio.TimeoutError:
            logger.error(
                "brain_execution_timeout",
                event_id=trigger.event_id,
                timeout_seconds=300,
            )
            metrics.record_error()
            # Build a minimal BLOCKED state
            from caos.schemas.enums import DecisionBand, RiskLevel
            # Release backpressure slot on timeout
            from caos.safety.runtime import get_backpressure_guard, get_asset_mutex
            get_backpressure_guard().release()
            get_asset_mutex().release(trigger.context.asset_id)
            final_state = {
                **initial_state,
                "verdict_score": -1.0,
                "decision_band": DecisionBand.BLOCKED,
                "risk_level": RiskLevel.VETO,
                "guardrail_violations": ["SO_007"],
                "reasoning_trace": ["TIMEOUT: Pipeline excedeu 5 minutos (SO_007)"],
                "error": "Execution timeout (SO_007)",
                "sense_blocked": True,
            }
    except Exception:
        metrics.record_error()
        # H7+H8: Release backpressure slot and mutex to prevent resource leaks
        # on crashes between sense_node (acquire) and act_node (release)
        try:
            from caos.safety.runtime import get_backpressure_guard, get_asset_mutex
            get_backpressure_guard().release()
            get_asset_mutex().release(trigger.context.asset_id)
        except Exception:
            pass  # Best-effort cleanup
        raise
    
    action = final_state.get("proposed_action")
    decision_band = final_state.get("decision_band")
    decision_str = decision_band.value if hasattr(decision_band, "value") else str(decision_band or "UNKNOWN")
    
    # Record metrics
    latency_ms = (time.perf_counter() - t0) * 1000
    metrics.record_request(latency_ms=latency_ms, decision=decision_str)
    
    logger.info(
        "brain_processing_complete",
        event_id=trigger.event_id,
        verdict=final_state.get("verdict_score"),
        decision=decision_str,
        action_id=action.action_id if action else None,
        latency_ms=round(latency_ms, 2),
    )
    
    return final_state
