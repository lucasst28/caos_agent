"""Brain Module - LangGraph StateGraph Orchestration.

This is the cognitive orchestrator that coordinates all nodes
in the CAOS decision loop.

Flow:
    Trigger → Sense → [Oracle?] → Cortex → Guardrails → Act
                ↑                              |
                └──────── Recycle (if veto) ───┘
"""

import structlog
from typing import Literal

from langgraph.graph import END, StateGraph

from caos.core.nodes.sense import sense_node
from caos.core.nodes.oracle import oracle_node, should_bypass_oracle
from caos.core.nodes.cortex import cortex_node
from caos.core.nodes.guardrails import guardrails_node
from caos.core.nodes.act import act_node
from caos.schemas.enums import Severity
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


def should_call_oracle(state: JudgeState) -> Literal["oracle", "cortex"]:
    """Conditional edge: decide if Oracle should be called.
    
    Bypass Oracle for:
    - CRITICAL severity (latency priority)
    - LOW severity (cost optimization)
    """
    if should_bypass_oracle(state):
        return "cortex"
    return "oracle"


def should_recycle(state: JudgeState) -> Literal["cortex", "act"]:
    """Conditional edge: decide if we need to re-plan after guardrail check.
    
    If guardrails vetoed the action, we could recycle back to cortex
    for re-planning. For now, we go straight to act (which will be blocked).
    """
    # For MVP, we don't recycle - we just report the veto
    # In future, could implement re-planning logic
    return "act"


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
       ┌─────────┐    ┌─────────┐
       │ oracle? │───▶│  oracle │  ← Prediction (optional)
       └────┬────┘    └────┬────┘
            │              │
            └──────┬───────┘
                   ▼
       ┌─────────────────┐
       │     cortex      │  ← Reasoning + Verdict
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │   guardrails    │  ← Safety Validation
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
    workflow.add_node("oracle", oracle_node)
    workflow.add_node("cortex", cortex_node)
    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node("act", act_node)
    
    # Set entry point
    workflow.set_entry_point("sense")
    
    # Add edges
    # Sense → Oracle (conditional)
    workflow.add_conditional_edges(
        "sense",
        should_call_oracle,
        {
            "oracle": "oracle",
            "cortex": "cortex",
        },
    )
    
    # Oracle → Cortex
    workflow.add_edge("oracle", "cortex")
    
    # Cortex → Guardrails
    workflow.add_edge("cortex", "guardrails")
    
    # Guardrails → Act (could be conditional for recycle)
    workflow.add_conditional_edges(
        "guardrails",
        should_recycle,
        {
            "cortex": "cortex",  # Recycle (not used yet)
            "act": "act",
        },
    )
    
    # Act → END
    workflow.add_edge("act", END)
    
    logger.info("brain_created", nodes=["sense", "oracle", "cortex", "guardrails", "act"])
    
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
        # Run the brain
        brain = get_brain()
        final_state = await brain.ainvoke(initial_state)
    except Exception:
        metrics.record_error()
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
