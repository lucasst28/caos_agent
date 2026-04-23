"""Integration tests for the CAOS Brain pipeline.

Tests the full cognitive flow: Sense → Oracle → Verifier → Guardrails → Act
using mocked external services.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from caos.schemas.enums import DecisionBand, RiskLevel, Severity, TriggerSource
from caos.schemas.risk import RiskDimensions
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerContext, TriggerPayload


def make_trigger(
    severity: Severity = Severity.MEDIUM,
    value: float = 75.0,
    source: TriggerSource = TriggerSource.SENTINEL,
) -> TriggerPayload:
    """Create a test trigger."""
    return TriggerPayload(
        event_id=f"evt_int_{severity.value.lower()}",
        source=source,
        timestamp=datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc),
        severity=severity,
        payload={"temp": value},
        context=TriggerContext(tenant_id="t_test", asset_id="CHILLER-01"),
        metric="temperature",
        value=value,
        threshold_violated=90.0,
    )


MOCK_ATLAS = {
    "current_state": {"temperature": 75.0},
    "max_operating_temp": 90.0,
    "contract_tier": "PREMIUM",
    "allowed_actions": ["shutdown", "notification"],
    "under_maintenance": False,
    "maintenance_window_active": False,
}

MOCK_ORACLE = {
    "forecast_metric": "failure_probability",
    "predicted_value": 0.75,
    "confidence": 0.85,
    "horizon_hours": 24,
    "financial_impact": 5000.0,
    "recommendation": "Desligamento preventivo.",
}


class TestSenseNode:
    """Test Sense node in isolation."""

    @pytest.mark.asyncio
    async def test_sense_populates_atlas_context(self):
        """Sense should call Atlas and populate context."""
        from caos.core.nodes.sense import sense_node

        trigger = make_trigger()
        state: JudgeState = {"trigger": trigger}

        with patch("caos.core.nodes.sense.get_atlas_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_asset_context = AsyncMock(return_value=MOCK_ATLAS)
            mock_get.return_value = mock_client

            result = await sense_node(state)

        assert "atlas_context" in result
        assert result["atlas_context"]["contract_tier"] == "PREMIUM"

    @pytest.mark.asyncio
    async def test_sense_fallback_on_atlas_error(self):
        """Sense should use fallback when Atlas is unavailable."""
        from caos.core.nodes.sense import sense_node

        trigger = make_trigger()
        state: JudgeState = {"trigger": trigger}

        with patch("caos.core.nodes.sense.get_atlas_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_asset_context = AsyncMock(side_effect=Exception("Atlas down"))
            mock_get.return_value = mock_client

            result = await sense_node(state)

        assert "atlas_context" in result
        assert result["atlas_context"]["under_maintenance"] is False


class TestOracleNode:
    """Test Oracle node in isolation."""

    @pytest.mark.asyncio
    async def test_oracle_bypass_critical(self):
        """Oracle should be bypassed for CRITICAL severity."""
        from caos.core.nodes.oracle import oracle_node

        trigger = make_trigger(severity=Severity.CRITICAL, value=100.0)
        state: JudgeState = {"trigger": trigger}

        result = await oracle_node(state)

        assert result["is_fast_track"] is True
        assert result["oracle_forecast"] is None

    @pytest.mark.asyncio
    async def test_oracle_calls_api_for_medium(self):
        """Oracle should call API for MEDIUM severity."""
        from caos.core.nodes.oracle import oracle_node

        trigger = make_trigger(severity=Severity.MEDIUM)
        state: JudgeState = {"trigger": trigger}

        with patch("caos.core.nodes.oracle.get_oracle_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_prediction = AsyncMock(return_value=MOCK_ORACLE)
            mock_get.return_value = mock_client

            result = await oracle_node(state)

        assert result["is_fast_track"] is False
        assert result["oracle_forecast"]["confidence"] == 0.85


class TestVerifierNode:
    """Test CAOS Verifier node (cortex) in isolation."""

    @pytest.mark.asyncio
    async def test_verifier_audits_oracle_output(self):
        """Verifier should produce verification report from Oracle's output."""
        from caos.core.nodes.cortex import cortex_node

        trigger = make_trigger(severity=Severity.MEDIUM, value=75.0)
        state: JudgeState = {
            "trigger": trigger,
            "atlas_context": MOCK_ATLAS,
            "oracle_forecast": MOCK_ORACLE,
            "is_fast_track": False,
            "risk_dimensions": RiskDimensions(
                physical=0.3, financial=0.2, contractual=0.1, communication=0.1
            ),
            "verdict_score": 0.65,
            "decision_band": DecisionBand.SUGGEST,
            "risk_level": RiskLevel.MEDIUM,
            "llm_analysis": {
                "analysis": "Test analysis causa raiz identificada",
                "justification": "Test justification",
                "thought": "Test thought with hipótese tested and confirmed",
                "risk_justifications": {
                    "physical": "R$ physical risk",
                    "financial": "R$ 1000 potential loss",
                    "contractual": "SLA ok",
                    "communication": "Stakeholders notified",
                },
                "action": "notification",
            },
            "reasoning_trace": ["Oracle: analysis complete"],
        }

        result = await cortex_node(state)

        assert "verification_report" in result
        assert "verification_passed" in result
        assert "verification_score" in result
        assert isinstance(result["verification_score"], float)
        assert result["verification_score"] >= 0.0
        assert result["verification_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_verifier_works_without_llm_analysis(self):
        """Verifier should work in code-only mode (no llm_analysis)."""
        from caos.core.nodes.cortex import cortex_node

        trigger = make_trigger(severity=Severity.HIGH, value=95.0)
        state: JudgeState = {
            "trigger": trigger,
            "atlas_context": MOCK_ATLAS,
            "oracle_forecast": None,
            "is_fast_track": True,
            "risk_dimensions": RiskDimensions(
                physical=0.7, financial=0.5, contractual=0.2, communication=0.3
            ),
            "verdict_score": 0.35,
            "decision_band": DecisionBand.ALERT,
            "risk_level": RiskLevel.HIGH,
            "reasoning_trace": ["Oracle: fast-track mode"],
        }

        result = await cortex_node(state)

        assert "verification_report" in result
        assert "verification_passed" in result


class TestGuardrailsNode:
    """Test Guardrails node in isolation."""

    @pytest.mark.asyncio
    async def test_guardrails_pass_normal(self):
        """Guardrails should pass for normal conditions."""
        from caos.core.nodes.guardrails import guardrails_node

        trigger = make_trigger(severity=Severity.MEDIUM, value=75.0)
        state: JudgeState = {
            "trigger": trigger,
            "atlas_context": MOCK_ATLAS,
            "verdict_score": 0.6,
            "risk_level": RiskLevel.MEDIUM,
        }

        result = await guardrails_node(state)

        # Should not veto for normal temp with premium contract
        veto_violations = [v for v in result.get("guardrail_violations", []) if v.startswith("PHYS_008")]
        assert len(veto_violations) == 0

    @pytest.mark.asyncio
    async def test_guardrails_veto_maintenance(self):
        """Guardrails should veto when asset is under maintenance."""
        from caos.core.nodes.guardrails import guardrails_node

        maintenance_atlas = {**MOCK_ATLAS, "under_maintenance": True}
        trigger = make_trigger(severity=Severity.CRITICAL, value=50.0)
        state: JudgeState = {
            "trigger": trigger,
            "atlas_context": maintenance_atlas,
            "verdict_score": 0.8,
            "risk_level": RiskLevel.MEDIUM,
        }

        result = await guardrails_node(state)

        # Should detect PHYS_001 (maintenance) via reflex layer for CRITICAL
        assert result.get("verdict_score") == -1.0 or "PHYS_001" in result.get("guardrail_violations", [])


class TestActNode:
    """Test Act node in isolation."""

    @pytest.mark.asyncio
    async def test_act_creates_action(self):
        """Act should create an ActionSchema."""
        from caos.core.nodes.act import act_node

        trigger = make_trigger()
        state: JudgeState = {
            "trigger": trigger,
            "atlas_context": MOCK_ATLAS,
            "verdict_score": 0.6,
            "decision_band": DecisionBand.SUGGEST,
            "risk_level": RiskLevel.MEDIUM,
            "risk_dimensions": {"R_F": 0.3, "R_Fin": 0.2, "R_C": 0.1, "R_K": 0.1},
            "reasoning_trace": ["Test reasoning"],
            "guardrail_violations": [],
            "guardrails_checked": ["PHYS_001"],
            "requires_human_approval": False,
        }

        result = await act_node(state)

        assert "proposed_action" in result
        action = result["proposed_action"]
        assert action.action_id.startswith("act_")
        assert action.verdict_score == 0.6
