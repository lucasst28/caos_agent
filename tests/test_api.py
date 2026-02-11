"""End-to-end tests for the CAOS API endpoints.

Uses FastAPI TestClient to test the full API without running the server.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from fastapi.testclient import TestClient


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


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    from main import app
    return TestClient(app)


class TestHealthEndpoints:
    """Test health and info endpoints."""

    def test_health_check(self, client):
        """Health check should return healthy."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_root(self, client):
        """Root should return service info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "CAOS Agent"
        assert "version" in data


class TestGuardrailsAPI:
    """Test guardrails API endpoints."""

    def test_list_guardrails(self, client):
        """Should list all 38 guardrails."""
        response = client.get("/v1/guardrails/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 38
        assert len(data["guardrails"]) == 38

    def test_list_guardrails_by_category(self, client):
        """Should filter guardrails by category."""
        response = client.get("/v1/guardrails/?category=PHYSICAL")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 8
        for g in data["guardrails"]:
            assert g["category"] == "PHYSICAL"

    def test_guardrail_stats(self, client):
        """Should return guardrail statistics."""
        response = client.get("/v1/guardrails/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_rules"] == 38
        assert "PHYSICAL" in data["by_category"]
        assert "BLOCKING" in data["by_severity"]


class TestMetricsAPI:
    """Test metrics API endpoint."""

    def test_metrics(self, client):
        """Should return metrics."""
        response = client.get("/v1/metrics/")
        assert response.status_code == 200
        data = response.json()
        assert "requests" in data
        assert "verdicts" in data
        assert "guardrails" in data
        assert "oracle" in data


class TestFeedbackAPI:
    """Test feedback API endpoints."""

    def test_submit_feedback(self, client):
        """Should accept feedback."""
        response = client.post(
            "/v1/feedback/",
            json={
                "event_id": "evt_test001",
                "action_id": "act_test001",
                "approved": True,
                "reviewer": "operator_01",
                "reason": "Ação correta",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["approved"] is True
        assert data["status"] == "recorded"

    def test_approve_action(self, client):
        """Should process approval."""
        response = client.post(
            "/v1/feedback/approve",
            json={
                "action_id": "act_test001",
                "approved": True,
                "approver": "manager_01",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approved"] is True

    def test_reject_action(self, client):
        """Should process rejection."""
        response = client.post(
            "/v1/feedback/approve",
            json={
                "action_id": "act_test002",
                "approved": False,
                "approver": "manager_01",
                "notes": "Ação desnecessária",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approved"] is False
        assert data["dispatched"] is False

    def test_feedback_stats(self, client):
        """Should return feedback stats."""
        response = client.get("/v1/feedback/stats")
        assert response.status_code == 200
        data = response.json()
        assert "approval_rate" in data


class TestEventsAPI:
    """Test events API endpoints."""

    def test_trigger_event(self, client):
        """Should process a manual trigger."""
        with patch("caos.api.events.process_trigger") as mock_process:
            mock_process.return_value = {
                "verdict_score": 0.65,
                "decision_band": "SUGGEST",
                "risk_level": "MEDIUM",
                "requires_human_approval": False,
                "guardrail_violations": [],
                "reasoning_trace": ["Temp within range"],
                "proposed_action": MagicMock(action_id="act_abc123"),
            }

            response = client.post(
                "/v1/events/trigger",
                json={
                    "tenant_id": "tenant_abc",
                    "asset_id": "CHILLER-04",
                    "severity": "MEDIUM",
                    "metric": "temperature",
                    "value": 75.0,
                    "payload": {"unit": "celsius"},
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["verdict_score"] == 0.65
        assert data["event_id"].startswith("evt_")

    def test_trigger_event_minimal(self, client):
        """Should accept minimal trigger request."""
        with patch("caos.api.events.process_trigger") as mock_process:
            mock_process.return_value = {
                "verdict_score": 0.5,
                "decision_band": "SUGGEST",
                "risk_level": "MEDIUM",
                "requires_human_approval": False,
                "guardrail_violations": [],
                "reasoning_trace": [],
                "proposed_action": None,
            }

            response = client.post(
                "/v1/events/trigger",
                json={
                    "tenant_id": "tenant_abc",
                    "asset_id": "PUMP-01",
                },
            )

        assert response.status_code == 201
