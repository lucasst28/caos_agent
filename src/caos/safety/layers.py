"""Safety Layers - Defense in Depth implementation.

Three layers of safety checks:

Layer 0 - Reflex (< 10ms):
    Hard-coded rules that bypass LLM.
    For emergencies requiring instant response.
    
Layer 1 - Cognitive (~2s):
    LLM-enhanced validation with context.
    Handled by the Cortex node.
    
Layer 2 - Audit (< 50ms):
    Post-check validation before execution.
    Final gateway before action dispatch.
"""

import structlog
from dataclasses import dataclass
from datetime import datetime, timezone

from caos.schemas.enums import DecisionBand, GuardrailAction, RiskLevel, Severity
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


@dataclass
class ReflexResult:
    """Result of Layer 0 reflex check."""

    triggered: bool
    rule_id: str | None = None
    action: GuardrailAction | None = None
    message: str | None = None
    latency_ms: float = 0.0


@dataclass
class AuditResult:
    """Result of Layer 2 audit check."""

    approved: bool
    issues: list[str] | None = None
    latency_ms: float = 0.0


class ReflexLayer:
    """Layer 0 - Reflex: Instant hard-coded safety checks.
    
    These checks bypass the LLM entirely and must complete in < 10ms.
    They are the first line of defense for critical situations.
    """

    # Métricas que representam temperatura (para diferenciar de GPS, pressão, etc.)
    TEMPERATURE_METRICS = {
        "temperature", "cabinet_temperature", "temp", "motor_temp",
        "coolant_temp", "ambient_temp", "exhaust_temp",
    }

    # Métricas de deslocamento GPS
    GPS_METRICS = {
        "gps_displacement", "displacement", "gps_distance", "location_change",
    }

    def _is_temperature_metric(self, metric: str | None) -> bool:
        """Check if the metric represents a temperature measurement."""
        if not metric:
            return False
        return metric.lower() in self.TEMPERATURE_METRICS

    def _is_gps_metric(self, metric: str | None) -> bool:
        """Check if the metric represents GPS displacement."""
        if not metric:
            return False
        return metric.lower() in self.GPS_METRICS

    def check(self, state: JudgeState) -> ReflexResult:
        """Execute reflex checks - must be fast!"""
        start = datetime.now(timezone.utc)
        trigger = state.get("trigger")
        
        if not trigger:
            return ReflexResult(triggered=False, latency_ms=0.0)
        
        value = trigger.value
        metric = trigger.metric

        # Check 1: Absolute temperature limit (PHYS_008)
        # Dynamic: limit = atlas.max_operating_temp * 1.1 (10% buffer) OR 110.0 default
        atlas = state.get("atlas_context") or {}
        max_temp = atlas.get("max_operating_temp")
        limit = float(max_temp) * 1.1 if max_temp else 110.0

        if (value is not None and value > limit
                and self._is_temperature_metric(metric)):
            latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            # Context-aware: planned defrost with relocated products
            # In this case, the temperature exceedance is EXPECTED and products are safe.
            # Let the LLM evaluate the nuance (e.g., stuck heater relay).
            telemetry = atlas.get("telemetry_extension") or atlas.get("current_state") or {}
            defrost_active = telemetry.get("defrost_mode_active", False)
            products_safe = telemetry.get("products_relocated", False)
            if defrost_active and products_safe:
                logger.info(
                    "reflex_defrost_bypass",
                    rule_id="PHYS_008_DEFROST",
                    value=value,
                    limit=limit,
                    defrost=True,
                    products_relocated=True,
                    latency_ms=latency,
                )
                # Return non-triggered so LLM can reason about the situation
                return ReflexResult(
                    triggered=False,
                    rule_id="PHYS_008_DEFROST",
                    message=f"Temp {value}°C > limite {limit:.1f}°C, mas degelo ativo + produtos realocados. LLM avaliará.",
                    latency_ms=latency,
                )

            logger.warning(
                "reflex_triggered",
                rule_id="PHYS_008",
                value=value,
                limit=limit,
                metric=metric,
                latency_ms=latency,
            )
            return ReflexResult(
                triggered=True,
                rule_id="PHYS_008",
                action=GuardrailAction.VETO,
                message=f"Temperatura humana {value}°C excede limite de segurança ({limit:.1f}°C).",
                latency_ms=latency,
            )

        # Check 2: GPS massive displacement (> 5000m = likely theft)
        if (value is not None and self._is_gps_metric(metric)
                and value > 5000):
            latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            logger.warning(
                "reflex_triggered",
                rule_id="GPS_003",
                value=value,
                metric=metric,
                latency_ms=latency,
            )
            return ReflexResult(
                triggered=True,
                rule_id="GPS_003",
                action=GuardrailAction.VETO,
                message=f"Deslocamento GPS {value:.0f}m excede 5km. Provável furto/roubo.",
                latency_ms=latency,
            )
        
        # Check 3: Critical severity + under maintenance
        if trigger.severity == Severity.CRITICAL:
            atlas = state.get("atlas_context") or {}
            if atlas.get("under_maintenance"):
                latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                logger.warning(
                    "reflex_triggered",
                    rule_id="PHYS_001",
                    latency_ms=latency,
                )
                return ReflexResult(
                    triggered=True,
                    rule_id="PHYS_001",
                    action=GuardrailAction.VETO,
                    message="Ativo em manutenção. Ação vetada.",
                    latency_ms=latency,
                )
        
        # Check 4: Source validation (SecOps)
        if trigger.source.value not in ["sentinel", "oracle", "manual"]:
            latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            logger.warning(
                "reflex_triggered",
                rule_id="SECOPS_002",
                source=trigger.source.value,
                latency_ms=latency,
            )
            return ReflexResult(
                triggered=True,
                rule_id="SECOPS_002",
                action=GuardrailAction.VETO,
                message="Fonte do trigger não reconhecida.",
                latency_ms=latency,
            )
        
        latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return ReflexResult(triggered=False, latency_ms=latency)


