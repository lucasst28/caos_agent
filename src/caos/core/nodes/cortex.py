"""Cortex Node - CAOS Verifier (LLM Auditor + Checklist).

The CAOS Verifier is a dual-layer audit system:

Layer A — Rule-Based Checklist (deterministic):
  30 structured checks across 8 categories ensuring the Oracle:
  1. Consulted all data sources
  2. Justified every risk dimension with evidence
  3. Proposed an appropriate action within contract limits
  4. Completed the full reasoning chain
  5. Respected safety constraints
  6. Analyzed temporal patterns
  7. Considered external factors
  8. Applied the verdict formula correctly

Layer B — LLM Deep Audit (Gemini):
  A dedicated CAOS LLM THINKS about the Oracle's decision:
  - Reviews the Oracle's full chain-of-thought and justifications
  - Cross-checks against the telemetry data and checklist results
  - Decides APPROVED or REJECTED with a reasoned opinion
  - If REJECTED: generates structured feedback with specific instructions
    for the Oracle to fix on retry (what data to consider, what hypotheses
    to test, what risks to re-evaluate)

When CAOS rejects, the Brain sends the structured feedback back to Oracle
for a retry (max 1 retry to prevent loops).
"""

import structlog
import asyncio
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from caos.config import get_settings
from caos.schemas.enums import DecisionBand, RiskLevel, Severity
from caos.schemas.risk import RiskDimensions
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


# =============================================
# M12: Configurable checklist thresholds (per-tenant via tenants.json)
# =============================================

_DEFAULT_CHECKLIST_THRESHOLDS = {
    "risk_low": 0.3,       # Below this → risk is considered low (no justification needed)
    "risk_high": 0.7,      # Above this → risk is considered high (inconsistency flag)
    "risk_critical": 0.8,  # Above this → safety-critical (force HITL)
    "severity_mismatch_low": 0.5,   # CRITICAL severity but max_risk below this → flag
    "verdict_high": 0.7,            # Verdict above this + high risk → flag
}


def get_checklist_thresholds(tenant_id: str | None = None) -> dict[str, float]:
    """Load checklist thresholds, respecting per-tenant overrides.

    Tenants can override via ``checklist_thresholds`` key in tenants.json.
    Missing keys inherit from ``_DEFAULT_CHECKLIST_THRESHOLDS``.
    """
    from caos.core.judge import get_tenant_overrides
    overrides = get_tenant_overrides(tenant_id) if tenant_id else {}
    ct = overrides.get("checklist_thresholds", {})
    return {k: ct.get(k, v) for k, v in _DEFAULT_CHECKLIST_THRESHOLDS.items()}


# =============================================
# CAOS Verifier System Prompt (LLM Audit)
# =============================================

CAOS_VERIFIER_SYSTEM_PROMPT = """Você é o CAOS (Cognitive Autonomous Oversight System) — o auditor independente do sistema.

Seu papel é AUDITAR a decisão do Oracle e decidir se ela é VÁLIDA ou precisa ser REFEITA.

## REGRA FUNDAMENTAL
Você é um AUDITOR, NÃO um consultor. Você NUNCA deve sugerir ações, valores de risco, ou soluções.
Você APENAS aponta:
- Informações que o Oracle deixou de analisar
- Inconsistências entre dados e conclusão
- Hipóteses que não foram testadas ou descartadas sem evidência
- Dados ignorados ou contradições não investigadas
O Oracle deve chegar à própria conclusão. Você apenas valida ou invalida.

## Sua Missão
Você recebe:
1. O ALERTA ORIGINAL (trigger com métricas do equipamento)
2. A DECISÃO DO ORACLE (análise, ação proposta, riscos calculados, justificativa)
3. O RESULTADO DO CHECKLIST AUTOMÁTICO (30 verificações estruturadas)
4. Os DADOS BRUTOS do equipamento (telemetria, contrato, manual)

## Critérios de Avaliação
Analise CRITICAMENTE:

### 1. A causa raiz foi corretamente identificada?
- O Oracle está tratando o SINTOMA ou a CAUSA REAL?
- Existem dados nos sensores que contradizem a conclusão?
- Hipóteses alternativas foram descartadas com evidência?

### 2. Os riscos estão calibrados corretamente?
- R_F (Físico): Reflete o risco REAL ao equipamento e produtos?
- R_Fin (Financeiro): Considera o valor dos produtos em risco?
- R_C (Contratual): Respeita as limitações contratuais?
- R_K (Comunicação): A urgência da situação exige notificação?

### 3. A ação proposta é adequada?
O Oracle pode propor UMA das seguintes ações. Avalie se a escolha é coerente com a causa raiz e os dados:

| Ação | Quando é apropriada |
|------|--------------------|
| **shutdown** | Falha técnica CRÍTICA com risco imediato ao equipamento ou pessoas |
| **maintenance** | Falha técnica confirmada que requer intervenção humana |
| **setpoint** | Ajuste de parâmetro operacional pode resolver o problema |
| **ticket** | Situação que precisa ser analisada, sem urgência imediata |
| **notification** | Informar gestor/operador sobre uma situação relevante |
| **observe** | Causa OPERACIONAL ou AMBIENTAL — aguardar estabilização e reavaliar em 30min |
| **read** | Apenas observar passivamente, sem reagendamento |

⚠️ REGRA CRÍTICA sobre `observe`:
A ação `observe` (aguardar e reavaliar) é a ação CORRETA quando:
- A causa raiz é OPERACIONAL (ex: excesso de abertura de portas, pico de uso) ou AMBIENTAL (ex: clima quente)
- O equipamento funciona NORMALMENTE (compressor rodando, corrente dentro do máximo, sem alarmes mecânicos)
- A situação pode se AUTO-RESOLVER quando o fator operacional cessar

NÃO rejeite `observe` apenas porque a severidade é HIGH ou a temperatura está acima do limite.
Se a causa é operacional e o equipamento está saudável, a temperatura DEVE estabilizar naturalmente.
Intervir desnecessariamente gera custos, interrupções operacionais e desgaste de equipamento.

Pergunte-se:
- A ação proposta é proporcional à CAUSA RAIZ (não ao sintoma)?
- Está dentro das ações permitidas pelo contrato?
- Se foi proposto `observe`: o Oracle demonstrou que a causa é operacional E o equipamento está OK?
- Se foi proposto `shutdown`/`maintenance`: há evidência de falha técnica real?

### 4. Dados foram ignorados?
- Todos os indicadores da telemetria foram analisados?
- Fatores externos (clima, operação) foram considerados?
- Contradições entre sensores foram investigadas?

## Formato de Resposta OBRIGATÓRIO

VEREDICTO: [APROVADO|REJEITADO]
CONFIANÇA: [0.0-1.0]
SCORE: [0.0-1.0]

ANÁLISE_CAOS:
[Sua análise crítica da decisão do Oracle, apontando pontos fortes e fracos]

PROBLEMAS_ENCONTRADOS:
- [Problema 1]
- [Problema 2]
(deixe vazio se aprovado sem ressalvas)

FEEDBACK_PARA_ORACLE:
[SE REJEITADO: Aponte APENAS o que o Oracle deixou de considerar ou verificar.
Inclua:
- Que dados ele esqueceu de analisar
- Que hipóteses ele não testou ou descartou sem evidência
- Que riscos parecem mal calibrados e por quê (sem dizer o valor correto)
- Que inconsistências existem entre os dados e a conclusão
NUNCA sugira qual ação o Oracle deveria tomar — ele deve chegar à conclusão sozinho.
SE APROVADO: "N/A"]

DADOS_IGNORADOS:
- [Dado 1 que não foi analisado]
- [Dado 2 que não foi analisado]
(deixe vazio se tudo foi considerado)
"""


