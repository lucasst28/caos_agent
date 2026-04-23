import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from caos.schemas.enums import Severity, TriggerSource
from caos.schemas.trigger import TriggerPayload, TriggerContext
from caos.schemas.state import JudgeState
from caos.core.nodes.oracle import build_context_prompt, oracle_node

@pytest.fixture
def base_state():
    from datetime import datetime, timezone
    trigger = TriggerPayload(
        event_id="evt_test_op",
        source=TriggerSource.SENTINEL,
        timestamp=datetime.now(timezone.utc),
        severity=Severity.HIGH,
        payload={"temp": -5.0},
        context=TriggerContext(tenant_id="t1", asset_id="FREEZER-01", location="Test"),
        metric="temperature",
        value=-5.0,
    )
    return {
        "trigger": trigger,
        "atlas_context": {
            "max_operating_temp": -12.1,
            "simulation_data": {
                "door_open_events_last_hour": 15,
                "compressor_status": "running",
                "compressor_current_amps": 4.2,
                "compressor_max_rated_amps": 5.5,
                "ambient_temperature_c": 32.0,
            }
        },
        "reasoning_trace": [],
    }

def test_build_context_prompt_operational_detection(base_state):
    """Test that build_context_prompt includes the operational diagnosis block."""
    prompt = build_context_prompt(base_state)
    
    # Check for the new sections
    assert "🚨 DIAGNÓSTICO OPERACIONAL:" in prompt
    assert "15 aberturas/hora" in prompt
    assert "Clima Externo 32.0°C" in prompt
    assert "REGRA OBRIGATÓRIA" in prompt
    assert "escolher ação `observe`" in prompt
    assert "NÃO gere notificação" in prompt

@pytest.mark.asyncio
async def test_oracle_node_operational_enforcement(base_state):
    """Test that oracle_node sets operational_root_cause and handles LLM misalignment."""
    
    # Mock LLM to return something else than 'observe'
    mock_llm_response = MagicMock()
    mock_llm_response.content = """
PENSAMENTO: Temperatura está alta. 
ANÁLISE: Equipamento operando fora do range.
AÇÃO: notification
JUSTIFICATIVA: Notificar sobre temperatura de -5.0°C.
RISCO_FISICO: 0.8
RISCO_FINANCEIRO: 0.5
RISCO_CONTRATUAL: 0.2
RISCO_COMUNICACAO: 0.3
CONFIANÇA: 0.9
"""

    with patch("caos.core.nodes.oracle.get_llm") as mock_get_llm, \
         patch("caos.core.nodes.oracle.get_oracle_client") as mock_get_oracle, \
         patch("caos.core.nodes.oracle.get_circuit_breaker") as mock_get_cb:
        
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = mock_llm_response
        mock_get_llm.return_value = mock_llm
        
        mock_oracle = AsyncMock()
        mock_oracle.get_prediction.return_value = None
        mock_get_oracle.return_value = mock_oracle
        
        mock_cb = MagicMock()
        mock_cb.is_tripped.return_value = False
        mock_get_cb.return_value = mock_cb
        
        # We need to mock _record_llm_usage as well
        with patch("caos.core.nodes.oracle._record_llm_usage"):
            result = await oracle_node(base_state)
    
    # Check enforcement
    assert result["operational_root_cause"] == "door_excess_or_ambient_heat"
    
    # Check trace for enforcement message
    trace = "\n".join(result["reasoning_trace"])
    assert "FORÇANDO OBSERVE (Code Logic) 🛡️" in trace
    assert "Ação foi ajustada para OBSERVE" in trace
    
    # Note: the llm_analysis still contains the original LLM response
    # but the operational_root_cause key tells act_node to use ActionType.OBSERVE
    assert result["llm_analysis"]["action"] == "notification" # Original LLM
    
def test_build_context_prompt_only_hot_weather(base_state):
    """Test detection with only hot weather (no excessive doors)."""
    base_state["atlas_context"]["simulation_data"]["door_open_events_last_hour"] = 2
    base_state["atlas_context"]["simulation_data"]["ambient_temperature_c"] = 35.0
    
    prompt = build_context_prompt(base_state)
    assert "🚨 DIAGNÓSTICO OPERACIONAL:" in prompt
    assert "Clima Externo 35.0°C" in prompt
    assert "2 aberturas/hora" not in prompt # Should only show if > 10

def test_build_context_prompt_healthy_comp_required(base_state):
    """Test that detection requires healthy compressor."""
    # Simulate failed compressor (high amps)
    base_state["atlas_context"]["simulation_data"]["compressor_current_amps"] = 10.0
    
    prompt = build_context_prompt(base_state)
    assert "🚨 DIAGNÓSTICO OPERACIONAL:" not in prompt
