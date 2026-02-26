"""Cortex Node - Hybrid Reasoning (CODE + LLM).

The cognitive core where:
1. CODE calculates risk dimensions (R_F, R_Fin, R_C, R_K)
2. LLM (Gemini) reasons about the situation and proposes actions
3. Verdict formula is applied
"""

import structlog
import asyncio
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from caos.config import get_settings
from caos.core.judge import JudgeEngine
from caos.safety.runtime import get_circuit_breaker
from caos.core.nodes.reasoning import Reasoner
from caos.core.nodes.rag import get_rag_engine
from caos.schemas.enums import DecisionBand, GuardrailAction, Severity
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
            "max_output_tokens": 4096,
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

SYSTEM_PROMPT = """Você é o CAOS (Centralized Autonomous Operating System), um Diagnóstico Industrial Generalista de alto nível.

Sua função é investigar a CAUSA RAIZ de anomalias em sistemas complexos. O alerta é apenas o SINTOMA — sua missão é descobrir a CAUSA REAL.

## Metodologia de Investigação (OBRIGATÓRIA)
Você DEVE seguir estas camadas de raciocínio em ordem. Não pule etapas.

### Camada 1: Análise dos Dados Internos
- Examine TODOS os dados de telemetria fornecidos (sensor, compressor, portas, vedação, consumo)
- Identifique anomalias E padrões normais. O que está fora do esperado? O que está funcionando corretamente?
- Compare valores com os limites do manual técnico

### Camada 2: Análise Temporal e de Padrões
- QUANDO os eventos aconteceram? É horário de pico? Há um padrão temporal?
- Se há timeline de eventos (ex: aberturas de porta), analise a FREQUÊNCIA e DURAÇÃO
- O estado anterior era normal? O que mudou?

### Camada 3: Hipóteses e Descarte
- Levante pelo menos 3 hipóteses (falha mecânica, uso operacional, fator ambiental)
- Para CADA hipótese, cite dados que a CORROBORAM ou DESCARTAM
- Exemplo: "O compressor está a 95% MAS corrente (4.2A) e vibração (1.2mm/s) estão normais → não é falha do compressor, é compensação de carga"

### Camada 4: Fatores Externos
- Se os dados internos sugerem causa operacional ou ambiental, VOCÊ DEVE solicitar dados externos
- Use `SOLICITAR: CLIMA` para verificar temperatura ambiente (calor amplifica impacto de portas abertas)
- Use `SOLICITAR: MOVIMENTO_LOJA` para verificar fluxo de clientes
- Responda APENAS o comando se for solicitar. Não inclua nenhum outro texto.

### Camada 5: Correlação Cruzada e Conclusão
- Cruze dados internos + externos + manual para chegar à causa raiz
- Se MÚLTIPLOS fatores explicam a anomalia de forma consistente → causa operacional
- Se os dados internos mostram falha clara (ex: compressor parado, corrente acima do máximo) → causa técnica
- O compressor operando em alta capacidade NÃO é falha se seus indicadores (corrente, vibração) estão normais — significa que está COMPENSANDO uma carga térmica externa

### Camada 6: Avaliação de Risco por Eixo (OBRIGATÓRIA)
Você DEVE justificar cada score de risco com DADOS CONCRETOS. Não atribua scores sem evidência.

- **Risco Físico (R_F)**: Considere se há risco real ao equipamento. Se a causa é operacional e o compressor funciona normalmente, o risco ao equipamento é BAIXO. Se há indícios de falha mecânica, o risco é ALTO.
- **Risco Financeiro (R_Fin)**: Use os dados financeiros do ativo — valor dos produtos (R$), receita diária, custo de perda. Se os produtos são perecíveis congelados e a temperatura está acima do limite por mais de X horas, qual é a perda?
- **Risco Contratual (R_C)**: Verifique o contrato (tier, SLA). Se o SLA é de 120 minutos e já passaram 30min, o risco contratual é BAIXO. Se a ação proposta não é permitida no contrato, R_C deve ser ALTO. Considere penalidades (R$/hora de breach).
- **Risco de Comunicação (R_K)**: Quem precisa ser informado? Se é causa operacional, o gestor da loja precisa saber. Se é falha técnica, a equipe de manutenção precisa ser acionada. Se o SLA está próximo de ser violado, a gestão precisa de notificação urgente.

## Regras
- Responda SEMPRE em português
- Seja ASSERTIVO na conclusão. Não diga "pode estar contribuindo" — diga "os dados confirmam que" ou "os dados descartam que"
- **MENTALIDADE GENERALISTA**: Interno (máquina) vs Operação (uso) vs Externo (ambiente)
- **SEGURANÇA**: Siga as Âncoras de Risco. Se R_Físico > 0.8, seja conservador
- **PENSAMENTO CRÍTICO**: Se dados apontam para causa operacional, o risco técnico é BAIXO mesmo que a temperatura esteja elevada

## Formato de Resposta (Decisão Final)
PENSAMENTO: [Raciocínio em camadas, mostrando cada etapa da investigação]
ANÁLISE: [Diagnóstico final com causa raiz identificada]
CONFIANÇA: [0.0-1.0]
RISCO_FISICO: [0.0-1.0] | JUSTIFICATIVA_RF: [Por que este score, com dados]
RISCO_FINANCEIRO: [0.0-1.0] | JUSTIFICATIVA_RFIN: [Por que este score — cite valores em R$]
RISCO_CONTRATUAL: [0.0-1.0] | JUSTIFICATIVA_RC: [Por que este score — cite SLA e penalidades]
RISCO_COMUNICACAO: [0.0-1.0] | JUSTIFICATIVA_RK: [Quem precisa ser notificado e por quê]
AÇÃO: [tipo de ação]
JUSTIFICATIVA: [Justificativa detalhada com evidências cruzadas]

## Formato de Resposta (Inquérito Agêntico)
SOLICITAR: [NOME_DO_DADO]"""