def build_verifier_context(state: JudgeState, checklist_report: dict[str, Any]) -> str:
    """Build the context prompt for the CAOS Verifier LLM."""
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}
    llm_analysis = state.get("llm_analysis") or {}
    risk_dims = state.get("risk_dimensions")
    reasoning_trace = state.get("reasoning_trace", [])
    
    parts = []
    
    # === Section 1: Original Alert ===
    parts.append("=" * 60)
    parts.append("📨 ALERTA ORIGINAL (TRIGGER)")
    parts.append("=" * 60)
    parts.append(f"Evento: {trigger.event_id}")
    parts.append(f"Fonte: {trigger.source.value}")
    parts.append(f"Severidade: {trigger.severity.value}")
    parts.append(f"Métrica: {trigger.metric}")
    parts.append(f"Valor: {trigger.value}")
    if trigger.context:
        parts.append(f"Ativo: {trigger.context.asset_id}")
        parts.append(f"Tenant: {trigger.context.tenant_id}")
    
    # === Section 2: Raw Equipment Data ===
    parts.append("\n" + "=" * 60)
    parts.append("📊 DADOS BRUTOS DO EQUIPAMENTO")
    parts.append("=" * 60)
    
    sim_data = atlas.get("simulation_data", {})
    if sim_data:
        for k, v in sim_data.items():
            if isinstance(v, list) and len(v) > 5:
                parts.append(f"  {k}: [{len(v)} itens] primeiros={v[:3]}")
            else:
                parts.append(f"  {k}: {v}")
    else:
        parts.append("  (sem telemetria estendida)")
    
    if atlas.get("contract_tier"):
        parts.append(f"\nContrato: {atlas['contract_tier']}")
        parts.append(f"SLA: {atlas.get('sla_response_time_minutes', 'N/A')} min")
        parts.append(f"Ações permitidas: {atlas.get('allowed_actions', [])}")
    
    if atlas.get("max_operating_temp") is not None:
        parts.append(f"Temp máx operação: {atlas['max_operating_temp']}°C")
    
    if atlas.get("under_maintenance"):
        parts.append("⚠️ ATIVO EM MANUTENÇÃO (LOTO)")
    
    # === Section 3: Oracle's Decision ===
    parts.append("\n" + "=" * 60)
    parts.append("🧠 DECISÃO DO ORACLE")
    parts.append("=" * 60)
    
    if llm_analysis:
        thought = llm_analysis.get("thought", "")
        if thought:
            parts.append(f"\n### Cadeia de Pensamento do Oracle:")
            parts.append(thought)
        
        parts.append(f"\n### Análise:")
        parts.append(llm_analysis.get("analysis", "(vazio)"))
        
        parts.append(f"\n### Ação Proposta: {llm_analysis.get('action', 'N/A')}")
        
        parts.append(f"\n### Justificativa:")
        parts.append(llm_analysis.get("justification", "(vazio)"))
        
        risk_justs = llm_analysis.get("risk_justifications", {})
        if risk_justs:
            parts.append(f"\n### Justificativas de Risco:")
            for axis, just in risk_justs.items():
                parts.append(f"  {axis}: {just}")
        
        hyps = llm_analysis.get("hypotheses_discarded", [])
        if hyps:
            parts.append(f"\n### Hipóteses Descartadas:")
            for h in hyps:
                parts.append(f"  - {h}")
        
        ds = llm_analysis.get("data_sources_consulted", [])
        if ds:
            parts.append(f"\n### Fontes de Dados Consultadas:")
            for s in ds:
                parts.append(f"  - {s}")
    else:
        parts.append("Oracle operou em MODO CÓDIGO (sem LLM)")
    
    # Risk dimensions
    if risk_dims:
        parts.append(f"\n### Riscos Calculados:")
        parts.append(f"  R_F (Físico):       {risk_dims.physical:.3f}")
        parts.append(f"  R_Fin (Financeiro):  {risk_dims.financial:.3f}")
        parts.append(f"  R_C (Contratual):    {risk_dims.contractual:.3f}")
        parts.append(f"  R_K (Comunicação):   {risk_dims.communication:.3f}")
    
    parts.append(f"\n### Verdict: {state.get('verdict_score', 'N/A')}")
    parts.append(f"### Banda: {state.get('decision_band', 'N/A')}")
    parts.append(f"### Nível de Risco: {state.get('risk_level', 'N/A')}")
    
    # === Section 4: Checklist Results ===
    parts.append("\n" + "=" * 60)
    parts.append("📋 RESULTADO DO CHECKLIST AUTOMÁTICO")
    parts.append("=" * 60)
    parts.append(f"Score: {checklist_report.get('score', 'N/A')}")
    parts.append(f"Checks: {checklist_report.get('passed_checks', '?')}/{checklist_report.get('total_checks', '?')}")
    
    issues = checklist_report.get("issues", [])
    if issues:
        parts.append("\nProblemas detectados pelo checklist:")
        for issue in issues:
            parts.append(f"  ❌ {issue}")
    
    recs = checklist_report.get("recommendations", [])
    if recs:
        parts.append("\nRecomendações do checklist:")
        for rec in recs:
            parts.append(f"  💡 {rec}")
    
    # === Section 5: Oracle Reasoning Trace ===
    parts.append("\n" + "=" * 60)
    parts.append("📝 TRACE DE RACIOCÍNIO COMPLETO DO ORACLE")
    parts.append("=" * 60)
    for step in reasoning_trace:
        parts.append(f"  {step}")
    
    # === Section 6: Operational Pattern Detection ===
    op_cause = state.get("operational_root_cause")
    if op_cause:
        parts.append("\n" + "=" * 60)
        parts.append("🚪 PADRÃO OPERACIONAL DETECTADO PELO CÓDIGO")
        parts.append("=" * 60)
        sim = atlas.get("simulation_data") or {}
        parts.append(f"Causa: {op_cause}")
        parts.append(f"Aberturas de porta/hora: {sim.get('door_open_events_last_hour', 'N/A')}")
        parts.append(f"Compressor status: {sim.get('compressor_status', 'N/A')}")
        parts.append(f"Corrente: {sim.get('compressor_current_amps', 'N/A')}A / {sim.get('compressor_max_rated_amps', 'N/A')}A")
        parts.append("O código de análise confirmou que a causa raiz é OPERACIONAL")
        parts.append("e o equipamento está saudável. Considere isso ao avaliar a ação proposta.")
    
    # === Section 7: Retry context ===
    retry_count = state.get("verifier_retry_count", 0)
    if retry_count > 0:
        parts.append("\n" + "=" * 60)
        parts.append("♻️ ESTA É UMA REAVALIAÇÃO (RETRY)")
        parts.append("=" * 60)
        parts.append(f"O Oracle já foi rejeitado {retry_count} vez(es) e recebeu feedback para corrigir.")
        parts.append("Avalie se as correções foram feitas adequadamente.")
    
    return "\n".join(parts)


