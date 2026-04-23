"""Test Oracle hybrid CODE+LLM fusion (reasoning moved from cortex to oracle)."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone
from caos.core.nodes import oracle as oracle_module
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerPayload, TriggerContext, TriggerSource, Severity
from caos.schemas.risk import RiskDimensions

# Mock LLM Response
def make_llm_response(confidence: float, physical: float = 0.5) -> str:
    return f"""
ANÁLISE: Test analysis.
CONFIANÇA: {confidence}
RISCO_FISICO: {physical}
RISCO_FINANCEIRO: 0.1
RISCO_CONTRATUAL: 0.1
RISCO_COMUNICACAO: 0.1
AÇÃO: notification
JUSTIFICATIVA: Just testing.
"""

@pytest.mark.asyncio
async def test_dynamic_fusion_low_confidence():
    """Verify code weight increases when LLM confidence is low."""
    # Setup State
    state = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_conf_low",
            source=TriggerSource.SENTINEL,
            timestamp=datetime.now(timezone.utc),
            severity=Severity.MEDIUM,
            metric="temperature",
            value=60.0,
            context=TriggerContext(tenant_id="t1", asset_id="a1"),
        ),
        atlas_context={"max_operating_temp": 100.0}
    )

    # Mock LLM returning 0.2 confidence
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content=make_llm_response(0.2))
    
    with patch("caos.core.nodes.oracle.get_llm", return_value=mock_llm):
        # We also need to mock circuit breaker to avoid 'tripped' check
        mock_cb = MagicMock()
        mock_cb.is_tripped.return_value = False
        with patch("caos.core.nodes.oracle.get_circuit_breaker", return_value=mock_cb):
            result = await oracle_module.oracle_node(state)

    # Find reasoning trace about fusion
    trace = result["reasoning_trace"]
    fusion_line = next((line for line in trace if "Fusão híbrida" in line), None)
    
    assert fusion_line is not None
    # Expect LLM(20%) + Código(80%)
    assert "LLM(20%)" in fusion_line
    assert "Código(80%)" in fusion_line

@pytest.mark.asyncio
async def test_dynamic_fusion_high_confidence():
    """Verify LLM weight increases when confidence is high."""
    state = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_conf_high",
            source=TriggerSource.SENTINEL,
            timestamp=datetime.now(timezone.utc),
            severity=Severity.MEDIUM,
            metric="temperature",
            value=60.0,
            context=TriggerContext(tenant_id="t1", asset_id="a1"),
        ),
        atlas_context={"max_operating_temp": 100.0}
    )

    # Mock LLM returning 0.9 confidence
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content=make_llm_response(0.9))
    
    with patch("caos.core.nodes.oracle.get_llm", return_value=mock_llm):
        mock_cb = MagicMock()
        mock_cb.is_tripped.return_value = False
        with patch("caos.core.nodes.oracle.get_circuit_breaker", return_value=mock_cb):
            result = await oracle_module.oracle_node(state)

    trace = result["reasoning_trace"]
    fusion_line = next((line for line in trace if "Fusão híbrida" in line), None)
    
    assert fusion_line is not None
    # Expect LLM(90%) + Código(10%)
    assert "LLM(90%)" in fusion_line
    assert "Código(10%)" in fusion_line
