"""Shared test fixtures for CAOS Agent tests."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from caos.schemas.enums import DecisionBand, RiskLevel, Severity, TriggerSource
from caos.schemas.trigger import TriggerContext, TriggerPayload


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before each test to prevent state leaks."""
    from caos._registry import reset_all
    reset_all()
    yield
    reset_all()


@pytest.fixture
def sample_trigger() -> TriggerPayload:
    """Create a standard test trigger."""
    return TriggerPayload(
        event_id="evt_test001",
        source=TriggerSource.SENTINEL,
        timestamp=datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc),
        severity=Severity.MEDIUM,
        payload={"temp": 75.0, "unit": "celsius"},
        context=TriggerContext(
            tenant_id="tenant_abc",
            asset_id="CHILLER-04",
            location="Sala de Máquinas B",
        ),
        metric="temperature",
        value=75.0,
        threshold_violated=90.0,
    )


@pytest.fixture
def critical_trigger() -> TriggerPayload:
    """Create a CRITICAL severity trigger."""
    return TriggerPayload(
        event_id="evt_critical001",
        source=TriggerSource.SENTINEL,
        timestamp=datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc),
        severity=Severity.CRITICAL,
        payload={"temp": 115.0, "unit": "celsius"},
        context=TriggerContext(
            tenant_id="tenant_abc",
            asset_id="CHILLER-04",
        ),
        metric="temperature",
        value=115.0,
        threshold_violated=90.0,
    )


@pytest.fixture
def low_trigger() -> TriggerPayload:
    """Create a LOW severity trigger."""
    return TriggerPayload(
        event_id="evt_low001",
        source=TriggerSource.SENTINEL,
        timestamp=datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc),
        severity=Severity.LOW,
        payload={"temp": 45.0, "unit": "celsius"},
        context=TriggerContext(
            tenant_id="tenant_abc",
            asset_id="CHILLER-04",
        ),
        metric="temperature",
        value=45.0,
    )


@pytest.fixture
def sample_atlas_context() -> dict:
    """Create a standard Atlas context."""
    return {
        "current_state": {"temperature": 75.0, "status": "operational"},
        "last_updated": "2026-02-09T12:00:00",
        "max_operating_temp": 90.0,
        "min_operating_temp": -20.0,
        "max_pressure": 150.0,
        "max_vibration": 8.5,
        "manual_excerpts": [
            "Se a temperatura exceder 90°C, desligar imediatamente.",
        ],
        "contract_id": "contract_001",
        "contract_tier": "PREMIUM",
        "allowed_actions": ["shutdown", "notification", "ticket", "setpoint"],
        "sla_response_time_minutes": 30,
        "under_maintenance": False,
        "maintenance_window_active": False,
    }


@pytest.fixture
def sample_oracle_prediction() -> dict:
    """Create a standard Oracle prediction."""
    return {
        "forecast_metric": "failure_probability",
        "predicted_value": 0.75,
        "confidence": 0.85,
        "horizon_hours": 24,
        "financial_impact": 5000.0,
        "recommendation": "Desligamento preventivo em 4h.",
    }


@pytest.fixture
def mock_atlas_client(sample_atlas_context):
    """Mock AtlasClient that returns sample context."""
    with patch("caos.integration.atlas.AtlasClient") as mock:
        client = AsyncMock()
        client.get_asset_context = AsyncMock(return_value=sample_atlas_context)
        client.search_manuals = AsyncMock(return_value=["Manual excerpt"])
        mock.return_value = client
        yield client


@pytest.fixture
def mock_oracle_client(sample_oracle_prediction):
    """Mock OracleClient that returns sample prediction."""
    with patch("caos.integration.oracle.OracleClient") as mock:
        client = AsyncMock()
        client.get_prediction = AsyncMock(return_value=sample_oracle_prediction)
        mock.return_value = client
        yield client