def parse_verifier_response(text: str) -> dict[str, Any]:
    """Parse the CAOS Verifier LLM structured response."""
    result = {
        "verdict": "APROVADO",
        "confidence": 0.7,
        "score": 0.7,
        "analysis": "",
        "issues": [],
        "feedback": "",
        "ignored_data": [],
    }
    
    lines = text.strip().split("\n")
    current_section = None
    buffer = []
    
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        
        # Parse top-level fields
        if upper.startswith("VEREDICTO:") or upper.startswith("VEREDITO:"):
            val = stripped.split(":", 1)[1].strip().upper()
            result["verdict"] = "APROVADO" if "APROV" in val else "REJEITADO"
            current_section = None
            continue
        
        if upper.startswith("CONFIANÇA:") or upper.startswith("CONFIANCA:"):
            try:
                result["confidence"] = float(stripped.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
            current_section = None
            continue
        
        if upper.startswith("SCORE:"):
            try:
                result["score"] = float(stripped.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
            current_section = None
            continue
        
        # Section headers
        if upper.startswith("ANÁLISE_CAOS:") or upper.startswith("ANALISE_CAOS:"):
            current_section = "analysis"
            remaining = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if remaining:
                buffer.append(remaining)
            continue
        
        if upper.startswith("PROBLEMAS_ENCONTRADOS:") or upper.startswith("PROBLEMAS:"):
            if current_section == "analysis":
                result["analysis"] = "\n".join(buffer).strip()
                buffer = []
            current_section = "issues"
            continue
        
        if upper.startswith("FEEDBACK_PARA_ORACLE:") or upper.startswith("FEEDBACK:"):
            if current_section == "analysis":
                result["analysis"] = "\n".join(buffer).strip()
            elif current_section == "issues":
                result["issues"] = [b.lstrip("- •").strip() for b in buffer if b.strip() and b.strip() != "N/A"]
            buffer = []
            current_section = "feedback"
            continue
        
        if upper.startswith("DADOS_IGNORADOS:") or upper.startswith("DADOS IGNORADOS:"):
            if current_section == "feedback":
                result["feedback"] = "\n".join(buffer).strip()
            elif current_section == "analysis":
                result["analysis"] = "\n".join(buffer).strip()
            elif current_section == "issues":
                result["issues"] = [b.lstrip("- •").strip() for b in buffer if b.strip() and b.strip() != "N/A"]
            buffer = []
            current_section = "ignored_data"
            continue
        
        # Accumulate content
        if current_section:
            buffer.append(stripped)
    
    # Flush last section
    if current_section == "analysis":
        result["analysis"] = "\n".join(buffer).strip()
    elif current_section == "issues":
        result["issues"] = [b.lstrip("- •").strip() for b in buffer if b.strip() and b.strip() != "N/A"]
    elif current_section == "feedback":
        result["feedback"] = "\n".join(buffer).strip()
    elif current_section == "ignored_data":
        result["ignored_data"] = [b.lstrip("- •").strip() for b in buffer if b.strip() and b.strip() != "N/A"]
    
    # Clean up N/A feedback
    if result["feedback"].upper().strip() in ("N/A", "NA", ""):
        result["feedback"] = ""
    
    return result


# =============================================
# Verification Checklist Items
# =============================================

class ChecklistItem:
    """A single checklist verification item."""
    
    def __init__(self, item_id: str, category: str, description: str, 
                 severity: str = "WARNING", required: bool = True):
        self.item_id = item_id
        self.category = category
        self.description = description
        self.severity = severity  # CRITICAL, WARNING, INFO
        self.required = required
        self.passed = False
        self.details = ""
        self.adjustment = None  # Optional risk adjustment
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "category": self.category,
            "description": self.description,
            "severity": self.severity,
            "required": self.required,
            "passed": self.passed,
            "details": self.details,
            "adjustment": self.adjustment,
        }


class VerificationReport:
    """Complete verification report from the CAOS Verifier."""
    
    def __init__(self):
        self.items: list[ChecklistItem] = []
        self.overall_passed = True
        self.score = 1.0  # 0.0 to 1.0
        self.issues: list[str] = []
        self.risk_adjustments: dict[str, float] = {}
        self.recommendations: list[str] = []
    
    def add_item(self, item: ChecklistItem):
        self.items.append(item)
        if not item.passed:
            if item.severity == "CRITICAL":
                self.overall_passed = False
                self.score -= 0.15
            elif item.severity == "WARNING":
                self.score -= 0.08
            else:
                self.score -= 0.03
            self.issues.append(f"[{item.severity}] {item.item_id}: {item.details}")
        self.score = max(0.0, self.score)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_passed": self.overall_passed,
            "score": round(self.score, 3),
            "total_checks": len(self.items),
            "passed_checks": sum(1 for i in self.items if i.passed),
            "failed_checks": sum(1 for i in self.items if not i.passed),
            "issues": self.issues,
            "risk_adjustments": self.risk_adjustments,
            "recommendations": self.recommendations,
            "checklist": [i.to_dict() for i in self.items],
        }


# =============================================
# Verification Functions
# =============================================

def verify_data_sources(state: JudgeState, report: VerificationReport):
    """CHECK 1: Were all available data sources consulted?"""
    atlas = state.get("atlas_context") or {}
    llm_analysis = state.get("llm_analysis") or {}
    
    # 1.1 Atlas Digital Twin consulted
    item = ChecklistItem("DATA_001", "Fontes de Dados", 
                         "Atlas Digital Twin foi consultado", "CRITICAL")
    if atlas:
        item.passed = True
        item.details = f"Atlas disponível com {len(atlas)} campos"
    else:
        item.details = "Atlas não disponível — decisão baseada apenas no trigger"
    report.add_item(item)
    
    # 1.2 Telemetria estendida analisada
    item = ChecklistItem("DATA_002", "Fontes de Dados",
                         "Telemetria estendida do equipamento foi analisada", "WARNING")
    sim_data = atlas.get("simulation_data", {})
    if sim_data:
        item.passed = True
        item.details = f"Telemetria com {len(sim_data)} indicadores analisados"
    else:
        item.details = "Sem telemetria estendida — análise limitada a dados básicos"
    report.add_item(item)
    
    # 1.3 Manual técnico consultado
    item = ChecklistItem("DATA_003", "Fontes de Dados",
                         "Manual técnico / documentação RAG consultado", "WARNING")
    manuals = atlas.get("manual_excerpts", [])
    if manuals:
        item.passed = True
        item.details = f"{len(manuals)} trechos de manual consultados"
    else:
        item.details = "Nenhum trecho de manual disponível"
    report.add_item(item)
    
    # 1.4 Dados financeiros considerados
    item = ChecklistItem("DATA_004", "Fontes de Dados",
                         "Dados financeiros do ativo foram considerados", "WARNING")
    fin = atlas.get("financial_data", {})
    if fin:
        item.passed = True
        prod_val = fin.get("estimated_product_value_brl", 0)
        item.details = f"Valor dos produtos: R${prod_val:,.2f}"
    else:
        item.details = "Sem dados financeiros — risco financeiro pode estar impreciso"
    report.add_item(item)
    
    # 1.5 Contrato e SLA verificados
    item = ChecklistItem("DATA_005", "Fontes de Dados",
                         "Contrato e SLA do cliente foram verificados", "WARNING")
    if atlas.get("contract_tier"):
        item.passed = True
        item.details = f"Contrato: {atlas['contract_tier']}, SLA: {atlas.get('sla_response_time_minutes', 'N/A')}min"
    else:
        item.details = "Sem informação contratual — risco contratual pode estar impreciso"
    report.add_item(item)
    
    # 1.6 Oracle ML prediction
    item = ChecklistItem("DATA_006", "Fontes de Dados",
                         "Predição ML do Oracle foi considerada", "INFO")
    oracle = state.get("oracle_forecast")
    if oracle:
        item.passed = True
        item.details = f"Oracle ML: confiança={oracle.get('confidence', 'N/A')}"
    elif state.get("is_fast_track"):
        item.passed = True
        item.details = "Oracle ML bypass (fast-track justificado)"
    else:
        item.details = "Oracle ML indisponível"
    report.add_item(item)


