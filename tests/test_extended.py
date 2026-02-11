"""Tests for BaseClient, CareDispatcher stub, PubSub parsing, and safe_eval edge cases."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from caos.integration.base import BaseClient
from caos.safety.engine import GuardrailEngine
from caos.schemas.enums import (
    DecisionBand,
    GuardrailAction,
    RiskLevel,
    Severity,
    TriggerSource,
)
from caos.schemas.risk import RiskWeights
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerContext, TriggerPayload


# =============================================
# Helpers
# =============================================

def _make_trigger(**kw) -> TriggerPayload:
    defaults = dict(
        event_id="evt_unit",
        source=TriggerSource.SENTINEL,
        timestamp=datetime(2026, 2, 9, tzinfo=timezone.utc),
        severity=Severity.MEDIUM,
        payload={},
        context=TriggerContext(tenant_id="t", asset_id="A"),
    )
    defaults.update(kw)
    return TriggerPayload(**defaults)


def _make_state(**overrides) -> JudgeState:
    state: JudgeState = {
        "trigger": _make_trigger(),
        "atlas_context": {
            "under_maintenance": False,
            "maintenance_window_active": False,
            "contract_tier": "PREMIUM",
            "max_operating_temp": 90.0,
            "allowed_actions": ["shutdown", "notification"],
        },
    }
    state.update(overrides)
    return state


# =============================================
# BaseClient
# =============================================


class TestBaseClient:
    """Test BaseClient context manager and lifecycle."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        """BaseClient should close its httpx client on __aexit__."""
        async with BaseClient(base_url="http://localhost:9999") as client:
            # Force creation of the internal httpx client
            inner = await client._get_client()
            assert not inner.is_closed
        # After exiting, inner client should be closed
        assert inner.is_closed

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """Calling close() twice should not raise."""
        client = BaseClient(base_url="http://localhost:9999")
        await client.close()  # no-op (client never created)
        inner = await client._get_client()
        await client.close()
        await client.close()  # second close should be safe


# =============================================
# CareDispatcher (blocked / pending_approval paths)
# =============================================


class TestCareDispatcher:
    """Test CareDispatcher without Google Cloud (local paths only)."""

    @pytest.mark.asyncio
    async def test_blocked_action_not_dispatched(self):
        from caos.integration.care import CareDispatcher
        from caos.schemas.action import ActionCommand, ActionSchema

        dispatcher = CareDispatcher()
        action = ActionSchema(
            action_id="act_blocked",
            workflow_name="TestWorkflow",
            verdict_score=-0.5,
            decision=DecisionBand.BLOCKED,
            asset_id="CHILLER-01",
            tenant_id="t",
            command=ActionCommand(type="notification", target="CHILLER-01"),
            requires_approval=False,
            justification="Blocked by guardrails.",
        )

        result = await dispatcher.dispatch(action)
        assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_pending_approval_not_dispatched(self):
        from caos.integration.care import CareDispatcher
        from caos.schemas.action import ActionCommand, ActionSchema

        dispatcher = CareDispatcher()
        action = ActionSchema(
            action_id="act_pending",
            workflow_name="TestWorkflow",
            verdict_score=0.5,
            decision=DecisionBand.SUGGEST,
            asset_id="CHILLER-01",
            tenant_id="t",
            command=ActionCommand(type="notification", target="CHILLER-01"),
            requires_approval=True,
            justification="Needs human approval.",
        )

        result = await dispatcher.dispatch(action)
        assert result["status"] == "pending_approval"


# =============================================
# PubSubConsumer message parsing
# =============================================


class TestPubSubParsing:
    """Test PubSub message parsing without GCP."""

    def test_parse_valid_message(self):
        import json
        from caos.integration.pubsub import PubSubConsumer

        consumer = PubSubConsumer(subscription_id="test-sub", handler=AsyncMock())

        # Build a mock Pub/Sub message
        msg = MagicMock()
        msg.data = json.dumps({
            "event_id": "evt_ps01",
            "source": "sentinel",
            "severity": "HIGH",
            "timestamp": "2026-02-09T12:00:00+00:00",
            "payload": {"temp": 95.0},
            "context": {"tenant_id": "t_test", "asset_id": "CHILLER-01"},
            "metric": "temperature",
            "value": 95.0,
        }).encode()
        msg.attributes = {}
        msg.message_id = "msg_001"

        trigger = consumer._parse_message(msg)

        assert trigger is not None
        assert trigger.event_id == "evt_ps01"
        assert trigger.severity == Severity.HIGH

    def test_parse_invalid_message_returns_none(self):
        import json
        from caos.integration.pubsub import PubSubConsumer

        consumer = PubSubConsumer(subscription_id="test-sub", handler=AsyncMock())

        msg = MagicMock()
        msg.data = b"not valid json"
        msg.attributes = {}
        msg.message_id = "msg_bad"

        trigger = consumer._parse_message(msg)
        assert trigger is None


# =============================================
# safe_eval edge cases
# =============================================


class TestSafeEval:
    """Test the improved _safe_eval in GuardrailEngine."""

    def setup_method(self):
        self.engine = GuardrailEngine()

    def test_gte_operator(self):
        """'>=' should not conflict with '>'."""
        ctx = {"score": 0.8}
        assert self.engine._safe_eval("score >= 0.8", ctx) is True
        assert self.engine._safe_eval("score >= 0.9", ctx) is False

    def test_gt_operator(self):
        """'>' standalone works correctly."""
        ctx = {"score": 0.8}
        assert self.engine._safe_eval("score > 0.7", ctx) is True
        assert self.engine._safe_eval("score > 0.8", ctx) is False

    def test_lte_operator(self):
        """'<=' works correctly."""
        ctx = {"score": 0.3}
        assert self.engine._safe_eval("score <= 0.3", ctx) is True
        assert self.engine._safe_eval("score <= 0.2", ctx) is False

    def test_lt_operator(self):
        """'<' standalone works correctly."""
        ctx = {"score": 0.3}
        assert self.engine._safe_eval("score < 0.4", ctx) is True
        assert self.engine._safe_eval("score < 0.3", ctx) is False

    def test_equality(self):
        ctx = {"status": "active"}
        assert self.engine._safe_eval("status == 'active'", ctx) is True
        assert self.engine._safe_eval("status == 'inactive'", ctx) is False

    def test_inequality(self):
        ctx = {"status": "active"}
        assert self.engine._safe_eval("status != 'inactive'", ctx) is True
        assert self.engine._safe_eval("status != 'active'", ctx) is False

    def test_in_operator(self):
        ctx = {"tier": "PREMIUM"}
        assert self.engine._safe_eval("tier in ['PREMIUM', 'GOLD']", ctx) is True
        assert self.engine._safe_eval("tier in ['BASIC']", ctx) is False

    def test_not_in_operator(self):
        ctx = {"tier": "BASIC"}
        assert self.engine._safe_eval("tier not in ['PREMIUM', 'GOLD']", ctx) is True
        assert self.engine._safe_eval("tier not in ['BASIC']", ctx) is False

    def test_missing_context_returns_false(self):
        ctx = {}
        assert self.engine._safe_eval("nonexistent > 5", ctx) is False

    def test_unknown_condition_returns_false(self):
        ctx = {"a": 1}
        assert self.engine._safe_eval("some weird expression", ctx) is False


# =============================================
# RiskWeights auto-validation
# =============================================


class TestRiskWeightsValidation:
    """Test that RiskWeights enforces sum == 1.0."""

    def test_valid_weights(self):
        """Default weights should pass validation."""
        w = RiskWeights()
        assert w.validate_sum() is True

    def test_invalid_weights_raises(self):
        """Weights that don't sum to 1.0 should raise ValueError."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            RiskWeights(w_physical=0.5, w_financial=0.5, w_contractual=0.5, w_communication=0.5)