def build_context_prompt(state: JudgeState) -> str:
    """Build the context prompt for the LLM, including relevant guardrails and risk anchors."""
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
            f"- Localização: {atlas.get('location', 'Desconhecida')}",
            f"- Estado: {atlas.get('current_state', {})}",
            f"- Temp máx operação: {atlas.get('max_operating_temp', 'N/A')}°C",
            f"- Contrato: {atlas.get('contract_tier', 'N/A')}",
            f"- SLA: {atlas.get('sla_response_time_minutes', 'N/A')} minutos",
            f"- Ações permitidas: {atlas.get('allowed_actions', [])}",
            f"- Em manutenção: {atlas.get('under_maintenance', False)}",
        ])
        
        # === Dados Financeiros e Contratuais ===
        fin = atlas.get("financial_data", {})
        if fin:
            parts.extend([
                f"\n### 💰 Dados Financeiros e Contratuais",
                f"  - Valor dos produtos no equipamento: R${fin.get('estimated_product_value_brl', 0):,.2f}",
                f"  - Tipo de produto: {fin.get('product_type', 'N/A')}",
                f"  - Tempo máximo acima do limite antes de perda total: {fin.get('max_time_above_threshold_hours', 'N/A')} horas",
                f"  - Receita diária gerada pelo equipamento: R${fin.get('daily_revenue_impact_brl', 0):,.2f}",
                f"  - Cobertura de seguro: {'Sim' if fin.get('insurance_coverage') else 'Não'}",
                f"  - Penalidade por violação de SLA: R${fin.get('sla_penalty_per_hour_brl', 0):,.2f}/hora",
                f"  - Penalidade por ação não autorizada: R${fin.get('sla_unauthorized_action_penalty_brl', 0):,.2f}",
            ])
        if atlas.get("manual_excerpts"):
            manuals = atlas['manual_excerpts'][:10]
            parts.append(f"\n### 📘 Manual Técnico ({len(manuals)} trechos relevantes)")
            for m in manuals:
                parts.append(f"  - {m}")
        
        # Inject extended simulation/telemetry context with structure
        if atlas.get("simulation_data"):
            sim = atlas["simulation_data"]
            parts.append(f"\n### 📊 Telemetria Estendida do Equipamento")
            
            # Door/access data
            door_keys = [k for k in sim if "door" in k.lower() or "porta" in k.lower()]
            if door_keys:
                parts.append(f"\n#### 🚪 Dados de Abertura de Porta")
                for k in door_keys:
                    v = sim[k]
                    if k == "door_events_timeline" and isinstance(v, list):
                        parts.append(f"  - Timeline de aberturas ({len(v)} eventos):")
                        for evt in v[:10]:  # Limit to 10 for context window
                            parts.append(f"    • {evt.get('time', '?')}: {evt.get('duration_sec', '?')}s aberta")
                        if len(v) > 10:
                            parts.append(f"    • ... +{len(v) - 10} eventos adicionais")
                    else:
                        parts.append(f"  - {k}: {v}")
            
            # Compressor data
            comp_keys = [k for k in sim if "compressor" in k.lower()]
            if comp_keys:
                parts.append(f"\n#### ⚙️ Estado do Compressor")
                for k in comp_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
            # Power/Energy data
            power_keys = [k for k in sim if any(x in k.lower() for x in ["power", "energy", "consumption", "uptime"])]
            if power_keys:
                parts.append(f"\n#### 🔌 Dados de Energia e Alimentação")
                for k in power_keys:
                    v = sim[k]
                    if k == "power_cycle_history" and isinstance(v, list):
                        parts.append(f"  - Histórico de ciclos liga/desliga ({len(v)} dias):")
                        for cycle in v:
                            parts.append(f"    • {cycle.get('date', '?')}: DESLIGOU {cycle.get('off', '?')} → LIGOU {cycle.get('on', '?')} ({cycle.get('off_duration_hours', '?')}h desligado)")
                    else:
                        parts.append(f"  - {k}: {v}")
            
            # Temperature recovery data
            temp_keys = [k for k in sim if "temperature" in k.lower() and k not in power_keys]
            if temp_keys:
                parts.append(f"\n#### 🌡️ Dados de Recuperação Térmica")
                for k in temp_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
            # Other telemetry (exclude already-shown keys)
            shown_keys = set(door_keys + comp_keys + power_keys + temp_keys)
            other_keys = [k for k in sim if k not in shown_keys]
            if other_keys:
                parts.append(f"\n#### 📈 Outros Indicadores")
                for k in other_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
            # Investigative cues
            door_events = sim.get("door_open_events_last_hour", 0)
            if isinstance(door_events, (int, float)) and door_events > 10:
                parts.append(f"\n⚠️ ATENÇÃO: {door_events} aberturas de porta na última hora é considerado USO INTENSO (>10/hora). Investigue se isso explica a anomalia de temperatura antes de considerar falha técnica.")
            
            comp_status = sim.get("compressor_status", "")
            comp_amps = sim.get("compressor_current_amps")
            comp_max = sim.get("compressor_max_rated_amps")
            if comp_status == "running" and comp_amps and comp_max:
                if comp_amps <= comp_max:
                    parts.append(f"ℹ️ NOTA: Compressor operando normalmente (corrente {comp_amps}A dentro do máximo {comp_max}A). Se está em alta carga, pode estar COMPENSANDO entrada de calor, não falhando.")
            
            # Power cycle investigative cue
            power_history = sim.get("power_cycle_history", [])
            if power_history and len(power_history) >= 3:
                avg_off = sum(c.get("off_duration_hours", 0) for c in power_history) / len(power_history)
                parts.append(f"\n🚨 INVESTIGAR: Detectado histórico de {len(power_history)} ciclos de liga/desliga nos últimos dias, com média de {avg_off:.1f}h desligado por noite. VERIFIQUE se o operador está desligando o equipamento intencionalmente e avalie o impacto no consumo de energia, vida útil do compressor e segurança alimentar.")
            
            # Sensor contradiction cue
            primary_temp = sim.get("primary_temp_sensor_c")
            secondary_temp = sim.get("secondary_temp_probe_c")
            comp_pct = sim.get("compressor_run_time_pct", 0)
            if primary_temp is not None and secondary_temp is not None:
                delta_sensors = abs(primary_temp - secondary_temp)
                if delta_sensors > 10:
                    parts.append(f"\n🔍 CONTRADIÇÃO DETECTADA: Sensor principal lê {primary_temp}°C mas sonda de produto lê {secondary_temp}°C (diferença de {delta_sensors:.1f}°C). Verifique se o sensor principal está calibrado ou com defeito.")
            if primary_temp is not None and comp_pct < 50 and primary_temp > -11:
                parts.append(f"⚠️ INCONSISTÊNCIA: Sensor principal indica {primary_temp}°C (alarme) mas compressor opera a apenas {comp_pct}% da capacidade. Se a temperatura fosse realmente {primary_temp}°C, o compressor deveria estar >90%. Considere falha do sensor.")

            # Cascade failure cue: refrigerant leak
            ref_charge = sim.get("compressor_refrigerant_charge_pct")
            suction = sim.get("compressor_suction_pressure_psi")
            superheat = sim.get("evaporator_superheat_c")
            cop = sim.get("compressor_efficiency_cop")
            if ref_charge and ref_charge < 70:
                parts.append(f"\n🚨 ALERTA CRÍTICO: Carga de refrigerante em {ref_charge}% (normal: >85%). Isso pode ser a CAUSA RAIZ de toda a cadeia de falhas.")
                if suction and suction < 20:
                    parts.append(f"📉 Pressão de sucção em {suction} PSI (normal: 25-30) — confirma vazamento de refrigerante.")
                if superheat and superheat > 10:
                    parts.append(f"🌡️ Superaquecimento do evaporador em {superheat}°C (normal: 5-8°C) — indica gás insuficiente.")
                if cop and cop < 2.0:
                    parts.append(f"📊 COP em {cop} (normal: 3.0-3.5) — eficiência gravemente comprometida pelo vazamento.")

            # Cascade cue: deferred maintenance
            maint_history = sim.get("maintenance_history", [])
            for visit in maint_history:
                deferred = visit.get("deferred_items", [])
                if deferred:
                    parts.append(f"\n📋 MANUTENÇÃO ADIADA ({visit.get('date', '?')}): {', '.join(deferred)}. Verifique se este item adiado é a CAUSA RAIZ do problema atual.")

            # Power voltage cue
            voltage = sim.get("power_supply_voltage_v")
            nominal = sim.get("power_supply_voltage_nominal_v", 220)
            building_affected = sim.get("building_other_equipment_affected", False)
            if voltage and nominal and voltage < nominal * 0.92:
                drop_pct = round((1 - voltage / nominal) * 100, 1)
                parts.append(f"\n⚡ SUBTENSÃO DETECTADA: Tensão em {voltage}V (nominal: {nominal}V, queda de {drop_pct}%). Isso reduz a capacidade do compressor mesmo que ele esteja 'rodando'.")
                if building_affected:
                    parts.append(f"🏢 CONFIRMAÇÃO: Outros equipamentos do prédio TAMBÉM estão afetados (HVAC: {sim.get('building_hvac_status', '?')}, luzes piscando: {sim.get('building_lighting_flickering', '?')}). A causa é da REDE ELÉTRICA, não do equipamento.")

            # Defrost overshoot cue
            defrost_active = sim.get("defrost_mode_active", False)
            defrost_scheduled = sim.get("defrost_scheduled_duration_min")
            defrost_elapsed = sim.get("defrost_actual_elapsed_min")
            if defrost_active and defrost_scheduled and defrost_elapsed:
                if defrost_elapsed > defrost_scheduled * 1.5:
                    parts.append(f"\n⏰ OVERSHOOT DE DEGELO: Ciclo programado para {defrost_scheduled}min, já decorreram {defrost_elapsed}min (+{defrost_elapsed - defrost_scheduled}min além do previsto). O degelo NÃO terminou no tempo esperado!")
                    heater_status = sim.get("defrost_heater_status")
                    heater_pct = sim.get("defrost_heater_power_pct")
                    heater_temp = sim.get("defrost_heater_temp_c")
                    relay_cycles = sim.get("defrost_heater_relay_cycles_today")
                    if heater_status == "ON" and heater_pct and heater_pct >= 100:
                        parts.append(f"🔥 RELÉ TRAVADO: Aquecedor em {heater_pct}% contínuo (deveria pulsar 50-70%). Temperatura do aquecedor: {heater_temp}°C. Ciclos do relé hoje: {relay_cycles} (normal: 5-8). O relé do aquecedor está PRESO em ON!")
                products_safe = sim.get("products_relocated", False)
                if products_safe:
                    parts.append(f"📦 PRODUTOS SEGUROS: Produtos foram realocados para {sim.get('products_relocated_to', '?')}. Não é emergência de produto, mas o equipamento precisa de atenção.")

    # --- Prompt Refinement: Context Anchors ---
    financial_anchor = ""
    physical_anchor = ""
    
    if oracle:
        parts.extend([
            f"\n## Predição (Oracle)",
            f"- Prob. falha: {oracle.get('predicted_value', 'N/A')}",
            f"- Confiança: {oracle.get('confidence', 'N/A')}",
            f"- Impacto financeiro: R${oracle.get('financial_impact', 0):,.2f}",
            f"- Recomendação: {oracle.get('recommendation', 'N/A')}",
        ])
        
        # Financial Anchor
        imp = oracle.get('financial_impact', 0.0)
        limit = 10000.0 # Default daily limit for demo
        if imp > 0:
            pct = (imp / limit) * 100
            if pct > 90:
                financial_anchor = f"⚠️ CRITICAL: Impacto ${imp:.2f} consome {pct:.1f}% do budget diário. R_Fin deve ser > 0.90."
            elif pct > 50:
                 financial_anchor = f"⚠️ HIGH: Impacto ${imp:.2f} consome {pct:.1f}% do budget diário. R_Fin deve ser > 0.60."

    elif state.get("is_fast_track"):
        parts.append("\n## Oracle: BYPASS (fast-track por severidade)")

    # Physical Anchor
    val = trigger.value
    max_t = atlas.get('max_operating_temp')
    if val and max_t and isinstance(val, (int, float)) and isinstance(max_t, (int, float)):
        diff = val - max_t
        if diff > 0:
            physical_anchor = f"⚠️ CRITICAL: Valor {val} excede limite {max_t} em {diff:.1f} un. R_F deve ser > 0.90."
        elif val > (max_t * 0.9):
             physical_anchor = f"⚠️ WARNING: Valor {val} está próximo do colapso ({max_t}). R_F deve ser > 0.70."

    if financial_anchor or physical_anchor:
        parts.append("\n## ⚓ Orientação de Risco (Context Anchors)")
        if financial_anchor: parts.append(f"- {financial_anchor}")
        if physical_anchor: parts.append(f"- {physical_anchor}")

    # === Inject relevant guardrails into LLM context (doc §6.1) ===
    try:
        from caos.core.nodes.guardrails import get_guardrail_engine
        engine = get_guardrail_engine()
        severity_val = trigger.severity.value
        contract_tier = atlas.get("contract_tier", "")
        under_maint = atlas.get("under_maintenance", False)

        # Select guardrails relevant to this specific scenario
        relevant = []
        for g in engine.guardrails:
            gid = g.get("id", "")
            gsev = g.get("severity", "")
            gcat = g.get("category", "")

            # Always include BLOCKING guardrails
            if gsev == "BLOCKING":
                relevant.append(g)
            # Include HIGH severity for CRITICAL/HIGH events
            elif gsev == "HIGH" and severity_val in ("CRITICAL", "HIGH"):
                relevant.append(g)
            # Include contractual guardrails when contract data is present
            elif gcat == "CONTRACTUAL" and contract_tier:
                relevant.append(g)
            # Include maintenance guardrails when asset is under maintenance
            elif under_maint and ("maintenance" in g.get("condition", "").lower() or "loto" in gid.lower()):
                relevant.append(g)

        # Deduplicate and limit
        seen = set()
        unique = []
        for g in relevant:
            gid = g.get("id")
            if gid not in seen:
                seen.add(gid)
                unique.append(g)
        relevant = unique[:15]

        if relevant:
            parts.append("\n## ⚠️ Guardrails Ativos (respeitar obrigatoriamente)")
            parts.append("Estas são regras de segurança que NÃO podem ser violadas. Considere-as na sua análise:")
            # Group by category for clarity
            from collections import defaultdict
            by_cat = defaultdict(list)
            for g in relevant:
                by_cat[g.get("category", "OTHER")].append(g)
            cat_labels = {
                "PHYSICAL": "🛡️ Físicas (Safety)",
                "FINANCIAL": "💰 Financeiras",
                "CONTRACTUAL": "📋 Contratuais",
                "COMMUNICATION": "📣 Comunicação",
                "SECOPS": "🔒 SecOps",
                "ROBUSTNESS": "⚙️ Robustez",
            }
            for cat, gs in by_cat.items():
                parts.append(f"\n### {cat_labels.get(cat, cat)}")
                for g in gs:
                    parts.append(
                        f"- **[{g.get('id', '?')}] {g.get('name', 'N/A')}**: "
                        f"{g.get('message', 'Sem descrição')} "
                        f"(ação: {g.get('action', '?')}, severidade: {g.get('severity', '?')}, "
                        f"condição: `{g.get('condition', 'N/A')}`)"
                    )
    except Exception:
        pass  # Guardrails injection is best-effort

    # === Enhancment: RAG Context Retrieval ===
    try:
        rag = get_rag_engine()
        
        # PoC: Seed knowledge base if empty (Simulation of KB)
        if hasattr(rag, "documents") and len(rag.documents) == 0:
             rag.add_document(
                 "MANUAL TÉCNICO - SISTEMA DE REFRIGERAÇÃO: "
                 "Se a temperatura exceder 110°C, verifique o nível de refrigeração e o funcionamento do ventilador. "
                 "Risco de cavitação da bomba se operar a seco. Desligamento imediato recomendado.",
                 {"topic": "cooling"}, "man_001"
             )
             rag.add_document(
                 "MANUAL TÉCNICO - VIBRAÇÃO: "
                 "Vibração excessiva (>5mm/s) indica desalinhamento do eixo ou falha no rolamento. "
                 "Reduza a carga e agende manutenção.",
                 {"topic": "vibration"}, "man_002"
             )

        # Query relevant knowledge
        query_text = f"{trigger.metric} {trigger.value} issue troubleshooting"
        retrieved_chunks = rag.query(query_text, k=1)
        
        if retrieved_chunks:
            # Filter irrelevant RAG results (e.g., chiller manuals for freezer alerts)
            asset_type = (atlas.get("type") or "").lower()
            filtered = []
            for chunk in retrieved_chunks:
                cl = chunk.lower()
                is_irrelevant = (
                    (asset_type == "freezer" and "110°c" in cl and "desligamento imediato" in cl) or
                    (asset_type == "cooler" and "chiller" in cl and "cooler" not in cl)
                )
                if not is_irrelevant:
                    filtered.append(chunk)
            if filtered:
                parts.append("\n## 📘 Manual Técnico (Contexto RAG)")
                for i, chunk in enumerate(filtered):
                    parts.append(f"- Excerpt {i+1}: {chunk}")
    except Exception as e:
        logger.warning("cortex_rag_failed", error=str(e))

    # Recycle context: if guardrails vetoed, tell LLM about violations
    prior_violations = state.get("guardrail_violations", [])
    recycle_count = state.get("recycle_count", 0)
    if recycle_count > 0 and prior_violations:
        parts.append("\n## ⚠️ REPLANEJAMENTO (Guardrails vetaram ação anterior)")
        parts.append("As seguintes regras de segurança foram violadas:")
        for v in prior_violations[:5]:
            parts.append(f"- [{v.get('id', '?')}] {v.get('description', v.get('message', 'N/A'))} (severity: {v.get('severity', '?')})")
        parts.append("Proponha uma ação MENOS agressiva que respeite essas restrições.")

    parts.append("\nAnalise a situação e responda no formato especificado.")
    return "\n".join(parts)


def parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse structured LLM response into risk dimensions.
    
    Handles multi-line PENSAMENTO blocks by collecting all lines
    between PENSAMENTO: and the next keyword.
    """
    result = {
        "confidence": 0.7,  # Default confidence
        "physical": 0.5,
        "financial": 0.3,
        "contractual": 0.1,
        "communication": 0.1,
        "action": "notification",
        "analysis": "",
        "justification": "",
        "thought": "",
        "risk_justifications": {},
    }

    # Keywords that signal the end of a multi-line block
    KEYWORDS = {
        "PENSAMENTO:", "ANÁLISE:", "CONFIANÇA:", "RISCO_FISICO:",
        "RISCO_FINANCEIRO:", "RISCO_CONTRATUAL:", "RISCO_COMUNICACAO:",
        "AÇÃO:", "JUSTIFICATIVA:", "SOLICITAR:",
    }

    def _extract_score(line_value: str) -> float:
        """Extract numeric score, handling 'score | justification' format."""
        raw = line_value.split("|")[0].strip()
        return float(raw)

    def _extract_justification(line_value: str) -> str:
        """Extract justification after the pipe."""
        parts = line_value.split("|", 1)
        if len(parts) > 1:
            just = parts[1].strip()
            if ":" in just:
                just = just.split(":", 1)[1].strip()
            return just
        return ""

    def _is_keyword_line(line: str) -> bool:
        return any(line.startswith(kw) for kw in KEYWORDS)

    lines = response_text.strip().split("\n")
    
    # First pass: collect multi-line blocks (PENSAMENTO, JUSTIFICATIVA)
    current_block = None
    block_lines: list[str] = []
    blocks: dict[str, str] = {}
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_block:
                block_lines.append("")  # Preserve paragraph breaks
            continue
        
        if _is_keyword_line(stripped):
            # Save previous block
            if current_block and block_lines:
                blocks[current_block] = "\n".join(block_lines)
            
            # Start new block
            for kw in KEYWORDS:
                if stripped.startswith(kw):
                    current_block = kw.rstrip(":")
                    content = stripped[len(kw):].strip()
                    block_lines = [content] if content else []
                    break
        elif current_block:
            # Continuation of current block
            block_lines.append(stripped)
    
    # Save last block
    if current_block and block_lines:
        blocks[current_block] = "\n".join(block_lines)
    
    # Extract structured fields from blocks
    if "PENSAMENTO" in blocks:
        result["thought"] = blocks["PENSAMENTO"]
    if "ANÁLISE" in blocks:
        result["analysis"] = blocks["ANÁLISE"]
    if "JUSTIFICATIVA" in blocks:
        result["justification"] = blocks["JUSTIFICATIVA"]
    if "AÇÃO" in blocks:
        result["action"] = blocks["AÇÃO"].strip().lower()
    if "CONFIANÇA" in blocks:
        try:
            result["confidence"] = float(blocks["CONFIANÇA"].strip())
        except ValueError:
            pass
    
    # Risk scores with justifications
    for key, field in [("RISCO_FISICO", "physical"), ("RISCO_FINANCEIRO", "financial"),
                       ("RISCO_CONTRATUAL", "contractual"), ("RISCO_COMUNICACAO", "communication")]:
        if key in blocks:
            val = blocks[key]
            try:
                result[field] = _extract_score(val)
                j = _extract_justification(val)
                if j:
                    result["risk_justifications"][field] = j
            except ValueError:
                pass

    # Ensure analysis falls back to thought if empty
    if not result.get("analysis") and result.get("thought"):
        result["analysis"] = result["thought"]

    return result


# =============================================
# Fallback: cálculo por código (sem LLM)
# =============================================

# Metrics that represent temperature measurements
TEMPERATURE_METRICS = {
    "temperature", "cabinet_temperature", "temp", "motor_temp",
    "coolant_temp", "ambient_temp", "exhaust_temp",
}

GPS_METRICS = {
    "gps_displacement", "displacement", "gps_distance", "location_change",
}


def calculate_risk_dimensions_code(state: JudgeState) -> RiskDimensions:
    """Calculate risk dimensions using code only (fallback without LLM).
    
    Multi-metric aware: handles temperature, GPS displacement, and other metrics
    with appropriate risk gradation for each type.
    """
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}
    metric = (trigger.metric or "").lower()
    value = trigger.value

    # R_F: Physical Risk — metric-aware calculation
    r_f = 0.0
    if metric in TEMPERATURE_METRICS and value is not None:
        max_temp = atlas.get("max_operating_temp")
        if max_temp and max_temp > 0:
            # Gradation: 0-80% of limit = low, 80-100% = moderate, 100-150% = high, >150% = critical
            temp_ratio = value / max_temp
            if temp_ratio <= 0.8:
                r_f = temp_ratio * 0.2  # 0 - 0.16
            elif temp_ratio <= 1.0:
                r_f = 0.16 + (temp_ratio - 0.8) * 1.7  # 0.16 - 0.50
            elif temp_ratio <= 1.5:
                r_f = 0.50 + (temp_ratio - 1.0) * 0.6  # 0.50 - 0.80
            else:
                r_f = min(1.0, 0.80 + (temp_ratio - 1.5) * 0.4)  # 0.80 - 1.0
        else:
            r_f = 0.3  # No reference: moderate default
    elif metric in GPS_METRICS and value is not None:
        # GPS displacement risk based on distance thresholds
        if value > 5000:
            r_f = 0.95  # Almost certainly theft
        elif value > 1000:
            r_f = 0.70  # Significant unauthorized movement
        elif value > 200:
            r_f = 0.40  # Possible unauthorized movement
        elif value > 50:
            r_f = 0.15  # Minor, likely store reorganization
        else:
            r_f = 0.05
    elif value is not None and atlas.get("max_operating_temp"):
        # Generic: use simple ratio
        max_val = atlas["max_operating_temp"]
        if max_val > 0:
            ratio = value / max_val
            r_f = min(1.0, max(0.0, (ratio - 0.8) / 0.4))

    # Severity floor: ensure minimum risk based on severity
    if trigger.severity == Severity.CRITICAL:
        r_f = max(r_f, 0.85)
    elif trigger.severity == Severity.HIGH:
        r_f = max(r_f, 0.50)
    elif trigger.severity == Severity.MEDIUM:
        r_f = max(r_f, 0.20)

    # R_Fin: Financial Risk
    r_fin = 0.0
    atlas_ext = atlas.get("telemetry_extension") or atlas.get("current_state") or {}
    oracle = state.get("oracle_forecast")
    if atlas_ext.get("products_relocated"):
        r_fin = 0.1  # Products safe in backup, minimal financial risk
    elif oracle and oracle.get("financial_impact"):
        r_fin = min(1.0, oracle["financial_impact"] / 10000.0)
    elif trigger.severity in [Severity.CRITICAL, Severity.HIGH]:
        r_fin = 0.5
    elif trigger.severity == Severity.MEDIUM:
        r_fin = 0.25

    # R_C: Contractual Risk
    r_c = 0.0
    contract_tier = atlas.get("contract_tier", "")
    if contract_tier == "MONITORING_ONLY":
        r_c = 0.8
    elif contract_tier == "ON_TRADE":
        r_c = 0.3  # On-trade outlets: moderate contractual exposure
    elif contract_tier == "PREMIUM":
        r_c = 0.5  # Premium: higher contractual obligations

    # R_K: Communication Risk
    r_k = 0.1
    if trigger.severity in [Severity.CRITICAL, Severity.HIGH]:
        r_k = 0.3  # Higher severity = more communication needed

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
    # Short-circuit if sense_node already blocked this request
    if state.get("sense_blocked"):
        return {}

    logger.info("cortex_node_start", event_id=state["trigger"].event_id)

    # Check if this is a recycle (re-plan after guardrail veto)
    recycle_count = state.get("recycle_count", 0)
    prior_violations = state.get("guardrail_violations", []) if recycle_count > 0 else []
    if recycle_count > 0:
        logger.info(
            "cortex_recycle_replan",
            recycle_count=recycle_count,
            prior_violations=prior_violations,
        )

    llm = get_llm()
    llm_reasoning = None
    reasoning_trace = []

    # === ROB_002: Circuit Breaker — skip LLM if budget exceeded ===
    cb = get_circuit_breaker()
    if cb.is_tripped():
        llm = None  # Force code-only fallback
        reasoning_trace.append("⚡ Circuit breaker ativo: orçamento LLM diário excedido, usando modo código")
        logger.warning("circuit_breaker_tripped", status=cb.get_status())

    atlas = state.get("atlas_context") or {} # Define early for discovery use


    # === Step 1: Hybrid Risk Calculation ===
    llm_response_empty = False
    code_risk = calculate_risk_dimensions_code(state)  # Always compute code baseline

    if llm is not None:
        try:
            # --- Passo 1: Solicitação Inicial ---
            context_prompt = build_context_prompt(state)
            logger.info("cortex_context_prompt_debug", prompt=context_prompt)
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=context_prompt),
            ]

            # Hard limit de 15s para a primeira chamada (prompt rico com telemetria)
            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warning("cortex_llm_timeout", timeout=15.0)
                raise TimeoutError("LLM timeout >15s — Reptilian Mode")

            llm_response_text = response.content
            
            # --- Passo 2: Verificação de Descoberta Agêntica ---
            if "SOLICITAR: CLIMA" in str(llm_response_text).upper():
                reasoning_trace.append("🔍 Agente solicitou dados externos: CLIMA")
                
                from caos.integration.weather import get_weather_client
                weather_client = get_weather_client()
                
                # Definir localidade baseada no Atlas ou default
                location = atlas.get("location", "São Paulo")
                weather_data = await weather_client.get_forecast(location)
                
                reasoning_trace.append(f"🌤️ Dados do Clima recebidos: {weather_data.get('temperature')}°C, {weather_data.get('condition')}")
                
                # Re-prompt com os novos dados
                messages.append(HumanMessage(content=f"DADOS RECEBIDOS: {weather_data}\nAgora forneça sua decisão final."))
                
                # Segunda chamada LLM (15s)
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=15.0,
                )
                llm_response_text = response.content

            if not llm_response_text or not llm_response_text.strip():
                llm_response_empty = True
                raise ValueError("LLM returned empty response")

            llm_reasoning = parse_llm_response(llm_response_text)

            llm_risk = RiskDimensions(
                physical=min(1.0, max(0.0, llm_reasoning["physical"])),
                financial=min(1.0, max(0.0, llm_reasoning["financial"])),
                contractual=min(1.0, max(0.0, llm_reasoning["contractual"])),
                communication=min(1.0, max(0.0, llm_reasoning["communication"])),
            )

            # --- Enhancement #2: Dynamic Fusion (Confidence-based) ---
            # Trust LLM based on its reported confidence, clamped to [0.1, 0.9]
            # If LLM is hallucinating (low confidence), we trust Code more.
            conf = llm_reasoning.get("confidence", 0.7)
            LLM_WEIGHT = max(0.1, min(0.9, conf))
            CODE_WEIGHT = 1.0 - LLM_WEIGHT
            fused = RiskDimensions(
                physical=LLM_WEIGHT * llm_risk.physical + CODE_WEIGHT * code_risk.physical,
                financial=LLM_WEIGHT * llm_risk.financial + CODE_WEIGHT * code_risk.financial,
                contractual=LLM_WEIGHT * llm_risk.contractual + CODE_WEIGHT * code_risk.contractual,
                communication=LLM_WEIGHT * llm_risk.communication + CODE_WEIGHT * code_risk.communication,
            )

            # --- Enhancement #1: Selective Clamp ---
            # Physical & Communication: LLM can't underestimate (safety floor)
            # Financial & Contractual: LLM CAN reduce (e.g., products relocated, SLA exemption)
            risk_dimensions = RiskDimensions(
                physical=max(fused.physical, code_risk.physical),
                financial=fused.financial,
                contractual=fused.contractual,
                communication=max(fused.communication, code_risk.communication),
            )

            # --- Enhancement #4: Audit trail (LLM vs Code delta) ---
            _audit_delta = {
                "physical":      round(llm_risk.physical - code_risk.physical, 3),
                "financial":     round(llm_risk.financial - code_risk.financial, 3),
                "contractual":   round(llm_risk.contractual - code_risk.contractual, 3),
                "communication": round(llm_risk.communication - code_risk.communication, 3),
            }
            logger.info(
                "cortex_hybrid_audit",
                llm_risk={"R_F": llm_risk.physical, "R_Fin": llm_risk.financial,
                          "R_C": llm_risk.contractual, "R_K": llm_risk.communication},
                code_risk={"R_F": code_risk.physical, "R_Fin": code_risk.financial,
                           "R_C": code_risk.contractual, "R_K": code_risk.communication},
                fused_risk={"R_F": risk_dimensions.physical, "R_Fin": risk_dimensions.financial,
                            "R_C": risk_dimensions.contractual, "R_K": risk_dimensions.communication},
                delta=_audit_delta,
            )

            # Full chain of thought
            thought = llm_reasoning.get('thought', '')
            if thought:
                reasoning_trace.append(f"🧠 Cadeia de Pensamento:\n{thought}")
            
            reasoning_trace.append(f"🤖 LLM: {llm_reasoning.get('analysis', '')}")
            reasoning_trace.append(f"💡 Ação sugerida: {llm_reasoning.get('action', 'N/A')}")
            reasoning_trace.append(f"📋 {llm_reasoning.get('justification', '')}")
            
            # Per-axis risk justifications
            risk_justs = llm_reasoning.get('risk_justifications', {})
            if risk_justs:
                rj_parts = ["📊 Justificativas de Risco:"]
                labels = {'physical': 'Físico', 'financial': 'Financeiro',
                          'contractual': 'Contratual', 'communication': 'Comunicação'}
                for axis, label in labels.items():
                    if axis in risk_justs:
                        score = llm_reasoning.get(axis, 'N/A')
                        rj_parts.append(f"  • R_{label} = {score}: {risk_justs[axis]}")
                reasoning_trace.append("\n".join(rj_parts))
            
            reasoning_trace.append(
                f"🔀 Fusão híbrida: LLM({int(round(LLM_WEIGHT*100))}%) + Código({int(round(CODE_WEIGHT*100))}%) "
                f"→ R_F={risk_dimensions.physical:.2f}, R_Fin={risk_dimensions.financial:.2f}, "
                f"R_C={risk_dimensions.contractual:.2f}, R_K={risk_dimensions.communication:.2f}"
            )

            logger.info(
                "cortex_llm_success",
                analysis=llm_reasoning.get("analysis", ""),
                action=llm_reasoning.get("action", ""),
            )

        except Exception as e:
            logger.warning("cortex_llm_fallback", error=str(e))
            risk_dimensions = code_risk
            llm_reasoning = None
            reasoning_trace.append(f"⚠️ LLM indisponível, usando cálculo por código: {e}")
    else:
        risk_dimensions = code_risk
        reasoning_trace.append("📊 Modo código: LLM não configurada")

    # Store LLM status flags for guardrail context
    _extra_state = {
        "_llm_response_empty": llm_response_empty,
    }

    # === Recycle dampening: soften risk on re-plan to produce less aggressive action ===
    if recycle_count > 0:
        dampen = 0.75  # 25% reduction on recycle
        risk_dimensions = RiskDimensions(
            physical=risk_dimensions.physical * dampen,
            financial=risk_dimensions.financial * dampen,
            contractual=risk_dimensions.contractual * dampen,
            communication=risk_dimensions.communication * dampen,
        )
        reasoning_trace.append(
            f"♻️ Recycle #{recycle_count}: riscos reduzidos em {int((1-dampen)*100)}% "
            f"para respeitar guardrails vetados: {[v.get('id','?') for v in prior_violations]}"
        )

    # === Step 2: Calculate A and O scores ===
    oracle = state.get("oracle_forecast")

    # --- Fix #2: Multi-signal Atlas Score (data richness) ---
    a_signals = 0
    a_total = 5  # max signals
    if atlas.get("current_state") and len(atlas["current_state"]) > 0:
        a_signals += 1  # has real-time state
    if atlas.get("manual_excerpts") and len(atlas["manual_excerpts"]) > 0:
        a_signals += 1  # has manual info
    if atlas.get("allowed_actions") and len(atlas["allowed_actions"]) > 0:
        a_signals += 1  # has action catalogue
    if atlas.get("contract_tier"):
        a_signals += 1  # has contract info
    if atlas.get("max_operating_temp") is not None:
        a_signals += 1  # has operational limits
    a_score = a_signals / a_total  # 0.0 to 1.0
    if atlas.get("under_maintenance"):
        a_score = 0.0  # cannot trust data during maintenance

    # --- Fix #3: Detect discrepancy between trigger and Atlas state ---
    trigger = state["trigger"]
    metric = (trigger.metric or "").lower()
    atlas_state = atlas.get("current_state") or {}
    if metric and trigger.value is not None:
        # Map trigger metric to Atlas state key
        atlas_key = metric.replace("compressor_", "").replace("cabinet_", "")
        atlas_val = atlas_state.get(atlas_key) or atlas_state.get(metric)
        if atlas_val is not None:
            discrepancy = abs(trigger.value - atlas_val)
            threshold = max(trigger.value, atlas_val) * 0.15  # 15% tolerance
            if discrepancy > threshold:
                reasoning_trace.append(
                    f"⚠️ Discrepância detectada: trigger={trigger.value} vs Atlas={atlas_val} "
                    f"(Δ={discrepancy:.1f}, tolerância={threshold:.1f}). "
                    f"Dados do Atlas podem estar desatualizados."
                )
                a_score = max(0.3, a_score * 0.6)  # penalize Atlas reliability
                logger.warning(
                    "cortex_data_discrepancy",
                    trigger_value=trigger.value,
                    atlas_value=atlas_val,
                    delta=discrepancy,
                )

    o_score = 1.0
    if oracle:
        o_score = oracle.get("confidence", 0.5)
    elif state.get("is_fast_track"):
        o_score = 1.0

    # === Step 3: Verdict (with RLHF-learned or tenant-specific weights) ===
    tenant_id = None
    if state["trigger"].context:
        tenant_id = state["trigger"].context.tenant_id
    # Fix #6: Use RLHF-learned weights when available
    judge = JudgeEngine.from_rlhf(tenant_id=tenant_id)
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

    # === Enhancment: RLHF Recording ===
    # Record the experience for future learning
    try:
        from caos.core.rlhf import get_rlhf_optimizer, ExperienceRecord
        settings = get_settings()
        if settings.rlhf_enabled:
            optimizer = get_rlhf_optimizer()
            exp = ExperienceRecord(
                action_id=state.get("action_id", f"act_{state['trigger'].event_id}"),
                event_id=state["trigger"].event_id,
                tenant_id=tenant_id or "default",
                asset_id=state["trigger"].context.asset_id if state["trigger"].context else "unknown",
                
                # Inputs
                atlas_score=a_score,
                oracle_score=o_score,
                severity_score=result.severity_score,
                risk_physical=risk_dimensions.physical,
                risk_financial=risk_dimensions.financial,
                risk_contractual=risk_dimensions.contractual,
                risk_communication=risk_dimensions.communication,
                guardrail_penalty=0.0,
                
                # Outputs
                verdict_score=result.verdict_score,
                decision_band=result.decision_band.value,
                risk_level=result.risk_level.value,
                
                # Weights
                weights_used=judge.get_weights_used() if hasattr(judge, "get_weights_used") else {}
            )
            optimizer.record_experience(exp)
    except Exception as e:
        logger.warning("cortex_rlhf_record_failed", error=str(e))

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
        **_extra_state,
    }