def verify_risk_assessment(state: JudgeState, report: VerificationReport,
                           thresholds: dict | None = None):
    """CHECK 2: Are risk dimensions properly justified?"""
    thresholds = thresholds or _DEFAULT_CHECKLIST_THRESHOLDS
    t_low = thresholds["risk_low"]
    t_high = thresholds["risk_high"]
    t_mismatch = thresholds.get("severity_mismatch_low", 0.5)
    risk_dims = state.get("risk_dimensions")
    llm_analysis = state.get("llm_analysis") or {}
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}
    risk_justs = llm_analysis.get("risk_justifications", {})
    
    if not risk_dims:
        item = ChecklistItem("RISK_000", "Avaliação de Risco",
                             "Risk dimensions calculados", "CRITICAL")
        item.details = "Risk dimensions ausentes — falha crítica no Oracle"
        report.add_item(item)
        return
    
    # 2.1 Physical risk justified
    item = ChecklistItem("RISK_001", "Avaliação de Risco",
                         "Risco Físico (R_F) justificado com dados", "CRITICAL")
    if "physical" in risk_justs and risk_justs["physical"].strip():
        item.passed = True
        item.details = f"R_F={risk_dims.physical:.2f} — {risk_justs['physical'][:100]}"
    elif risk_dims.physical < t_low:
        item.passed = True  # Low risk doesn't need deep justification
        item.details = f"R_F={risk_dims.physical:.2f} — risco baixo (justificativa implícita)"
    else:
        item.details = f"R_F={risk_dims.physical:.2f} — ALTO mas sem justificativa textual"
    report.add_item(item)
    
    # 2.2 Financial risk justified
    item = ChecklistItem("RISK_002", "Avaliação de Risco",
                         "Risco Financeiro (R_Fin) justificado com valores em R$", "WARNING")
    if "financial" in risk_justs and ("R$" in risk_justs["financial"] or "reais" in risk_justs["financial"].lower()):
        item.passed = True
        item.details = f"R_Fin={risk_dims.financial:.2f} — justificativa com valores monetários"
    elif risk_dims.financial < 0.2:
        item.passed = True
        item.details = f"R_Fin={risk_dims.financial:.2f} — risco financeiro baixo"
    else:
        item.details = f"R_Fin={risk_dims.financial:.2f} — sem valores monetários na justificativa"
    report.add_item(item)
    
    # 2.3 Contractual risk justified
    item = ChecklistItem("RISK_003", "Avaliação de Risco",
                         "Risco Contratual (R_C) coherente com SLA e contrato", "WARNING")
    contract_tier = atlas.get("contract_tier", "")
    if contract_tier == "MONITORING_ONLY" and risk_dims.contractual < 0.5:
        # MONITORING_ONLY should have high contractual risk for intervention
        item.details = (f"R_C={risk_dims.contractual:.2f} — contrato MONITORING_ONLY mas risco contratual BAIXO. "
                       "Qualquer ação interventiva viola o contrato!")
        report.risk_adjustments["contractual_up"] = 0.8
        report.recommendations.append("R_C parece inconsistente com o tipo de contrato MONITORING_ONLY — Oracle não avaliou o impacto contratual de ações interventivas")
    elif "contractual" in risk_justs:
        item.passed = True
        item.details = f"R_C={risk_dims.contractual:.2f} — {risk_justs.get('contractual', '')[:80]}"
    else:
        item.passed = risk_dims.contractual < t_low
        item.details = f"R_C={risk_dims.contractual:.2f}"
    report.add_item(item)
    
    # 2.4 Communication risk justified
    item = ChecklistItem("RISK_004", "Avaliação de Risco",
                         "Risco de Comunicação (R_K) identifica stakeholders", "INFO")
    if "communication" in risk_justs:
        item.passed = True
        item.details = f"R_K={risk_dims.communication:.2f} — {risk_justs.get('communication', '')[:80]}"
    elif trigger.severity in [Severity.CRITICAL, Severity.HIGH] and risk_dims.communication < 0.2:
        item.details = f"R_K={risk_dims.communication:.2f} — severidade {trigger.severity.value} mas comunicação baixa"
        report.recommendations.append("Severidade alta mas Oracle não avaliou a necessidade de comunicação com stakeholders")
    else:
        item.passed = True
        item.details = f"R_K={risk_dims.communication:.2f}"
    report.add_item(item)
    
    # 2.5 Cross-validation: severity vs risk dimensions
    item = ChecklistItem("RISK_005", "Avaliação de Risco",
                         "Severidade do trigger coherente com riscos calculados", "WARNING")
    max_risk = max(risk_dims.physical, risk_dims.financial, risk_dims.contractual, risk_dims.communication)
    if trigger.severity == Severity.CRITICAL and max_risk < t_mismatch:
        item.details = (f"Severidade CRITICAL mas risco máximo={max_risk:.2f}. "
                       "Oracle pode estar subestimando riscos")
        report.recommendations.append("Inconsistência: severidade CRITICAL mas nenhum risco calculado acima de 0.5 — Oracle não justificou essa discrepância")
    elif trigger.severity == Severity.LOW and max_risk > t_high:
        item.details = (f"Severidade LOW mas risco máximo={max_risk:.2f}. "
                       "Oracle pode estar superestimando riscos")
        report.recommendations.append("Inconsistência: severidade LOW mas risco calculado alto — Oracle não explicou por que os riscos são maiores que a severidade indicada")
    else:
        item.passed = True
        item.details = f"Severidade {trigger.severity.value} coherente com risco máximo {max_risk:.2f}"
    report.add_item(item)


def verify_action_appropriateness(state: JudgeState, report: VerificationReport,
                                   thresholds: dict[str, float] | None = None):
    """CHECK 3: Is the proposed action appropriate?"""
    if thresholds is None:
        thresholds = _DEFAULT_CHECKLIST_THRESHOLDS
    t_high = thresholds["risk_high"]
    llm_analysis = state.get("llm_analysis") or {}
    atlas = state.get("atlas_context") or {}
    trigger = state["trigger"]
    suggested_action = llm_analysis.get("action", "").strip().lower()
    risk_dims = state.get("risk_dimensions")
    
    # 3.1 Action is within allowed actions
    item = ChecklistItem("ACT_001", "Ação Proposta",
                         "Ação proposta está dentro das ações permitidas pelo contrato", "CRITICAL")
    allowed = [a.lower() for a in atlas.get("allowed_actions", [])]
    if not suggested_action:
        item.passed = True
        item.details = "LLM não sugeriu ação (modo código)"
    elif not allowed:
        item.passed = True
        item.details = "Sem restrição de ações no contrato"
    elif suggested_action in allowed or suggested_action in ["observe", "read", "notification"]:
        item.passed = True
        item.details = f"Ação '{suggested_action}' permitida no contrato"
    else:
        item.details = (f"Ação '{suggested_action}' NÃO está em ações permitidas: {allowed}. "
                       "Risco contratual deve ser elevado!")
        report.risk_adjustments["contractual_action_violation"] = 0.9
        report.recommendations.append(f"Ação '{suggested_action}' não está nas ações permitidas pelo contrato — Oracle não verificou as restrições contratuais antes de propor a ação")
    report.add_item(item)
    
    # 3.2 Shutdown only for confirmed critical failures
    item = ChecklistItem("ACT_002", "Ação Proposta",
                         "Shutdown proposto apenas para falhas críticas confirmadas", "CRITICAL")
    if suggested_action == "shutdown":
        if trigger.severity == Severity.CRITICAL and risk_dims and risk_dims.physical > thresholds["risk_critical"]:
            item.passed = True
            item.details = f"Shutdown justificado: severidade CRITICAL + R_F={risk_dims.physical:.2f}"
        else:
            rf_str = f"{risk_dims.physical:.2f}" if risk_dims else "N/A"
            item.details = (f"Shutdown proposto mas severidade={trigger.severity.value}, "
                          f"R_F={rf_str}. "
                          "Shutdown deve ser reservado para emergências reais")
            report.recommendations.append("Oracle propôs shutdown sem evidência suficiente — a análise não demonstrou por que esta situação exige a ação mais extrema")
    else:
        item.passed = True
        item.details = f"Ação '{suggested_action}' — não é shutdown"
    report.add_item(item)
    
    # 3.3 Observe only when situation is self-resolving
    item = ChecklistItem("ACT_003", "Ação Proposta",
                         "Ação 'observe' usada quando a situação pode se auto-resolver", "WARNING")
    if suggested_action == "observe":
        if risk_dims and risk_dims.physical > t_high:
            item.details = (f"Observe proposto mas R_F={risk_dims.physical:.2f}. "
                          "Risco físico alto exige ação imediata, não observação!")
            report.recommendations.append("Oracle não justificou por que propõe observação passiva quando o risco físico calculado é alto")
        else:
            item.passed = True
            item.details = "Observe apropriado para situação de risco baixo/moderado"
    else:
        item.passed = True
        item.details = f"Ação '{suggested_action}' — não é observe"
    report.add_item(item)
    
    # 3.4 Maintenance status respected
    item = ChecklistItem("ACT_004", "Ação Proposta",
                         "Status de manutenção (LOTO) foi respeitado", "CRITICAL")
    under_maintenance = atlas.get("under_maintenance", False)
    if under_maintenance:
        if suggested_action in ["shutdown", "setpoint", "maintenance"]:
            item.details = (f"Ativo está em MANUTENÇÃO (LOTO) mas ação '{suggested_action}' foi proposta. "
                          "LOTO exige que somente 'notification' ou 'ticket' sejam executados!")
            report.risk_adjustments["loto_violation"] = -1.0
        else:
            item.passed = True
            item.details = "LOTO ativo — ação segura proposta"
    else:
        item.passed = True
        item.details = "Ativo não está em manutenção"
    report.add_item(item)


