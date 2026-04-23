"""Tests for the Guardrails Engine and Safety Layers."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from caos.safety.engine import GuardrailEngine, GuardrailCheckResult
from caos.safety.layers import ReflexLayer, AuditLayer, ReflexResult, AuditResult
from caos.schemas.enums import (
    DecisionBand,
    GuardrailAction,
    GuardrailSeverity,
    RiskLevel,
    Severity,
    TriggerSource,
)
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerContext, TriggerPayload


def make_trigger(
    severity: Severity = Severity.MEDIUM,
    value: float | None = None,
    source: TriggerSource = TriggerSource.SENTINEL,
) -> TriggerPayload:
    """Create a test trigger."""
    return TriggerPayload(
        event_id="evt_test123",
        source=source,
        timestamp=datetime.now(timezone.utc),
        severity=severity,
        metric="temperature",  # Added for reflex check
        payload={"temp": value} if value else {},
        context=TriggerContext(tenant_id="test_tenant", asset_id="CHILLER-01"),
        value=value,
    )


def make_state(
    severity: Severity = Severity.MEDIUM,
    value: float | None = None,
    under_maintenance: bool = False,
    contract_tier: str = "PREMIUM",
) -> JudgeState:
    """Create a test state."""
    return JudgeState(
        trigger=make_trigger(severity=severity, value=value),
        atlas_context={
            "under_maintenance": under_maintenance,
            "maintenance_window_active": False,
            "contract_tier": contract_tier,
            "max_operating_temp": 90.0,
            "allowed_actions": ["shutdown", "notification"],
        },
    )


class TestReflexLayer:
    """Test Layer 0 - Reflex checks."""

    def setup_method(self):
        """Setup test fixtures."""
        self.reflex = ReflexLayer()

    def test_reflex_passes_normal(self):
        """Reflex should pass for normal conditions."""
        state = make_state(severity=Severity.MEDIUM, value=60.0)
        result = self.reflex.check(state)
        
        assert not result.triggered
        assert result.latency_ms < 10  # Should be fast

    def test_reflex_triggers_absolute_temp(self):
        """Reflex should trigger for extreme temperature."""
        state = make_state(severity=Severity.CRITICAL, value=115.0)
        result = self.reflex.check(state)
        
        assert result.triggered
        assert result.rule_id == "PHYS_008"
        assert result.action == GuardrailAction.VETO

    def test_reflex_triggers_maintenance_critical(self):
        """Reflex should trigger for critical alert during maintenance."""
        state = make_state(
            severity=Severity.CRITICAL,
            value=50.0,
            under_maintenance=True,
        )
        result = self.reflex.check(state)
        
        assert result.triggered
        assert result.rule_id == "PHYS_001"


class TestAuditLayer:
    """Test Layer 2 - Audit checks."""

    def setup_method(self):
        """Setup test fixtures."""
        self.audit = AuditLayer()

    def test_audit_no_action(self):
        """Audit should fail with no proposed action."""
        state = make_state()
        result = self.audit.check(state)
        
        assert not result.approved
        assert "No proposed action" in str(result.issues)

    def test_audit_passes_valid_action(self):
        """Audit should pass for valid action."""
        state = make_state()
        state["proposed_action"] = MagicMock(
            action_id="act_test123",
            decision=DecisionBand.EXECUTE,
            requires_approval=False,
            justification="Valid justification for this action.",
            verdict_score=0.8,
        )
        state["risk_level"] = RiskLevel.LOW
        
        result = self.audit.check(state)
        assert result.approved
        assert result.issues is None


class TestGuardrailEngine:
    """Test the full Guardrail Engine."""

    def test_engine_loads_rules(self):
        """Engine should load rules from JSON."""
        engine = GuardrailEngine()
        
        # Updated to 41 rules
        assert len(engine.rules) == 41
        assert len(engine.categories) == 6

    def test_engine_has_all_categories(self):
        """Engine should have all 6 categories."""
        engine = GuardrailEngine()
        
        categories = set(r.category for r in engine.rules)
        assert "PHYSICAL" in categories
        assert "FINANCIAL" in categories
        assert "CONTRACTUAL" in categories
        assert "COMMUNICATION" in categories
        assert "SECOPS" in categories
        assert "ROBUSTNESS" in categories

    def test_engine_check_maintenance(self):
        """Engine should detect maintenance violation."""
        engine = GuardrailEngine()
        state = make_state(under_maintenance=True)
        
        result = engine.check_all(state, categories=["PHYSICAL"])
        
        # Should find PHYS_001 violation
        violation_ids = [v.rule_id for v in result.violations]
        assert "PHYS_001" in violation_ids

    def test_engine_check_monitoring_only(self):
        """Engine should detect MONITORING_ONLY contract."""
        engine = GuardrailEngine()
        state = make_state(contract_tier="MONITORING_ONLY")
        
        result = engine.check_all(state, categories=["CONTRACTUAL"])
        
        violation_ids = [v.rule_id for v in result.violations]
        assert "CONTR_001" in violation_ids

    def test_engine_penalty_calculation(self):
        """Engine should calculate penalties correctly."""
        engine = GuardrailEngine()
        
        # Multiple violations should sum penalties
        state = make_state(under_maintenance=True, contract_tier="MONITORING_ONLY")
        result = engine.check_all(state)
        
        assert result.penalty > 0
        assert not result.passed  # Has blocking violations

    def test_engine_get_reflex_rules(self):
        """Engine should return reflex (blocking) rules."""
        engine = GuardrailEngine()
        reflex_rules = engine.get_reflex_rules()
        
        assert len(reflex_rules) > 0
        for rule in reflex_rules:
            assert rule.severity == GuardrailSeverity.BLOCKING

    def test_engine_get_rules_by_category(self):
        """Engine should filter rules by category."""
        engine = GuardrailEngine()
        physical_rules = engine.get_rules_by_category("PHYSICAL")
        
        # Updated to 11 physical rules
        assert len(physical_rules) == 11
        for rule in physical_rules:
            assert rule.category == "PHYSICAL"