# =============================================
# Full Brain end-to-end (mocked external)
# =============================================


class TestBrainEndToEnd:
    """End-to-end test of the full brain pipeline with mocks."""

    @pytest.mark.asyncio
    async def test_brain_processes_medium_trigger(self):
        """Full pipeline for a MEDIUM severity trigger."""
        from caos.core.brain import process_trigger

        trigger_data = {
            "event_id": "evt_e2e_001",
            "source": "manual",
            "severity": "MEDIUM",
            "payload": {"temp": 78.0},
            "context": {
                "tenant_id": "tenant_e2e",
                "asset_id": "CHILLER-04",
            },
            "metric": "temperature",
            "value": 78.0,
        }

        mock_atlas = {
            "current_state": {"temperature": 78.0},
            "max_operating_temp": 90.0,
            "contract_tier": "PREMIUM",
            "allowed_actions": ["shutdown", "notification"],
            "under_maintenance": False,
            "maintenance_window_active": False,
        }

        mock_oracle = {
            "forecast_metric": "failure_probability",
            "predicted_value": 0.4,
            "confidence": 0.7,
            "horizon_hours": 24,
            "financial_impact": 2000.0,
            "recommendation": "Monitorar.",
        }

        with patch("caos.core.nodes.sense.get_atlas_client") as m_atlas, \
             patch("caos.core.nodes.oracle.get_oracle_client") as m_oracle:

            atlas_client = AsyncMock()
            atlas_client.get_asset_context = AsyncMock(return_value=mock_atlas)
            m_atlas.return_value = atlas_client

            oracle_client = AsyncMock()
            oracle_client.get_prediction = AsyncMock(return_value=mock_oracle)
            m_oracle.return_value = oracle_client

            final_state = await process_trigger(trigger_data)

        # Verify the pipeline produced a complete state
        assert "verdict_score" in final_state
        assert "decision_band" in final_state
        assert "proposed_action" in final_state
        assert final_state["proposed_action"] is not None
        action = final_state["proposed_action"]
        assert action.action_id.startswith("act_")

    @pytest.mark.asyncio
    async def test_brain_blocks_critical_maintenance(self):
        """Critical alert on asset under maintenance should be blocked."""
        from caos.core.brain import process_trigger

        trigger_data = {
            "event_id": "evt_e2e_block",
            "source": "sentinel",
            "severity": "CRITICAL",
            "payload": {"temp": 50.0},
            "context": {
                "tenant_id": "tenant_e2e",
                "asset_id": "CHILLER-04",
            },
            "metric": "temperature",
            "value": 50.0,
        }

        mock_atlas = {
            "current_state": {"temperature": 50.0},
            "max_operating_temp": 90.0,
            "contract_tier": "PREMIUM",
            "allowed_actions": ["shutdown", "notification"],
            "under_maintenance": True,
            "maintenance_window_active": True,
        }

        with patch("caos.core.nodes.sense.get_atlas_client") as m_atlas:
            atlas_client = AsyncMock()
            atlas_client.get_asset_context = AsyncMock(return_value=mock_atlas)
            m_atlas.return_value = atlas_client

            final_state = await process_trigger(trigger_data)

        # Should be blocked/vetoed because of maintenance + CRITICAL
        assert final_state.get("verdict_score", 0) <= 0