def verify_reasoning_completeness(state: JudgeState, report: VerificationReport):
    """CHECK 4: Is the reasoning chain complete?"""
    llm_analysis = state.get("llm_analysis") or {}
    reasoning_trace = state.get("reasoning_trace", [])
    
    # 4.1 Chain of thought present
    item = ChecklistItem("REASON_001", "Raciocínio",
                         "Cadeia de pensamento (chain-of-thought) está presente", "WARNING")
    thought = llm_analysis.get("thought", "")
    if thought and len(thought) > 50:
        item.passed = True
        item.details = f"Pensamento com {len(thought)} caracteres"
    elif not llm_analysis:
        item.passed = True
        item.details = "Modo código (sem LLM) — raciocínio via heurísticas"
    else:
        item.details = "Cadeia de pensamento ausente ou muito curta"
    report.add_item(item)
    
    # 4.2 Hypotheses were generated and tested
    item = ChecklistItem("REASON_002", "Raciocínio",
                         "Múltiplas hipóteses foram levantadas e testadas", "WARNING")
    hypotheses = llm_analysis.get("hypotheses_discarded", [])
    thought_text = llm_analysis.get("thought", "").lower()
    has_hypotheses = (
        len(hypotheses) > 0 or
        "hipótese" in thought_text or
        "hipotese" in thought_text or
        "descartar" in thought_text or
        "confirmar" in thought_text
    )
    if has_hypotheses:
        item.passed = True
        if hypotheses:
            item.details = f"{len(hypotheses)} hipóteses descartadas documentadas"
        else:
            item.details = "Hipóteses mencionadas no pensamento"
    elif not llm_analysis:
        item.passed = True
        item.details = "Modo código — hipóteses via heurísticas"
    else:
        item.details = "Nenhuma hipótese alternativa foi documentada"
        report.recommendations.append("Oracle deve levantar ≥3 hipóteses e testar cada uma contra os dados")
    report.add_item(item)
    
    # 4.3 Root cause identified
    item = ChecklistItem("REASON_003", "Raciocínio",
                         "Causa raiz foi identificada (não apenas o sintoma)", "CRITICAL")
    analysis = llm_analysis.get("analysis", "").lower()
    justification = llm_analysis.get("justification", "").lower()
    has_root_cause = any(kw in analysis + justification for kw in [
        "causa raiz", "causa principal", "causa real", "raiz do problema",
        "causa operacional", "causa técnica", "causa ambiental",
        "os dados confirmam", "evidência indica",
    ])
    if has_root_cause:
        item.passed = True
        item.details = "Causa raiz identificada na análise"
    elif not llm_analysis:
        item.passed = True
        item.details = "Modo código — causa inferida via severidade/métrica"
    else:
        item.details = "Análise pode estar tratando SINTOMA e não CAUSA RAIZ"
        report.recommendations.append("Oracle deve distinguir claramente sintoma (alerta) de causa raiz")
    report.add_item(item)
    
    # 4.4 Justification present and substantive
    item = ChecklistItem("REASON_004", "Raciocínio",
                         "Justificativa detalhada está presente", "WARNING")
    justification = llm_analysis.get("justification", "")
    if justification and len(justification) > 30:
        item.passed = True
        item.details = f"Justificativa com {len(justification)} caracteres"
    elif not llm_analysis:
        item.passed = True
        item.details = "Modo código — justificativa via formula de risco"
    else:
        item.details = "Justificativa ausente ou muito curta"
    report.add_item(item)
    
    # 4.5 Data sources were explicitly listed
    item = ChecklistItem("REASON_005", "Raciocínio",
                         "Fontes de dados consultadas foram listadas", "INFO")
    data_sources = llm_analysis.get("data_sources_consulted", [])
    if data_sources and len(data_sources) > 0:
        item.passed = True
        item.details = f"{len(data_sources)} fontes de dados listadas"
    elif not llm_analysis:
        item.passed = True
        item.details = "Modo código"
    else:
        item.details = "Oracle não listou explicitamente as fontes de dados consultadas"
    report.add_item(item)


def verify_safety_constraints(state: JudgeState, report: VerificationReport,
                               thresholds: dict[str, float] | None = None):
    """CHECK 5: Are safety constraints respected?"""
    if thresholds is None:
        thresholds = _DEFAULT_CHECKLIST_THRESHOLDS
    t_critical = thresholds["risk_critical"]
    t_low = thresholds["risk_low"]
    t_verdict_high = thresholds["verdict_high"]
    risk_dims = state.get("risk_dimensions")
    verdict = state.get("verdict_score", 0.0)
    atlas = state.get("atlas_context") or {}
    trigger = state["trigger"]
    
    # 5.1 High physical risk → conservative action
    item = ChecklistItem("SAFE_001", "Segurança",
                         "Risco físico alto → ação conservadora", "CRITICAL")
    if risk_dims and risk_dims.physical > t_critical:
        band = state.get("decision_band")
        if band in [DecisionBand.EXECUTE]:
            item.details = (f"R_F={risk_dims.physical:.2f} (ALTO) mas decisão é EXECUTE. "
                          "Risco físico alto deveria ter HITL (SUGGEST ou superior)")
            report.risk_adjustments["force_hitl"] = True
            report.recommendations.append("R_F > 0.8 deve SEMPRE requerer aprovação humana")
        else:
            item.passed = True
            item.details = f"R_F={risk_dims.physical:.2f} — banda {band.value if band else 'N/A'} é conservadora"
    else:
        item.passed = True
        item.details = f"R_F={risk_dims.physical:.2f} — dentro do normal" if risk_dims else "R_F=N/A — dentro do normal"
    report.add_item(item)
    
    # 5.2 Verdict coherence
    item = ChecklistItem("SAFE_002", "Segurança",
                         "Verdict coherente com dimensões de risco", "WARNING")
    if risk_dims:
        max_risk = max(risk_dims.physical, risk_dims.financial, 
                      risk_dims.contractual, risk_dims.communication)
        if verdict > t_verdict_high and max_risk > t_critical:
            item.details = (f"Verdict={verdict:.3f} (alto) mas risco máximo={max_risk:.2f}. "
                          "Possível inconsistência no cálculo")
        elif verdict < 0 and max_risk < t_low:
            item.details = (f"Verdict={verdict:.3f} (bloqueado) mas riscos são todos baixos. "
                          "Oracle pode estar sendo excessivamente conservador")
        else:
            item.passed = True
            item.details = f"Verdict={verdict:.3f} coherente com risco máximo={max_risk:.2f}"
    else:
        item.details = "Risk dimensions ausentes"
    report.add_item(item)
    
    # 5.3 Temperature safety margin
    item = ChecklistItem("SAFE_003", "Segurança",
                         "Margem de segurança térmica verificada", "WARNING")
    max_temp = atlas.get("max_operating_temp")
    if max_temp and trigger.value is not None and isinstance(trigger.value, (int, float)):
        metric = (trigger.metric or "").lower()
        if "temp" in metric:
            margin = trigger.value / max_temp if max_temp != 0 else 0
            if margin > 1.5 and risk_dims and risk_dims.physical < 0.8:
                item.details = (f"Temperatura a {margin:.0%} do limite mas R_F={risk_dims.physical:.2f}. "
                              "Risco físico pode estar subestimado!")
                report.risk_adjustments["physical_underestimate"] = min(1.0, margin * 0.6)
            else:
                item.passed = True
                item.details = f"Temperatura a {margin:.0%} do limite operacional"
        else:
            item.passed = True
            item.details = "Métrica não é temperatura"
    else:
        item.passed = True
        item.details = "Sem dados de temperatura para verificar"
    report.add_item(item)


