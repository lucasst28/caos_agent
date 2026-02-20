
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone
from caos.core.nodes import cortex
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerPayload, TriggerContext, TriggerSource, Severity

# Mock LLM Response
def make_llm_response():
    return """
ANÁLISE: Analysis based on RAG.
CONFIANÇA: 0.9
RISCO_FISICO: 0.1
RISCO_FINANCEIRO: 0.1
RISCO_CONTRATUAL: 0.1
RISCO_COMUNICACAO: 0.1
AÇÃO: notification
JUSTIFICATIVA: RAG helped.
"""

@pytest.mark.asyncio
async def test_cortex_uses_rag_context():
    """Verify Cortex queries RAG and injects result into prompt."""
    state = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_rag_test",
            source=TriggerSource.SENTINEL,
            timestamp=datetime.now(timezone.utc),
            severity=Severity.MEDIUM,
            metric="temperature",
            value=105.0,
            context=TriggerContext(tenant_id="t1", asset_id="a1"),
        ),
        atlas_context={}
    )

    # Mock RAG Engine
    mock_rag = MagicMock()
    mock_rag.query.return_value = ["CRITICAL: Check coolant level immediately."]
    # Mock seeding behavior
    mock_rag.documents = []  
    
    # Mock LLM
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content=make_llm_response())

    with patch("caos.core.nodes.cortex.get_rag_engine", return_value=mock_rag) as p_rag:
        with patch("caos.core.nodes.cortex.get_llm", return_value=mock_llm) as p_llm:
            # Mock CB
            mock_cb = MagicMock()
            mock_cb.is_tripped.return_value = False
            
            with patch("caos.core.nodes.cortex.get_circuit_breaker", return_value=mock_cb):
                await cortex.cortex_node(state)
    
    # Debug checks
    assert mock_cb.is_tripped.called, "Circuit breaker check skipped"
    assert p_llm.called, "get_llm not called"
    
    # Verify RAG Query
    mock_rag.query.assert_called_once()
    args, _ = mock_rag.query.call_args
    assert "temperature" in args[0]
    assert "105.0" in args[0]

    # Verify Prompt Injection
    call_args = p_llm.return_value.ainvoke.call_args
    assert call_args is not None, "LLM ainvoke never called"
    messages = call_args[0][0]
    human_msg = messages[1].content
    
    assert "## 📘 Manual Técnico (Contexto RAG)" in human_msg
    assert "Check coolant level immediately" in human_msg
