import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerPayload, TriggerContext, TriggerSource, Severity
from caos.schemas.risk import RiskDimensions
from caos.schemas.enums import DecisionBand, RiskLevel
from caos.core.nodes.cortex import (
    ChecklistItem,
    VerificationReport,
    verify_data_sources,
    verify_risk_assessment,
    verify_action_appropriateness,
    verify_reasoning_completeness,
    parse_verifier_response,
    cortex_node
)

def make_base_state(severity=Severity.HIGH) -> JudgeState:
    return JudgeState(
        trigger=TriggerPayload(
            event_id="test_event_123",
            source=TriggerSource.SENTINEL,
            timestamp=datetime.now(timezone.utc),
            severity=severity,
            metric="temperature",
            value=85.0,
            context=TriggerContext(tenant_id="t1", asset_id="a1")
        ),
        atlas_context={
            "contract_tier": "PREMIUM",
            "sla_response_time_minutes": 60,
            "max_operating_temp": 80.0,
            "allowed_actions": ["observe", "ticket", "notification", "setpoint", "shutdown"],
            "simulation_data": {"temperature": [80.0, 85.0]},
            "financial_data": {"estimated_product_value_brl": 10000.0}
        },
        llm_analysis={
            "thought": "A temperatura subiu acima do limite. Precisamos analisar a causa raiz...",
            "analysis": "Causa raiz identificada: falha no compressor confirmada por anomalia de corrente.",
            "action": "shutdown",
            "justification": "Desligamento imediato necessário para evitar perda total",
            "risk_justifications": {
                "physical": "Risco de quebra do motor",
                "financial": "Perda de R$ 10.000,00 reais em mercadoria",
                "contractual": "Viola SLA de 60 minutos se não houver ação",
                "communication": "Notificar gerente urgentemente"
            },
            "hypotheses_discarded": ["Hipótese 1 refutada"],
            "data_sources_consulted": ["Atlas", "Telemetria"]
        },
        risk_dimensions=RiskDimensions(physical=0.9, financial=0.6, contractual=0.4, communication=0.8),
        verifier_retry_count=0,
        decision_band=DecisionBand.EXECUTE,
        risk_level=RiskLevel.HIGH
    )


def test_parse_verifier_response():
    sample_response_approved = """
VEREDICTO: APROVADO
CONFIANÇA: 0.85
SCORE: 0.90

ANÁLISE_CAOS:
O Oracle analisou corretamente todas as evidências.

PROBLEMAS_ENCONTRADOS:

FEEDBACK_PARA_ORACLE:
N/A

DADOS_IGNORADOS:
"""
    parsed = parse_verifier_response(sample_response_approved)
    assert parsed["verdict"] == "APROVADO"
    assert parsed["confidence"] == 0.85
    assert parsed["score"] == 0.90
    assert "corretamente" in parsed["analysis"]
    assert len(parsed["issues"]) == 0
    assert parsed["feedback"] == ""

    sample_response_rejected = """
VEREDICTO: REJEITADO
CONFIANÇA: 0.9
SCORE: 0.3

ANÁLISE_CAOS:
Ação muito severa para a causa raiz.

PROBLEMAS_ENCONTRADOS:
- Ignorou fator ambiente externo.
- R_F superestimado sem provas reais.

FEEDBACK_PARA_ORACLE:
Avalie se a porta aberta não explica a temperatura antes de propor shutdown.

DADOS_IGNORADOS:
- Sensor de Porta
"""
    parsed_rej = parse_verifier_response(sample_response_rejected)
    assert parsed_rej["verdict"] == "REJEITADO"
    assert "Ignorou fator" in parsed_rej["issues"][0]
    assert len(parsed_rej["issues"]) == 2
    assert "Avalie se a porta" in parsed_rej["feedback"]
    assert "Sensor de Porta" in parsed_rej["ignored_data"][0]


