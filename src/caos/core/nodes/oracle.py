"""Oracle Node - Prediction (Optional).

Responsible for projecting future scenarios using ML models.
This node is OPTIONAL and can be bypassed in Fast-Track mode for:
- Critical alerts (latency priority)
- Low-risk actions (cost optimization)
- Read-only operations
"""

import structlog
from typing import Any

from caos.config import get_settings
from caos.integration.oracle import get_oracle_client
from caos.schemas.enums import Severity
from caos.schemas.state import JudgeState, OraclePrediction

logger = structlog.get_logger(__name__)


def should_bypass_oracle(state: JudgeState) -> bool:
    """Determine if Oracle should be bypassed.
    
    Bypass conditions:
    1. Severity is CRITICAL (latency priority — no time for prediction)
    2. Severity is LOW (cost optimization — prediction not worth it)
    3. Severity is MEDIUM but sensor value is below bypass_threshold
       of the max operating limit (low urgency, Oracle adds latency)
    """
    settings = get_settings()
    trigger = state["trigger"]
    
    # CRITICAL = bypass Oracle for faster response
    if trigger.severity == Severity.CRITICAL:
        logger.info("oracle_bypass_critical", event_id=trigger.event_id)
        return True
    
    # Check if bypass is enabled
    if not settings.oracle_bypass_enabled:
        return False
    
    # Bypass for low severity
    if trigger.severity == Severity.LOW:
        logger.info("oracle_bypass_low_severity", event_id=trigger.event_id)
        return True
    
    # For MEDIUM: bypass if value is below threshold ratio of the limit
    # e.g., threshold=0.8 means skip Oracle when value < 80% of max
    if trigger.severity == Severity.MEDIUM and trigger.value is not None:
        atlas = state.get("atlas_context") or {}
        max_temp = atlas.get("max_operating_temp")
        if max_temp and max_temp > 0:
            ratio = trigger.value / max_temp
            if ratio < settings.oracle_bypass_threshold:
                logger.info(
                    "oracle_bypass_threshold",
                    event_id=trigger.event_id,
                    ratio=round(ratio, 3),
                    threshold=settings.oracle_bypass_threshold,
                )
                return True
    
    return False


async def oracle_node(state: JudgeState) -> dict[str, Any]:
    """Oracle Node: Prediction and Simulation.
    
    Calls the Oracle agent to get:
    - Failure probability predictions
    - Financial impact estimates
    - Recommended actions
    
    Returns:
        Updated state with oracle_forecast populated
    """
    # Short-circuit if sense_node already blocked this request
    if state.get("sense_blocked"):
        return {}

    trigger = state["trigger"]
    
    logger.info(
        "oracle_node_start",
        event_id=trigger.event_id,
    )
    
    # Check if we should bypass
    if should_bypass_oracle(state):
        logger.info(
            "oracle_node_bypassed",
            event_id=trigger.event_id,
            reason="fast_track",
        )
        from caos.observability.metrics import get_metrics
        get_metrics().record_oracle(bypassed=True)
        return {
            "oracle_forecast": None,
            "is_fast_track": True,
        }
    
    # Call Oracle API
    oracle_client = get_oracle_client()
    
    try:
        oracle_forecast = await oracle_client.get_prediction(
            tenant_id=trigger.context.tenant_id,
            asset_id=trigger.context.asset_id,
            metric=trigger.metric or "failure_probability",
            current_value=trigger.value,
        )
        
        if oracle_forecast is None:
            logger.warning(
                "oracle_node_no_prediction",
                event_id=trigger.event_id,
            )
            return {
                "oracle_forecast": None,
                "is_fast_track": True,  # Fallback to fast-track
            }
        
        # === Input Validation (doc §5.2.1) ===
        # Confidence < 0.75 → ignore prediction (unreliable)
        confidence = oracle_forecast.get("confidence", 0.0)
        if confidence < 0.75:
            logger.warning(
                "oracle_low_confidence_ignored",
                event_id=trigger.event_id,
                confidence=confidence,
                threshold=0.75,
            )
            return {
                "oracle_forecast": None,
                "is_fast_track": True,
            }
        
        # Horizon > 7 days → ignore (too far out to be actionable)
        horizon_hours = oracle_forecast.get("horizon_hours", 0)
        if horizon_hours > 168:  # 7 * 24
            logger.warning(
                "oracle_horizon_too_far",
                event_id=trigger.event_id,
                horizon_hours=horizon_hours,
                max_hours=168,
            )
            return {
                "oracle_forecast": None,
                "is_fast_track": True,
            }
        
        logger.info(
            "oracle_node_complete",
            event_id=trigger.event_id,
            predicted_value=oracle_forecast.get("predicted_value"),
            confidence=oracle_forecast.get("confidence"),
        )
        
        from caos.observability.metrics import get_metrics
        get_metrics().record_oracle(bypassed=False)

        from caos.safety.runtime import get_fail_safe
        get_fail_safe().report_service_up("oracle")
        
        return {
            "oracle_forecast": oracle_forecast,
            "is_fast_track": False,
        }
    
    except Exception as e:
        logger.warning(
            "oracle_node_error",
            event_id=trigger.event_id,
            error=str(e),
        )
        from caos.safety.runtime import get_fail_safe
        get_fail_safe().report_service_down("oracle", str(e))
        return {
            "oracle_forecast": None,
            "is_fast_track": True,  # Fallback to fast-track
        }