class AuditLayer:
    """Layer 2 - Audit: Final validation before execution.
    
    Validates the proposed action one last time before dispatch.
    Must complete in < 50ms.
    """

    def check(self, state: JudgeState) -> AuditResult:
        """Execute audit checks on the proposed action."""
        start = datetime.now(timezone.utc)
        issues: list[str] = []
        
        action = state.get("proposed_action")
        if not action:
            return AuditResult(
                approved=False,
                issues=["No proposed action to audit"],
                latency_ms=0.0,
            )
        
        # Audit 1: Decision consistency
        if action.decision == DecisionBand.BLOCKED and not state.get("guardrail_violations"):
            issues.append("BLOCKED decision without guardrail violations")
        
        # Audit 2: Approval requirement consistency
        risk_level = state.get("risk_level", RiskLevel.MEDIUM)
        if risk_level in [RiskLevel.HIGH, RiskLevel.VETO] and not action.requires_approval:
            issues.append("High risk action without approval requirement")
        
        # Audit 3: Action ID format
        if not action.action_id.startswith("act_"):
            issues.append("Invalid action ID format")
        
        # Audit 4: Justification present
        if not action.justification or len(action.justification) < 10:
            issues.append("Missing or insufficient justification")
        
        # Audit 5: Verdict score sanity
        if action.verdict_score < -1.0 or action.verdict_score > 1.0:
            issues.append(f"Verdict score out of range: {action.verdict_score}")
        
        # Audit 6: VETO consistency
        if action.decision == DecisionBand.BLOCKED and action.verdict_score > 0:
            issues.append("BLOCKED with positive verdict - inconsistent")
        
        latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        
        if issues:
            logger.warning(
                "audit_issues_found",
                issue_count=len(issues),
                issues=issues,
                latency_ms=latency,
            )
        else:
            logger.info("audit_passed", latency_ms=latency)
        
        return AuditResult(
            approved=len(issues) == 0,
            issues=issues if issues else None,
            latency_ms=latency,
        )


def get_reflex_layer() -> ReflexLayer:
    """Get reflex layer singleton (via registry)."""
    from caos._registry import get, put
    layer = get("reflex_layer")
    if layer is None:
        layer = ReflexLayer()
        put("reflex_layer", layer)
    return layer


def get_audit_layer() -> AuditLayer:
    """Get audit layer singleton (via registry)."""
    from caos._registry import get, put
    layer = get("audit_layer")
    if layer is None:
        layer = AuditLayer()
        put("audit_layer", layer)
    return layer