def test_verify_data_sources():
    state = make_base_state()
    report = VerificationReport()
    verify_data_sources(state, report)
    # Check that data sources were checked correctly
    assert any(i.item_id == "DATA_001" and i.passed for i in report.items)
    assert any(i.item_id == "DATA_002" and i.passed for i in report.items)


def test_verify_risk_assessment_mismatch():
    state = make_base_state(severity=Severity.CRITICAL)
    # Give it low risk dimensions -> mismatch 
    state["risk_dimensions"] = RiskDimensions(physical=0.2, financial=0.1, contractual=0.1, communication=0.1)
    
    report = VerificationReport()
    verify_risk_assessment(state, report)
    
    # Should flag RISK_005 because severity CRITICAL but max risk is 0.2
    risk_005 = next(i for i in report.items if i.item_id == "RISK_005")
    assert not risk_005.passed
    assert report.score < 1.0


def test_verify_action_appropriateness_loto_violation():
    state = make_base_state()
    # Assume under maintenance (LOTO)
    state["atlas_context"]["under_maintenance"] = True
    report = VerificationReport()
    verify_action_appropriateness(state, report)
    
    act_004 = next(i for i in report.items if i.item_id == "ACT_004")
    assert not act_004.passed
    assert "loto_violation" in report.risk_adjustments


def test_verify_reasoning_completeness():
    state = make_base_state()
    report = VerificationReport()
    verify_reasoning_completeness(state, report)
    
    reason_001 = next((i for i in report.items if i.item_id == "REASON_001"), None)
    assert reason_001 is not None and reason_001.passed

    reason_002 = next((i for i in report.items if i.item_id == "REASON_002"), None)
    assert reason_002 is not None and reason_002.passed


@pytest.mark.asyncio
@patch("caos.core.nodes.oracle.get_llm")
@patch("caos.safety.runtime.get_circuit_breaker")
async def test_cortex_node_llm_rejection(mock_get_cb, mock_get_llm):
    # Mock Circuit breaker
    cb_instance = MagicMock()
    cb_instance.is_tripped.return_value = False
    mock_get_cb.return_value = cb_instance
    
    # Mock LLM to return REJEITADO
    mock_llm_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "VEREDICTO: REJEITADO\nCONFIANÇA: 0.9\nSCORE: 0.5\nANÁLISE_CAOS: Análise fraca.\nPROBLEMAS_ENCONTRADOS:\n- Nada\nFEEDBACK_PARA_ORACLE:\nRefaça a análise\n"
    mock_llm_instance.ainvoke.return_value = mock_response
    mock_get_llm.return_value = mock_llm_instance
    
    state = make_base_state()
    result = await cortex_node(state)
    
    assert "verification_passed" in result
    assert result["verification_passed"] is False
    assert result["verification_score"] < 1.0
    assert "verifier_feedback" in result
    assert "Refaça a análise" in result["verifier_feedback"]
    assert result["verifier_retry_count"] == 0


@pytest.mark.asyncio
@patch("caos.core.nodes.oracle.get_llm")
@patch("caos.safety.runtime.get_circuit_breaker")
async def test_cortex_node_loto_override(mock_get_cb, mock_get_llm):
    # Mock Circuit breaker
    cb_instance = MagicMock()
    cb_instance.is_tripped.return_value = False
    mock_get_cb.return_value = cb_instance
    
    # Mock LLM to return APROVADO (but checklist will find LOTO violation and override it)
    mock_llm_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "VEREDICTO: APROVADO\nCONFIANÇA: 0.9\nSCORE: 0.9\nANÁLISE_CAOS: Tudo certo.\n"
    mock_llm_instance.ainvoke.return_value = mock_response
    mock_get_llm.return_value = mock_llm_instance
    
    state = make_base_state()
    state["atlas_context"]["under_maintenance"] = True # triggers LOTO failure in ACT_004
    
    result = await cortex_node(state)
    
    assert result["verification_passed"] is False
    assert result["decision_band"] == DecisionBand.BLOCKED
    assert result["verdict_score"] == -1.0
    assert "loto_block" in result.get("verification_adjustments", {})

