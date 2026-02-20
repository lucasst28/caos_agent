"""Sense Node - Data Fusion (Perception).

Responsible for consolidating alerts from Sentinel with context from Atlas.
This is the first cognitive step: understanding the current reality.
"""

import structlog
from datetime import datetime, timezone
from typing import Any

from caos.integration.atlas import get_atlas_client
from caos.integration.sentinel import get_sentinel_client
from caos.safety.runtime import (
    check_trigger_injection,
    get_anti_spam,
    get_asset_mutex,
    get_backpressure_guard,
    get_event_dedup,
    get_fail_safe,
    get_rate_limiter,
)
from caos.schemas.state import AssetContext, JudgeState

logger = structlog.get_logger(__name__)


async def sense_node(state: JudgeState) -> dict[str, Any]:
    """Sense Node: Data Fusion.
    
    Combines:
    - Sentinel alert data (enrichment and validation)
    - Atlas context (Digital Twin, manuals, contracts)
    
    This node:
    1. Calls Sentinel to generate/enrich the alert
    2. Calls Atlas API to get asset context
    3. Merges data into unified perception
    
    Returns:
        Updated state with atlas_context populated
    """
    trigger = state["trigger"]
    asset_id = trigger.context.asset_id
    
    logger.info(
        "sense_node_start",
        event_id=trigger.event_id,
        asset_id=asset_id,
    )

    # --- Pre-checks: runtime safety guards ---
    early_violations: list[dict] = []

    # ROB_005: Backpressure — reject if too many events in-flight
    bp = get_backpressure_guard()
    bp_violation = bp.try_acquire()
    if bp_violation:
        early_violations.append(bp_violation)

    # SO_003/SO_004: Fail-Safe — check if external services are degraded
    failsafe = get_fail_safe()
    severity_val = trigger.severity.value if hasattr(trigger.severity, 'value') else str(trigger.severity)
    fs_action = failsafe.get_degraded_action(severity_val)
    if fs_action:
        early_violations.append(fs_action)

    # ROB_004: Event deduplication — skip if already processed
    dedup = get_event_dedup()
    dedup_violation = dedup.check(trigger.event_id)
    if dedup_violation:
        early_violations.append(dedup_violation)

    # SO_005: Rate limiting — reject if too many requests/min
    rate_limiter = get_rate_limiter()
    rate_violation = rate_limiter.record()
    if rate_violation:
        early_violations.append(rate_violation)

    # SO_002: Prompt injection detection on trigger fields
    injection = check_trigger_injection(trigger.model_dump())
    if injection:
        early_violations.append(injection)

    # ROB_001: Mutex — reject if another workflow is active for this asset
    mutex = get_asset_mutex()
    mutex_violation = mutex.try_acquire(asset_id)
    if mutex_violation:
        early_violations.append(mutex_violation)

    # COMM_002: Anti-spam — suppress if too many identical alerts
    spam = get_anti_spam()
    spam_violation = spam.check(
        asset_id=asset_id,
        metric=trigger.metric,
        severity=trigger.severity.value if hasattr(trigger.severity, 'value') else str(trigger.severity),
    )
    if spam_violation:
        early_violations.append(spam_violation)

    # If any early violation is BLOCKING/HIGH, short-circuit the entire pipeline
    if early_violations:
        blocking = [v for v in early_violations if v.get("severity") in ("BLOCKING", "HIGH")]
        if blocking:
            from caos.schemas.enums import DecisionBand, RiskLevel
            violation_ids = [v["id"] for v in blocking]
            logger.warning(
                "sense_node_blocked",
                event_id=trigger.event_id,
                violations=violation_ids,
            )
            # Release backpressure slot since we won't proceed
            bp.release()
            return {
                "guardrail_violations": violation_ids,
                "guardrails_checked": violation_ids,
                "verdict_score": -1.0,
                "decision_band": DecisionBand.BLOCKED,
                "risk_level": RiskLevel.VETO,
                "requires_human_approval": True,
                "reasoning_trace": [
                    f"BLOQUEADO NA ENTRADA: {', '.join(violation_ids)}",
                    *[v.get("description", "") for v in blocking],
                ],
                "sense_blocked": True,
                "recycle_count": 99,  # Prevent recycling for blocked requests
                "error": f"Bloqueado na entrada: {violation_ids}",
                "processing_started_at": datetime.now(timezone.utc).isoformat(),
            }
    
    # --- Step 1: Call Sentinel for alert enrichment ---
    sentinel_client = get_sentinel_client()
    sentinel_alert = None
    try:
        sentinel_alert = await sentinel_client.generate_alert(
            asset_id=trigger.context.asset_id,
            metric=trigger.metric or "temperature",
            severity=trigger.severity.value if hasattr(trigger.severity, 'value') else str(trigger.severity),
            value=trigger.value,
            tenant_id=trigger.context.tenant_id,
        )
        logger.info(
            "sense_node_sentinel_enriched",
            event_id=trigger.event_id,
            alert_id=sentinel_alert.get("alert_id") if sentinel_alert else None,
        )
    except Exception as e:
        logger.warning(
            "sense_node_sentinel_fallback",
            event_id=trigger.event_id,
            error=str(e),
        )
    
    # --- Step 2: Call Atlas to get context ---
    atlas_client = get_atlas_client()
    
    try:
        atlas_context = await atlas_client.get_asset_context(
            tenant_id=trigger.context.tenant_id,
            asset_id=trigger.context.asset_id,
        )
        failsafe.report_service_up("atlas")
    except Exception as e:
        failsafe.report_service_down("atlas", str(e))
        logger.exception(
            "sense_node_atlas_fallback",
            event_id=trigger.event_id,
        )
        # Fallback to minimal context
        atlas_context: AssetContext = {
            "current_state": {"sensor_value": trigger.value},
            "under_maintenance": False,
            "maintenance_window_active": False,
            "contract_tier": "PREMIUM",
            "allowed_actions": ["shutdown", "notification", "ticket"],
        }

    # --- ROB_003: LOTO (Lockout/Tagout) — block ALL actions if under maintenance ---
    if atlas_context.get("under_maintenance"):
        from caos.schemas.enums import DecisionBand, RiskLevel
        logger.warning(
            "sense_loto_block",
            event_id=trigger.event_id,
            asset_id=asset_id,
        )
        # Release mutex since we're aborting
        mutex = get_asset_mutex()
        mutex.release(asset_id)
        # Release backpressure slot
        bp.release()
        return {
            "atlas_context": atlas_context,
            "sentinel_alert": sentinel_alert,
            "guardrail_violations": ["ROB_003"],
            "guardrails_checked": ["ROB_003"],
            "verdict_score": -1.0,
            "decision_band": DecisionBand.BLOCKED,
            "risk_level": RiskLevel.VETO,
            "requires_human_approval": True,
            "reasoning_trace": [
                "BLOQUEADO: LOTO ativo — ativo em manutenção (ROB_003)",
                "Todas as ações automáticas suspensas durante manutenção física.",
            ],
            "sense_blocked": True,
            "recycle_count": 99,
            "error": "LOTO: ativo em manutenção",
            "processing_started_at": datetime.now(timezone.utc).isoformat(),
        }
    
    logger.info(
        "sense_node_complete",
        event_id=trigger.event_id,
        contract_tier=atlas_context.get("contract_tier"),
        sentinel_enriched=sentinel_alert is not None,
    )
    
    return {
        "atlas_context": atlas_context,
        "sentinel_alert": sentinel_alert,
        "processing_started_at": datetime.now(timezone.utc).isoformat(),
    }