def verify_temporal_patterns(state: JudgeState, report: VerificationReport):
    """CHECK 6: Were temporal patterns analyzed?"""
    atlas = state.get("atlas_context") or {}
    llm_analysis = state.get("llm_analysis") or {}
    
    # 6.1 Door events pattern (if applicable)
    sim = atlas.get("simulation_data", {})
    item = ChecklistItem("TEMP_001", "Padrões Temporais",
                         "Padrão de abertura de portas foi analisado", "WARNING")
    door_events = sim.get("door_open_events_last_hour", 0)
    if isinstance(door_events, (int, float)) and door_events > 0:
        thought = llm_analysis.get("thought", "").lower()
        analysis = llm_analysis.get("analysis", "").lower()
        door_mentioned = any(kw in thought + analysis for kw in [
            "porta", "door", "abertura", "pico", "operacional"
        ])
        if door_mentioned:
            item.passed = True
            item.details = f"{door_events} aberturas/hora — analisadas pelo Oracle"
        else:
            item.details = f"{door_events} aberturas/hora MAS Oracle não mencionou portas na análise"
            report.recommendations.append(f"Aberturas de porta ({door_events}/hora) podem ser a causa raiz — Oracle ignorou")
    else:
        item.passed = True
        item.details = "Sem dados de abertura de porta"
    report.add_item(item)
    
    # 6.2 Power cycle history
    item = ChecklistItem("TEMP_002", "Padrões Temporais",
                         "Histórico de ciclos liga/desliga foi verificado", "INFO")
    power_history = sim.get("power_cycle_history", [])
    if power_history and len(power_history) >= 2:
        thought = llm_analysis.get("thought", "").lower()
        power_mentioned = any(kw in thought for kw in [
            "desliga", "liga", "ciclo", "noite", "power"
        ])
        if power_mentioned or not llm_analysis:
            item.passed = True
            item.details = f"{len(power_history)} ciclos detectados — verificados"
        else:
            item.details = f"{len(power_history)} ciclos de liga/desliga NÃO foram mencionados"
            report.recommendations.append("Padrão de desligamentos pode indicar operação irregular")
    else:
        item.passed = True
        item.details = "Sem histórico de ciclos liga/desliga"
    report.add_item(item)

    # 6.3 Sensor contradiction
    item = ChecklistItem("TEMP_003", "Padrões Temporais",
                         "Contradições entre sensores foram verificadas", "WARNING")
    primary = sim.get("primary_temp_sensor_c")
    secondary = sim.get("secondary_temp_probe_c")
    if primary is not None and secondary is not None:
        delta = abs(primary - secondary)
        if delta > 10:
            thought = llm_analysis.get("thought", "").lower()
            sensor_mentioned = any(kw in thought for kw in [
                "sensor", "sonda", "contradição", "divergência", "calibr"
            ])
            if sensor_mentioned or not llm_analysis:
                item.passed = True
                item.details = f"Δ sensores={delta:.1f}°C — contradição analisada"
            else:
                item.details = f"Δ sensores={delta:.1f}°C — contradição NÃO foi analisada pelo Oracle"
                report.recommendations.append(f"Diferença de {delta:.1f}°C entre sensores pode indicar falha de sensor")
        else:
            item.passed = True
            item.details = f"Sensores concordam (Δ={delta:.1f}°C)"
    else:
        item.passed = True
        item.details = "Sem múltiplos sensores para comparar"
    report.add_item(item)


def verify_external_factors(state: JudgeState, report: VerificationReport):
    """CHECK 7: Were external factors considered?"""
    reasoning_trace = state.get("reasoning_trace", [])
    llm_analysis = state.get("llm_analysis") or {}
    
    # 7.1 Weather data requested when relevant
    item = ChecklistItem("EXT_001", "Fatores Externos",
                         "Dados climáticos foram considerados quando relevante", "INFO")
    weather_requested = any("CLIMA" in str(t) or "clima" in str(t).lower() for t in reasoning_trace)
    thought = llm_analysis.get("thought", "").lower()
    weather_in_thought = any(kw in thought for kw in [
        "clima", "weather", "temperatura ambiente", "calor", "frio externo"
    ])
    if weather_requested:
        item.passed = True
        item.details = "Dados climáticos foram solicitados e integrados"
    elif weather_in_thought:
        item.passed = True
        item.details = "Fatores climáticos mencionados na análise"
    else:
        item.passed = True  # Not always needed
        item.details = "Sem solicitação de dados climáticos (pode não ser relevante)"
    report.add_item(item)


def verify_verdict_formula(state: JudgeState, report: VerificationReport,
                           thresholds: dict[str, float] | None = None):
    """CHECK 8: Is the verdict formula correctly applied?"""
    if thresholds is None:
        thresholds = _DEFAULT_CHECKLIST_THRESHOLDS
    verdict = state.get("verdict_score")
    band = state.get("decision_band")
    risk_level = state.get("risk_level")
    
    # 8.1 Verdict exists
    item = ChecklistItem("VERD_001", "Fórmula de Decisão",
                         "Verdict score foi calculado", "CRITICAL")
    if verdict is not None:
        item.passed = True
        item.details = f"V={verdict:.3f}"
    else:
        item.details = "Verdict score ausente"
    report.add_item(item)
    
    # 8.2 Decision band coherent with verdict
    item = ChecklistItem("VERD_002", "Fórmula de Decisão",
                         "Banda de decisão coherente com verdict score", "CRITICAL")
    if verdict is not None and band is not None:
        # Use tenant-aware thresholds for band validation
        dt = thresholds
        t_blocked = 0.0
        t_alert = dt.get("risk_low", 0.3)
        t_suggest = dt.get("risk_high", 0.7)
        
        expected_band = None
        if verdict <= t_blocked:
            expected_band = DecisionBand.BLOCKED
        elif verdict <= t_alert:
            expected_band = DecisionBand.ALERT
        elif verdict <= t_suggest:
            expected_band = DecisionBand.SUGGEST
        else:
            expected_band = DecisionBand.EXECUTE
        
        if band == expected_band:
            item.passed = True
            item.details = f"V={verdict:.3f} → {band.value} (correto)"
        else:
            item.details = f"V={verdict:.3f} deveria ser {expected_band.value} mas é {band.value}"
    else:
        item.details = "Verdict ou banda ausentes"
    report.add_item(item)
    
    # 8.3 Risk level coherent
    item = ChecklistItem("VERD_003", "Fórmula de Decisão",
                         "Nível de risco coherente com análise", "WARNING")
    if risk_level:
        item.passed = True
        item.details = f"Risk Level: {risk_level.value}"
    else:
        item.details = "Risk level ausente"
    report.add_item(item)


