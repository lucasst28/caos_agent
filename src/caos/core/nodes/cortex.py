"""Cortex Node - Hybrid Reasoning (CODE + LLM).

The cognitive core where:
1. CODE calculates risk dimensions (R_F, R_Fin, R_C, R_K)
2. LLM (Gemini) reasons about the situation and proposes actions
3. Verdict formula is applied
"""

import structlog
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from caos.config import get_settings
from caos.core.judge import JudgeEngine
from caos.schemas.enums import Severity
from caos.schemas.risk import RiskDimensions
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)

# =============================================
# LLM singleton (via registry)
# =============================================

# Sentinel value to distinguish "not yet initialized" from "initialized as None"
_LLM_NOT_SET = object()


def get_llm():
    """Get or create the LLM instance.
    
    Uses ChatGoogleGenerativeAI (langchain-google-genai) which supports both:
    1. Google AI Studio via API key (GOOGLE_API_KEY)
    2. Vertex AI via ADC (gcloud auth) when Google Cloud is configured
    """
    from caos._registry import get, put

    llm = get("llm")
    if llm is not None:
        return llm

    # Check if we already tried and got None (no config)
    if get("llm_initialized"):
        return None

    settings = get_settings()

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs: dict[str, Any] = {
            "model": settings.vertex_ai_model,
            "temperature": 0.1,
            "max_output_tokens": 1024,
        }

        # Use API key if available; otherwise rely on ADC (Application Default Credentials)
        if settings.google_api_key:
            kwargs["google_api_key"] = settings.google_api_key
            provider = "google_ai_studio"
        elif settings.google_cloud_project:
            # ADC mode — requires `gcloud auth application-default login`
            kwargs["project"] = settings.google_cloud_project
            kwargs["location"] = settings.vertex_ai_location
            provider = "vertex_ai_adc"
        else:
            logger.warning("llm_not_configured", detail="No API key or GCP project set")
            put("llm_initialized", True)
            return None

        llm = ChatGoogleGenerativeAI(**kwargs)
        put("llm", llm)
        logger.info("llm_initialized", provider=provider, model=settings.vertex_ai_model)
        return llm

    except Exception as e:
        logger.warning("llm_init_failed", error=str(e))
        put("llm_initialized", True)
        return None


# =============================================
# System Prompt para o CAOS
# =============================================

SYSTEM_PROMPT = """Você é o CAOS (Centralized Autonomous Operating System), o juiz autônomo do sistema VivAIOT.

Sua função é analisar alertas de equipamentos industriais e recomendar ações.

## Regras
- Seja CONCISO e OBJETIVO
- Responda SEMPRE em português
- Analise os 4 eixos de risco: Físico, Financeiro, Contratual, Comunicação
- Proponha a ação mais adequada entre: shutdown, setpoint, notification, ticket, restart, nenhuma
- Justifique em 2-3 frases

## Formato de Resposta
ANÁLISE: [1-2 frases sobre a situação]
RISCO_FISICO: [0.0-1.0]
RISCO_FINANCEIRO: [0.0-1.0]
RISCO_CONTRATUAL: [0.0-1.0]
RISCO_COMUNICACAO: [0.0-1.0]
AÇÃO: [tipo de ação recomendada]
JUSTIFICATIVA: [2-3 frases]"""


def build_context_prompt(state: JudgeState) -> str:
    """Build the context prompt for the LLM."""
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}
    oracle = state.get("oracle_forecast")

    parts = [
        f"## Alerta Recebido",
        f"- Ativo: {trigger.context.asset_id if trigger.context else 'Desconhecido'}",
        f"- Severidade: {trigger.severity.value}",
        f"- Métrica: {trigger.metric or 'N/A'}",
        f"- Valor atual: {trigger.value}",
        f"- Threshold violado: {trigger.threshold_violated or 'N/A'}",
    ]

    if atlas:
        parts.extend([
            f"\n## Contexto do Ativo (Atlas)",
            f"- Estado: {atlas.get('current_state', {})}",
            f"- Temp máx operação: {atlas.get('max_operating_temp', 'N/A')}°C",
            f"- Contrato: {atlas.get('contract_tier', 'N/A')}",
            f"- Ações permitidas: {atlas.get('allowed_actions', [])}",
            f"- Em manutenção: {atlas.get('under_maintenance', False)}",
        ])
        if atlas.get("manual_excerpts"):
            parts.append(f"- Manual: {'; '.join(atlas['manual_excerpts'][:2])}")

    if oracle:
        parts.extend([
            f"\n## Predição (Oracle)",
            f"- Prob. falha: {oracle.get('predicted_value', 'N/A')}",
            f"- Confiança: {oracle.get('confidence', 'N/A')}",
            f"- Impacto financeiro: R${oracle.get('financial_impact', 0):,.2f}",
            f"- Recomendação: {oracle.get('recommendation', 'N/A')}",
        ])
    elif state.get("is_fast_track"):
        parts.append("\n## Oracle: BYPASS (fast-track por severidade)")

    parts.append("\nAnalise a situação e responda no formato especificado.")
    return "\n".join(parts)


def parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse structured LLM response into risk dimensions."""
    result = {
        "physical": 0.5,
        "financial": 0.3,
        "contractual": 0.1,
        "communication": 0.1,
        "action": "notification",
        "analysis": "",
        "justification": "",
    }

    for line in response_text.strip().split("\n"):
        line = line.strip()
        if line.startswith("RISCO_FISICO:"):
            try:
                result["physical"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif line.startswith("RISCO_FINANCEIRO:"):
            try:
                result["financial"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif line.startswith("RISCO_CONTRATUAL:"):
            try:
                result["contractual"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif line.startswith("RISCO_COMUNICACAO:"):
            try:
                result["communication"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif line.startswith("AÇÃO:"):
            result["action"] = line.split(":")[1].strip().lower()
        elif line.startswith("ANÁLISE:"):
            result["analysis"] = line.split(":", 1)[1].strip()
        elif line.startswith("JUSTIFICATIVA:"):
            result["justification"] = line.split(":", 1)[1].strip()

    return result


# =============================================
# Fallback: cálculo por código (sem LLM)
# =============================================

def calculate_risk_dimensions_code(state: JudgeState) -> RiskDimensions:
    """Calculate risk dimensions using code only (fallback without LLM)."""
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}

    # R_F: Physical Risk
    r_f = 0.0
    if trigger.value and atlas.get("max_operating_temp"):
        temp_ratio = trigger.value / atlas["max_operating_temp"]
        r_f = min(1.0, max(0.0, (temp_ratio - 0.8) / 0.2))
    if trigger.severity == Severity.CRITICAL:
        r_f = max(r_f, 0.9)
    elif trigger.severity == Severity.HIGH:
        r_f = max(r_f, 0.6)

    # R_Fin: Financial Risk
    r_fin = 0.0
    oracle = state.get("oracle_forecast")
    if oracle and oracle.get("financial_impact"):
        r_fin = min(1.0, oracle["financial_impact"] / 10000.0)
    elif trigger.severity in [Severity.CRITICAL, Severity.HIGH]:
        r_fin = 0.5

    # R_C: Contractual Risk
    r_c = 0.0
    if atlas.get("contract_tier") == "MONITORING_ONLY":
        r_c = 0.8

    # R_K: Communication Risk
    r_k = 0.1

    return RiskDimensions(physical=r_f, financial=r_fin, contractual=r_c, communication=r_k)


# =============================================
# Main Cortex Node
# =============================================

async def cortex_node(state: JudgeState) -> dict[str, Any]:
    """Cortex Node: Hybrid Reasoning (CODE + LLM) and Verdict.

    Flow:
    1. Try LLM reasoning (Gemini) for rich analysis
    2. Fallback to code-only if LLM unavailable
    3. Apply Verdict formula
    4. Classify decision band
    """
    logger.info("cortex_node_start", event_id=state["trigger"].event_id)

    llm = get_llm()
    llm_reasoning = None
    reasoning_trace = []

    # === Step 1: Hybrid Risk Calculation ===
    if llm is not None:
        try:
            context_prompt = build_context_prompt(state)
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=context_prompt),
            ]

            response = await llm.ainvoke(messages)
            llm_response_text = response.content
            llm_reasoning = parse_llm_response(llm_response_text)

            risk_dimensions = RiskDimensions(
                physical=min(1.0, max(0.0, llm_reasoning["physical"])),
                financial=min(1.0, max(0.0, llm_reasoning["financial"])),
                contractual=min(1.0, max(0.0, llm_reasoning["contractual"])),
                communication=min(1.0, max(0.0, llm_reasoning["communication"])),
            )

            reasoning_trace.append(f"🤖 LLM: {llm_reasoning.get('analysis', '')}")
            reasoning_trace.append(f"💡 Ação sugerida: {llm_reasoning.get('action', 'N/A')}")
            reasoning_trace.append(f"📋 {llm_reasoning.get('justification', '')}")

            logger.info(
                "cortex_llm_success",
                analysis=llm_reasoning.get("analysis", ""),
                action=llm_reasoning.get("action", ""),
            )

        except Exception as e:
            logger.warning("cortex_llm_fallback", error=str(e))
            risk_dimensions = calculate_risk_dimensions_code(state)
            reasoning_trace.append(f"⚠️ LLM indisponível, usando cálculo por código: {e}")
    else:
        risk_dimensions = calculate_risk_dimensions_code(state)
        reasoning_trace.append("📊 Modo código: LLM não configurada")

    # === Step 2: Calculate A and O scores ===
    atlas = state.get("atlas_context") or {}
    oracle = state.get("oracle_forecast")

    a_score = 0.5
    if atlas.get("manual_excerpts"):
        a_score = 0.8
    if atlas.get("under_maintenance"):
        a_score = 0.0

    o_score = 1.0
    if oracle:
        o_score = oracle.get("confidence", 0.5)
    elif state.get("is_fast_track"):
        o_score = 1.0

    # === Step 3: Verdict ===
    judge = JudgeEngine()
    result = judge.judge(
        atlas_score=a_score,
        oracle_score=o_score,
        risk_dimensions=risk_dimensions,
        guardrail_penalty=0.0,
    )

    reasoning_trace.append(
        f"⚖️ Verdict: {result.verdict_score:.3f} → {result.decision_band.value} "
        f"(Risk: {result.risk_level.value})"
    )

    logger.info(
        "cortex_node_complete",
        event_id=state["trigger"].event_id,
        verdict_score=result.verdict_score,
        decision_band=result.decision_band.value,
        risk_level=result.risk_level.value,
        used_llm=llm_reasoning is not None,
    )

    return {
        "risk_dimensions": risk_dimensions,
        "atlas_score": a_score,
        "oracle_score": o_score,
        "severity_score": result.severity_score,
        "verdict_score": result.verdict_score,
        "decision_band": result.decision_band,
        "risk_level": result.risk_level,
        "reasoning_trace": reasoning_trace,
        "llm_analysis": llm_reasoning,
    }