# =============================================
# Main CAOS Verifier Node
# =============================================

async def cortex_node(state: JudgeState) -> dict[str, Any]:
    """CAOS Verifier Node: LLM Deep Audit + Rule-Based Checklist.
    
    Dual-layer audit of the Oracle's decision:
    
    Layer A — Rule-Based Checklist (always runs):
      30 structured checks across 8 categories.
      Produces a deterministic score and identifies specific issues.
    
    Layer B — LLM Deep Audit (when available):
      A CAOS LLM THINKS about the Oracle's decision holistically.
      Can APPROVE or REJECT with structured feedback for retry.
    
    When CAOS rejects and verifier_retry_count < 1:
      → Generates specific feedback instructions for Oracle
      → Brain routes back to Oracle for retry with the feedback
    """
    # Short-circuit if blocked
    if state.get("sense_blocked"):
        return {}
    
    trigger = state["trigger"]
    verifier_retry_count = state.get("verifier_retry_count", 0)
    logger.info("caos_verifier_start", event_id=trigger.event_id, retry=verifier_retry_count)
    
    # =============================================
    # LAYER A: Rule-Based Checklist (deterministic)
    # =============================================
    report = VerificationReport()
    tenant_id = trigger.context.tenant_id if trigger.context else None
    thresholds = get_checklist_thresholds(tenant_id)
    
    verify_data_sources(state, report)
    verify_risk_assessment(state, report, thresholds)
    verify_action_appropriateness(state, report, thresholds)
    verify_reasoning_completeness(state, report)
    verify_safety_constraints(state, report, thresholds)
    verify_temporal_patterns(state, report)
    verify_external_factors(state, report)
    verify_verdict_formula(state, report, thresholds)
    
    checklist_report = report.to_dict()
    
    # =============================================
    # LAYER B: LLM Deep Audit (CAOS thinks)
    # =============================================
    from caos.core.nodes.oracle import get_llm
    from caos.safety.runtime import get_circuit_breaker
    
    llm = get_llm()
    cb = get_circuit_breaker()
    if cb.is_tripped():
        llm = None
        logger.warning("caos_verifier_circuit_breaker_tripped")
    
    caos_llm_verdict = None
    caos_llm_analysis = ""
    caos_llm_feedback = ""
    caos_llm_issues = []
    caos_llm_score = report.score  # Default to checklist score
    
    if llm is not None:
        try:
            context_prompt = build_verifier_context(state, checklist_report)
            
            messages = [
                SystemMessage(content=CAOS_VERIFIER_SYSTEM_PROMPT),
                HumanMessage(content=context_prompt),
            ]
            
            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                logger.warning("caos_verifier_llm_timeout", timeout=120.0)
                raise TimeoutError("CAOS LLM timeout >120s")
            
            # Record LLM token usage for circuit breaker budget tracking
            from caos.core.nodes.oracle import _record_llm_usage
            _record_llm_usage(cb, response, "caos_verifier")
            
            response_text = response.content
            if response_text and response_text.strip():
                parsed = parse_verifier_response(response_text)
                
                caos_llm_verdict = parsed["verdict"]
                caos_llm_analysis = parsed["analysis"]
                caos_llm_feedback = parsed["feedback"]
                caos_llm_issues = parsed["issues"]
                caos_llm_score = parsed["score"]
                
                logger.info(
                    "caos_verifier_llm_complete",
                    verdict=caos_llm_verdict,
                    confidence=parsed["confidence"],
                    score=caos_llm_score,
                    has_feedback=bool(caos_llm_feedback),
                    issues_count=len(caos_llm_issues),
                )
            else:
                logger.warning("caos_verifier_llm_empty_response")
                
        except Exception as e:
            logger.warning("caos_verifier_llm_fallback", error=str(e))
            caos_llm_verdict = None
    else:
        logger.info("caos_verifier_llm_not_available")
    
    # =============================================
    # COMBINE: Merge checklist + LLM results
    # =============================================
    
    # Determine final verdict
    # Priority: LLM verdict > checklist (LLM has deeper reasoning)
    # But if checklist found CRITICAL failures, those override
    if caos_llm_verdict is not None:
        # LLM was available — use its verdict as primary
        final_passed = caos_llm_verdict == "APROVADO"
        
        # But CRITICAL checklist failures always cause rejection
        if not report.overall_passed:
            final_passed = False
            if caos_llm_verdict == "APROVADO":
                caos_llm_analysis += "\n[OVERRIDE: Checklist encontrou falhas CRÍTICAS que impedem aprovação]"
        
        # Combine scores: weighted average (LLM 60%, checklist 40%)
        final_score = (caos_llm_score * 0.6) + (report.score * 0.4)
    else:
        # LLM not available — fallback to checklist only
        final_passed = report.overall_passed
        final_score = report.score
    
    # Merge issues from both layers
    all_issues = list(report.issues)  # Start with checklist issues
    for llm_issue in caos_llm_issues:
        if llm_issue and llm_issue not in all_issues:
            all_issues.append(f"[CAOS LLM] {llm_issue}")
    
    # =============================================
    # BUILD RESULT
    # =============================================
    result: dict[str, Any] = {
        "verification_report": {
            **checklist_report,
            "caos_llm": {
                "available": caos_llm_verdict is not None,
                "verdict": caos_llm_verdict,
                "analysis": caos_llm_analysis,
                "feedback": caos_llm_feedback,
                "issues": caos_llm_issues,
                "score": caos_llm_score,
            },
        },
        "verification_passed": final_passed,
        "verification_score": round(final_score, 3),
        "verification_issues": all_issues,
    }
    
    # === Generate feedback for Oracle if REJECTED and can retry ===
    if not final_passed and verifier_retry_count < 1:
        # Build structured feedback from LLM + checklist
        feedback_parts = []
        
        feedback_parts.append("## CAOS REJEITOU sua decisão. Corrija os seguintes problemas:\n")
        
        if caos_llm_feedback:
            feedback_parts.append("### Feedback do CAOS (análise profunda):")
            feedback_parts.append(caos_llm_feedback)
        
        if report.issues:
            feedback_parts.append("\n### Problemas do Checklist Automático:")
            for issue in report.issues:
                feedback_parts.append(f"  - {issue}")
        
        if report.recommendations:
            feedback_parts.append("\n### Lacunas Identificadas no Checklist:")
            for rec in report.recommendations:
                feedback_parts.append(f"  - {rec}")
        
        if caos_llm_issues:
            feedback_parts.append("\n### Problemas Adicionais Identificados pelo CAOS:")
            for issue in caos_llm_issues:
                feedback_parts.append(f"  - {issue}")
        
        # === GAP IDENTIFICATION (what the Oracle missed) ===
        # The Verifier is an AUDITOR — it points out gaps, NOT suggests actions.
        # The Oracle must figure out the right action on its own.
        llm_analysis = state.get("llm_analysis") or {}
        suggested_action = llm_analysis.get("action", "").strip().lower()
        risk_dims = state.get("risk_dimensions")
        
        feedback_parts.append("\n### 🔍 LACUNAS E INCONSISTÊNCIAS IDENTIFICADAS:")
        
        if risk_dims:
            max_risk = max(risk_dims.physical, risk_dims.financial,
                          risk_dims.contractual, risk_dims.communication)
            feedback_parts.append(f"  Riscos calculados: R_F={risk_dims.physical:.2f}, R_Fin={risk_dims.financial:.2f}, R_C={risk_dims.contractual:.2f}, R_K={risk_dims.communication:.2f}")
            
            # Point out inconsistencies between risk and action
            if risk_dims.physical > 0.7 and suggested_action in ("observe", "ticket", "notification"):
                feedback_parts.append(f"  ⚠️ INCONSISTÊNCIA: R_F={risk_dims.physical:.2f} indica risco físico ALTO, mas a ação proposta ('{suggested_action}') não reflete a gravidade desse risco. Oracle não explicou essa discrepância.")
            
            if risk_dims.contractual > 0.7 and suggested_action in ("observe", "ticket"):
                feedback_parts.append(f"  ⚠️ INCONSISTÊNCIA: R_C={risk_dims.contractual:.2f} indica risco contratual elevado (SLA), mas Oracle não justificou como a ação proposta ('{suggested_action}') atende ao nível de urgência exigido pelo contrato.")
            
            if max_risk > 0.6 and suggested_action == "observe":
                feedback_parts.append(f"  ⚠️ INCONSISTÊNCIA: Risco máximo={max_risk:.2f} mas ação é 'observe'. Oracle não apresentou evidência de que a situação pode se auto-resolver.")
            
            if trigger.severity in (Severity.CRITICAL, Severity.HIGH) and suggested_action in ("observe", "ticket"):
                feedback_parts.append(f"  ⚠️ INCONSISTÊNCIA: Severidade {trigger.severity.value} não está alinhada com a ação '{suggested_action}'. Oracle não justificou essa discrepância na análise de impacto.")
        
        feedback_parts.append("\n### INSTRUÇÕES: O CAOS identificou lacunas na sua análise.")
        feedback_parts.append("Reveja os dados e hipóteses que você não considerou.")
        feedback_parts.append("Refaça sua análise completa levando em conta as inconsistências apontadas acima.")
        
        result["verifier_feedback"] = "\n".join(feedback_parts)
        result["verifier_retry_count"] = verifier_retry_count  # Brain will increment
        
        logger.info(
            "caos_verifier_rejected_with_feedback",
            event_id=trigger.event_id,
            feedback_length=len(result["verifier_feedback"]),
        )
    else:
        result["verifier_feedback"] = None
        result["verifier_retry_count"] = verifier_retry_count
    
    # === Apply risk adjustments if critical issues found ===
    risk_dims = state.get("risk_dimensions")
    adjustments_made = {}
    
    if risk_dims and report.risk_adjustments:
        new_physical = risk_dims.physical
        new_financial = risk_dims.financial
        new_contractual = risk_dims.contractual
        new_communication = risk_dims.communication
        
        # Apply contractual adjustment for MONITORING_ONLY violation
        if "contractual_up" in report.risk_adjustments:
            new_val = report.risk_adjustments["contractual_up"]
            if new_contractual < new_val:
                adjustments_made["contractual"] = f"{new_contractual:.2f} → {new_val:.2f}"
                new_contractual = new_val
        
        # Apply contractual adjustment for action violation
        if "contractual_action_violation" in report.risk_adjustments:
            new_val = report.risk_adjustments["contractual_action_violation"]
            if new_contractual < new_val:
                adjustments_made["contractual_action"] = f"{new_contractual:.2f} → {new_val:.2f}"
                new_contractual = new_val
        
        # Apply physical underestimate correction
        if "physical_underestimate" in report.risk_adjustments:
            new_val = report.risk_adjustments["physical_underestimate"]
            if new_physical < new_val:
                adjustments_made["physical"] = f"{new_physical:.2f} → {new_val:.2f}"
                new_physical = new_val
        
        # LOTO violation → force verdict to BLOCKED
        if "loto_violation" in report.risk_adjustments:
            adjustments_made["loto_block"] = "VETO"
            result["verdict_score"] = -1.0
            result["decision_band"] = DecisionBand.BLOCKED
            result["risk_level"] = RiskLevel.VETO
        
        # Force HITL for high physical risk
        if report.risk_adjustments.get("force_hitl"):
            adjustments_made["force_hitl"] = "SUGGEST"
            current_band = result.get("decision_band", state.get("decision_band"))
            if current_band == DecisionBand.EXECUTE:
                result["decision_band"] = DecisionBand.SUGGEST
                result["requires_human_approval"] = True
        
        if adjustments_made:
            result["verification_adjustments"] = adjustments_made
        else:
            result["verification_adjustments"] = None

        # Update risk dimensions if adjusted
        if any(k in adjustments_made for k in ["contractual", "contractual_action", "physical"]):
            adjusted_risk = RiskDimensions(
                physical=new_physical,
                financial=new_financial,
                contractual=new_contractual,
                communication=new_communication,
            )
            result["risk_dimensions"] = adjusted_risk
            
            # Recalculate verdict with adjusted risks
            if "loto_block" not in adjustments_made:
                tenant_id = trigger.context.tenant_id if trigger.context else None
                from caos.core.judge import JudgeEngine
                judge = JudgeEngine.from_rlhf(tenant_id=tenant_id)
                new_result = judge.judge(
                    atlas_score=state.get("atlas_score", 0.5),
                    oracle_score=state.get("oracle_score", 1.0),
                    risk_dimensions=adjusted_risk,
                    guardrail_penalty=0.0,
                )
                result["verdict_score"] = new_result.verdict_score
                result["decision_band"] = new_result.decision_band
                result["risk_level"] = new_result.risk_level
                result["severity_score"] = new_result.severity_score
    
    # =============================================
    # BUILD REASONING TRACE
    # =============================================
    verification_trace = []
    
    # Checklist summary
    verification_trace.append(
        f"✅ CAOS Checklist: {checklist_report['passed_checks']}/{checklist_report['total_checks']} "
        f"checks passaram (score={report.score:.2f})"
    )
    
    # LLM audit summary
    if caos_llm_verdict is not None:
        emoji = "✅" if caos_llm_verdict == "APROVADO" else "❌"
        verification_trace.append(
            f"{emoji} CAOS LLM: {caos_llm_verdict} (score={caos_llm_score:.2f})"
        )
        if caos_llm_analysis:
            # Truncate for trace but keep first 200 chars
            truncated = caos_llm_analysis[:200] + ("..." if len(caos_llm_analysis) > 200 else "")
            verification_trace.append(f"🧠 Análise CAOS: {truncated}")
    else:
        verification_trace.append("⚠️ CAOS LLM indisponível — auditoria somente por checklist")
    
    # Final combined verdict
    verdict_emoji = "✅" if final_passed else "❌"
    verification_trace.append(
        f"{verdict_emoji} VEREDICTO FINAL CAOS: {'APROVADO' if final_passed else 'REJEITADO'} "
        f"(score combinado={final_score:.2f})"
    )
    
    if all_issues:
        verification_trace.append("❌ Problemas encontrados:")
        for issue in all_issues[:5]:
            verification_trace.append(f"  • {issue}")
    
    if adjustments_made:
        verification_trace.append("🔧 Ajustes aplicados pelo CAOS:")
        for adj_key, adj_val in adjustments_made.items():
            verification_trace.append(f"  • {adj_key}: {adj_val}")
    
    if report.recommendations:
        verification_trace.append("💡 Recomendações do CAOS:")
        for rec in report.recommendations[:5]:
            verification_trace.append(f"  • {rec}")
    
    if not final_passed and verifier_retry_count < 1:
        verification_trace.append("♻️ CAOS enviará feedback ao Oracle para retry")
    
    # Append to existing reasoning trace
    existing_trace = state.get("reasoning_trace", [])
    result["reasoning_trace"] = existing_trace + verification_trace
    
    logger.info(
        "caos_verifier_complete",
        event_id=trigger.event_id,
        verification_passed=final_passed,
        verification_score=round(final_score, 3),
        checklist_score=round(report.score, 3),
        llm_verdict=caos_llm_verdict,
        llm_score=round(caos_llm_score, 3) if caos_llm_verdict else None,
        checks_passed=checklist_report["passed_checks"],
        checks_total=checklist_report["total_checks"],
        issues_count=len(all_issues),
        adjustments=adjustments_made or None,
        will_retry=not final_passed and verifier_retry_count < 1,
    )
    
    return result
