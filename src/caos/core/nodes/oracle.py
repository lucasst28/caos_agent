"""Oracle Node - Full Deliberation (Reasoning + Prediction + Verdict).

The Oracle is now the cognitive core of the system. It performs:
1. Optional prediction via ML models (external Oracle API)
2. CODE calculates risk dimensions (R_F, R_Fin, R_C, R_K) — deterministic baseline
3. LLM (Gemini) reasons about the situation and proposes actions
4. Dynamic confidence-based fusion (CODE + LLM)
5. Verdict formula is applied
6. Decision band and risk level are classified

CAOS (Verifier) will then audit the Oracle's decision with a checklist.
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
from caos.integration.oracle import get_oracle_client
from caos.schemas.enums import DecisionBand, GuardrailAction, Severity
from caos.schemas.risk import RiskDimensions
from caos.schemas.state import JudgeState, OraclePrediction

logger = structlog.get_logger(__name__)


def _record_llm_usage(cb, response, caller: str = "oracle") -> None:
    """Record LLM token usage on the circuit breaker.
    
    LangChain responses expose usage via response.usage_metadata (dict with
    'input_tokens', 'output_tokens', 'total_tokens') or response.response_metadata.
    """
    try:
        tokens = 0
        meta = getattr(response, "usage_metadata", None)
        if meta and isinstance(meta, dict):
            tokens = meta.get("total_tokens", 0)
        elif hasattr(response, "response_metadata"):
            rm = response.response_metadata or {}
            usage = rm.get("usage", rm.get("token_usage", {}))
            if isinstance(usage, dict):
                tokens = usage.get("total_tokens", 0)
        if tokens > 0:
            cb.record_usage(tokens=tokens, cost_usd=0.0)
            logger.debug("circuit_breaker_usage_recorded", caller=caller, tokens=tokens)
    except Exception as e:
        logger.debug("circuit_breaker_usage_record_failed", caller=caller, error=str(e))


# =============================================
# LLM singleton (via registry)
# =============================================

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

        if settings.google_api_key:
            kwargs["google_api_key"] = settings.google_api_key
            provider = "google_ai_studio"
        elif settings.google_cloud_project:
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


def get_oracle_system_cache(llm) -> str | None:
    """Create or reuse a cached system prompt for Vertex AI requests."""
    from caos._registry import get, put

    cache_name = get("oracle_system_cache")
    if cache_name is not None:
        return cache_name

    try:
        from langchain_google_genai import create_context_cache

        settings = get_settings()
        ttl = settings.vertex_system_cache_ttl
        if llm is None or getattr(llm, "client", None) is None:
            return None

        cache_name = create_context_cache(
            llm,
            [SystemMessage(content=ORACLE_SYSTEM_PROMPT)],
            ttl=ttl,
        )
        put("oracle_system_cache", cache_name)
        logger.info("oracle_system_cache_created", cache_name=cache_name, ttl=ttl)
        return cache_name
    except Exception as e:
        logger.warning("oracle_system_cache_failed", error=str(e))
        return None


# =============================================
# System Prompt para o Oracle (Raciocínio)
# =============================================

ORACLE_SYSTEM_PROMPT = """Você é o Oracle, o motor de raciocínio do sistema CAOS (Centralized Autonomous Operating System). Você é um Diagnóstico Industrial Generalista de alto nível.

Sua função é investigar a CAUSA RAIZ de anomalias em sistemas complexos. O alerta é apenas o SINTOMA — sua missão é descobrir a CAUSA REAL e propor a melhor ação.

IMPORTANTE: Sua decisão será AUDITADA pelo CAOS Verifier, que validará cada aspecto do seu raciocínio com um checklist rigoroso. Seja completo e preciso.

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

## Ações Disponíveis
- **shutdown**: Desligamento de emergência (apenas falhas críticas com risco imediato)
- **maintenance**: Escalonar para equipe de manutenção (falhas técnicas confirmadas)
- **setpoint**: Ajuste de parâmetro operacional
- **ticket**: Criar ticket para análise posterior
- **notification**: Notificar gestor/operador imediatamente
- **observe**: AGUARDAR e REAVALIAR após 30 minutos. Use quando:
  - A causa raiz é OPERACIONAL (ex: aberturas de porta em horário de pico) ou AMBIENTAL (ex: clima quente)
  - O equipamento está funcionando NORMALMENTE (compressor ok, corrente ok, sem falhas)
  - A situação deve se AUTO-RESOLVER quando o fator operacional cessar (ex: fim do pico)
  - NÃO há risco imediato de perda de produto (temperatura ainda dentro da margem de segurança)
  - Exemplo: "Portas abertas 15x/hora em pico + clima 35°C → aguardar fim do pico e reavaliar"

⚠️ REGRA OBRIGATÓRIA — OBSERVE para causa operacional/ambiental:
Se TODOS os critérios abaixo forem verdadeiros, você DEVE escolher AÇÃO: observe. Não use 'notification' ou 'maintenance' nestes casos:
  1. Aberturas de porta > 10/hora (uso intenso) OU Temperatura Ambiente (CLIMA) > 30°C.
  2. Compressor operando normalmente (corrente ≤ máximo, sem alarmes críticos).
  3. Não há falha técnica confirmada (sem vazamento de gás, sem falha elétrica).
Justificativa: quando a elevação de temperatura é causada por excesso de abertura de portas
(comportamento operacional) e/ou temperatura ambiente alta (clima), a temperatura deve ESTABILIZAR
naturalmente quando o fator operacional cessar. Intervir precocemente com manutenção gera perdas financeiras e operacionais. 
AGUARDAR 30min e REAVALIAR é a decisão estratégica correta.
- **read**: Apenas observar passivamente (sem agenda de reavaliação)

## Formato de Resposta (Decisão Final)
PENSAMENTO: 
[Raciocínio em camadas, estruturado e DIDÁTICO.
OBRIGATÓRIO pular duas linhas (usar ENTER duplo) entre cada Camada ou novo parágrafo.
Utilize bullet points para dados. NUNCA gere um bloco monolítico de texto. 
Seja visualmente agradável, como uma documentação clínica limpa.]
ANÁLISE: [Diagnóstico final com causa raiz identificada]
CONFIANÇA: [0.0-1.0]
RISCO_FISICO: [0.0-1.0] | JUSTIFICATIVA_RF: [Por que este score, com dados]
RISCO_FINANCEIRO: [0.0-1.0] | JUSTIFICATIVA_RFIN: [Por que este score — cite valores em R$]
RISCO_CONTRATUAL: [0.0-1.0] | JUSTIFICATIVA_RC: [Por que este score — cite SLA e penalidades]
RISCO_COMUNICACAO: [0.0-1.0] | JUSTIFICATIVA_RK: [Quem precisa ser notificado e por quê]
AÇÃO: [tipo de ação: shutdown|maintenance|setpoint|ticket|notification|observe|read]
JUSTIFICATIVA: [Justificativa detalhada com evidências cruzadas]
DADOS_CONSULTADOS: [Lista de todas as fontes de dados que você analisou]
HIPOTESES_DESCARTADAS: [Lista de hipóteses que foram descartadas e por quê]

## Formato de Resposta (Inquérito Agêntico)
SOLICITAR: [NOME_DO_DADO]"""


def build_context_prompt(state: JudgeState) -> str:
    """Build the context prompt for the LLM, including relevant guardrails and risk anchors."""
    trigger = state["trigger"]
    atlas = state.get("atlas_context") or {}
    oracle_prediction = state.get("oracle_forecast")

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
        
        # Inject extended simulation/telemetry context
        sim_data_raw = atlas.get("simulation_data")
        if isinstance(sim_data_raw, dict):
            sim = sim_data_raw
            parts.append(f"\n### 📊 Telemetria Estendida do Equipamento")
            
            door_keys = [k for k in sim if "door" in k.lower() or "porta" in k.lower()]
            if door_keys:
                parts.append(f"\n#### 🚪 Dados de Abertura de Porta")
                for k in door_keys:
                    v = sim[k]
                    if k == "door_events_timeline" and isinstance(v, list):
                        parts.append(f"  - Timeline de aberturas ({len(v)} eventos):")
                        for evt in v[:10]:
                            parts.append(f"    • {evt.get('time', '?')}: {evt.get('duration_sec', '?')}s aberta")
                        if len(v) > 10:
                            parts.append(f"    • ... +{len(v) - 10} eventos adicionais")
                    else:
                        parts.append(f"  - {k}: {v}")
            
            comp_keys = [k for k in sim if "compressor" in k.lower()]
            if comp_keys:
                parts.append(f"\n#### ⚙️ Estado do Compressor")
                for k in comp_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
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
            
            temp_keys = [k for k in sim if "temperature" in k.lower() and k not in power_keys]
            if temp_keys:
                parts.append(f"\n#### 🌡️ Dados de Recuperação Térmica")
                for k in temp_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
            shown_keys = set(door_keys + comp_keys + power_keys + temp_keys)
            other_keys = [k for k in sim if k not in shown_keys]
            if other_keys:
                parts.append(f"\n#### 📈 Outros Indicadores")
                for k in other_keys:
                    parts.append(f"  - {k}: {sim[k]}")
            
            door_events = sim.get("door_open_events_last_hour", 0)
            if isinstance(door_events, (int, float)) and door_events > 10:
                parts.append(f"\n⚠️ ATENÇÃO: {door_events} aberturas de porta na última hora é considerado USO INTENSO (>10/hora). Investigue se isso explica a anomalia de temperatura antes de considerar falha técnica.")
            
            comp_status = sim.get("compressor_status", "")
            comp_amps = sim.get("compressor_current_amps")
            comp_max = sim.get("compressor_max_rated_amps")
            comp_ok = (
                comp_status == "running"
                and comp_amps is not None
                and comp_max is not None
                and comp_amps <= comp_max
            )
            if comp_status == "running" and comp_amps and comp_max:
                if comp_amps <= comp_max:
                    parts.append(f"ℹ️ NOTA: Compressor operando normalmente (corrente {comp_amps}A dentro do máximo {comp_max}A). Se está em alta carga, pode estar COMPENSANDO entrada de calor, não falhando.")
            
            # Explicit OBSERVE pattern recommendation when operational cause detected
            # Operational root cause detection (doors + climate)
            is_hot_weather = False
            try:
                # Logic to check if any ambient sensor is above 30°C
                ambient_temp = sim.get("ambient_temperature_c") or sim.get("outdoor_temperature_c")
                if ambient_temp and ambient_temp > 30:
                    is_hot_weather = True
            except Exception: pass

            if (isinstance(door_events, (int, float)) and door_events > 10 
                    or is_hot_weather) and comp_ok:
                parts.append(
                    f"\n🚨 DIAGNÓSTICO OPERACIONAL: "
                    f"{f'{int(door_events)} aberturas/hora' if door_events > 10 else ''} "
                    f"{' + ' if door_events > 10 and is_hot_weather else ''}"
                    f"{f'Clima Externo {ambient_temp}°C' if is_hot_weather else ''}. "
                    f"\nO equipamento está saudável (Compressor OK, Corrente {comp_amps}A). "
                    f"\nConforme REGRA OBRIGATÓRIA, você DEVE escolher ação `observe` e "
                    f"recomendar aguardar a estabilização térmica natural. "
                    f"NÃO gere notificação ou chamado técnico para este sintoma operacional."
                )
            
            power_history = sim.get("power_cycle_history", [])
            if power_history and len(power_history) >= 3:
                avg_off = sum(c.get("off_duration_hours", 0) for c in power_history) / len(power_history)
                parts.append(f"\n🚨 INVESTIGAR: Detectado histórico de {len(power_history)} ciclos de liga/desliga nos últimos dias, com média de {avg_off:.1f}h desligado por noite. VERIFIQUE se o operador está desligando o equipamento intencionalmente e avalie o impacto no consumo de energia, vida útil do compressor e segurança alimentar.")
            
            primary_temp = sim.get("primary_temp_sensor_c")
            secondary_temp = sim.get("secondary_temp_probe_c")
            comp_pct = sim.get("compressor_run_time_pct", 0)
            if primary_temp is not None and secondary_temp is not None:
                delta_sensors = abs(primary_temp - secondary_temp)
                if delta_sensors > 10:
                    parts.append(f"\n🔍 CONTRADIÇÃO DETECTADA: Sensor principal lê {primary_temp}°C mas sonda de produto lê {secondary_temp}°C (diferença de {delta_sensors:.1f}°C). Verifique se o sensor principal está calibrado ou com defeito.")
            if primary_temp is not None and comp_pct < 50 and primary_temp > -11:
                parts.append(f"⚠️ INCONSISTÊNCIA: Sensor principal indica {primary_temp}°C (alarme) mas compressor opera a apenas {comp_pct}% da capacidade. Se a temperatura fosse realmente {primary_temp}°C, o compressor deveria estar >90%. Considere falha do sensor.")

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

            maint_history = sim.get("maintenance_history", [])
            for visit in maint_history:
                deferred = visit.get("deferred_items", [])
                if deferred:
                    parts.append(f"\n📋 MANUTENÇÃO ADIADA ({visit.get('date', '?')}): {', '.join(deferred)}. Verifique se este item adiado é a CAUSA RAIZ do problema atual.")

            voltage = sim.get("power_supply_voltage_v")
            nominal = sim.get("power_supply_voltage_nominal_v", 220)
            building_affected = sim.get("building_other_equipment_affected", False)
            if voltage and nominal and voltage < nominal * 0.92:
                drop_pct = round((1 - voltage / nominal) * 100, 1)
                parts.append(f"\n⚡ SUBTENSÃO DETECTADA: Tensão em {voltage}V (nominal: {nominal}V, queda de {drop_pct}%). Isso reduz a capacidade do compressor mesmo que ele esteja 'rodando'.")
                if building_affected:
                    parts.append(f"🏢 CONFIRMAÇÃO: Outros equipamentos do prédio TAMBÉM estão afetados (HVAC: {sim.get('building_hvac_status', '?')}, luzes piscando: {sim.get('building_lighting_flickering', '?')}). A causa é da REDE ELÉTRICA, não do equipamento.")

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
    
    if oracle_prediction:
        parts.extend([
            f"\n## Predição ML (Oracle API)",
            f"- Prob. falha: {oracle_prediction.get('predicted_value', 'N/A')}",
            f"- Confiança: {oracle_prediction.get('confidence', 'N/A')}",
            f"- Impacto financeiro: R${oracle_prediction.get('financial_impact', 0):,.2f}",
            f"- Recomendação: {oracle_prediction.get('recommendation', 'N/A')}",
        ])
        
        imp = oracle_prediction.get('financial_impact', 0.0)
        limit = 10000.0
        if imp > 0:
            pct = (imp / limit) * 100
            if pct > 90:
                financial_anchor = f"⚠️ CRITICAL: Impacto ${imp:.2f} consome {pct:.1f}% do budget diário. R_Fin deve ser > 0.90."
            elif pct > 50:
                 financial_anchor = f"⚠️ HIGH: Impacto ${imp:.2f} consome {pct:.1f}% do budget diário. R_Fin deve ser > 0.60."

    elif state.get("is_fast_track"):
        parts.append("\n## Oracle ML: BYPASS (fast-track por severidade)")

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

    # === Inject relevant guardrails into LLM context ===
    try:
        from caos.core.nodes.guardrails import get_guardrail_engine
        engine = get_guardrail_engine()
        severity_val = trigger.severity.value
        contract_tier = atlas.get("contract_tier", "")
        under_maint = atlas.get("under_maintenance", False)

        relevant = []
        for g in engine.guardrails:
            gid = g.get("id", "")
            gsev = g.get("severity", "")
            gcat = g.get("category", "")

            if gsev == "BLOCKING":
                relevant.append(g)
            elif gsev == "HIGH" and severity_val in ("CRITICAL", "HIGH"):
                relevant.append(g)
            elif gcat == "CONTRACTUAL" and contract_tier:
                relevant.append(g)
            elif under_maint and ("maintenance" in g.get("condition", "").lower() or "loto" in gid.lower()):
                relevant.append(g)

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
        pass

    # === RAG Context Retrieval ===
    try:
        rag = get_rag_engine()
        
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

        query_text = f"{trigger.metric} {trigger.value} issue troubleshooting"
        retrieved_chunks = rag.query(query_text, k=1)
        
        if retrieved_chunks:
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
        logger.warning("oracle_rag_failed", error=str(e))

    # Recycle context
    prior_violations = state.get("guardrail_violations", [])
    recycle_count = state.get("recycle_count", 0)
    if recycle_count > 0 and prior_violations:
        parts.append("\n## ⚠️ REPLANEJAMENTO (Guardrails vetaram ação anterior)")
        parts.append("As seguintes regras de segurança foram violadas:")
        for v in prior_violations[:5]:
            # violations are rule ID strings (e.g. "PHYS_008"), not dicts
            if isinstance(v, str):
                parts.append(f"- [{v}]")
            elif isinstance(v, dict):
                parts.append(f"- [{v.get('id', '?')}] {v.get('description', v.get('message', 'N/A'))} (severity: {v.get('severity', '?')})")
            else:
                parts.append(f"- {v}")
        parts.append("Proponha uma ação MENOS agressiva que respeite essas restrições.")

    parts.append("\nAnalise a situação e responda no formato especificado.")
    return "\n".join(parts)


def parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse structured LLM response into risk dimensions."""
    result = {
        "confidence": 0.7,
        "physical": 0.5,
        "financial": 0.3,
        "contractual": 0.1,
        "communication": 0.1,
        "action": "notification",
        "analysis": "",
        "justification": "",
        "thought": "",
        "risk_justifications": {},
        "data_sources_consulted": [],
        "hypotheses_discarded": [],
    }

    KEYWORDS = {
        "PENSAMENTO:", "ANÁLISE:", "CONFIANÇA:", "RISCO_FISICO:",
        "RISCO_FINANCEIRO:", "RISCO_CONTRATUAL:", "RISCO_COMUNICACAO:",
        "AÇÃO:", "JUSTIFICATIVA:", "SOLICITAR:", "DADOS_CONSULTADOS:",
        "HIPOTESES_DESCARTADAS:",
    }

    def _extract_score(line_value: str) -> float:
        raw = line_value.split("|")[0].strip()
        return float(raw)

    def _extract_justification(line_value: str) -> str:
        parts_j = line_value.split("|", 1)
        if len(parts_j) > 1:
            just = parts_j[1].strip()
            if ":" in just:
                just = just.split(":", 1)[1].strip()
            return just
        return ""

    def _is_keyword_line(line: str) -> bool:
        return any(line.startswith(kw) for kw in KEYWORDS)

    lines = response_text.strip().split("\n")
    
    current_block = None
    block_lines: list[str] = []
    blocks: dict[str, str] = {}
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_block:
                block_lines.append("")
            continue
        
        if _is_keyword_line(stripped):
            if current_block and block_lines:
                blocks[current_block] = "\n".join(block_lines)
            
            for kw in KEYWORDS:
                if stripped.startswith(kw):
                    current_block = kw.rstrip(":")
                    content = stripped[len(kw):].strip()
                    block_lines = [content] if content else []
                    break
        elif current_block:
            block_lines.append(stripped)
    
    if current_block and block_lines:
        blocks[current_block] = "\n".join(block_lines)
    
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
    if "DADOS_CONSULTADOS" in blocks:
        result["data_sources_consulted"] = [
            s.strip().lstrip("- ") for s in blocks["DADOS_CONSULTADOS"].split("\n") if s.strip()
        ]
    if "HIPOTESES_DESCARTADAS" in blocks:
        result["hypotheses_discarded"] = [
            s.strip().lstrip("- ") for s in blocks["HIPOTESES_DESCARTADAS"].split("\n") if s.strip()
        ]
    
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

    if not result.get("analysis") and result.get("thought"):
        result["analysis"] = result["thought"]

    if not result.get("justification") or result["justification"].strip() == "":
        analysis = result.get("analysis", "")
        action = result.get("action", "notification")
        if analysis:
            summary = analysis[:300].rstrip()
            if len(analysis) > 300:
                summary += "..."
            result["justification"] = f"Ação recomendada: {action}. {summary}"

    # H10: Warn when parser falls back to defaults (possible LLM hallucination)
    if not blocks:
        logger.warning(
            "parse_llm_response_no_keywords",
            response_length=len(response_text),
            preview=response_text[:200],
            defaults_used=True,
        )
    elif result["action"] == "notification" and "AÇÃO" not in blocks:
        logger.warning(
            "parse_llm_response_no_action_keyword",
            blocks_found=list(blocks.keys()),
            response_length=len(response_text),
        )

    return result


# =============================================
# Fallback: cálculo por código (sem LLM)
# =============================================

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

    r_f = 0.0
    if metric in TEMPERATURE_METRICS and value is not None:
        max_temp = atlas.get("max_operating_temp")
        if max_temp and max_temp > 0:
            temp_ratio = value / max_temp
            if temp_ratio <= 0.8:
                r_f = temp_ratio * 0.2
            elif temp_ratio <= 1.0:
                r_f = 0.16 + (temp_ratio - 0.8) * 1.7
            elif temp_ratio <= 1.5:
                r_f = 0.50 + (temp_ratio - 1.0) * 0.6
            else:
                r_f = min(1.0, 0.80 + (temp_ratio - 1.5) * 0.4)
        else:
            r_f = 0.3
    elif metric in GPS_METRICS and value is not None:
        if value > 5000:
            r_f = 0.95
        elif value > 1000:
            r_f = 0.70
        elif value > 200:
            r_f = 0.40
        elif value > 50:
            r_f = 0.15
        else:
            r_f = 0.05
    elif value is not None and atlas.get("max_operating_temp"):
        max_val = atlas["max_operating_temp"]
        if max_val > 0:
            ratio = value / max_val
            r_f = min(1.0, max(0.0, (ratio - 0.8) / 0.4))

    if trigger.severity == Severity.CRITICAL:
        r_f = max(r_f, 0.85)
    elif trigger.severity == Severity.HIGH:
        r_f = max(r_f, 0.50)
    elif trigger.severity == Severity.MEDIUM:
        r_f = max(r_f, 0.20)

    r_fin = 0.0
    atlas_ext = atlas.get("telemetry_extension") or atlas.get("current_state") or {}
    oracle = state.get("oracle_forecast")
    if atlas_ext.get("products_relocated"):
        r_fin = 0.1
    elif oracle and oracle.get("financial_impact"):
        r_fin = min(1.0, oracle["financial_impact"] / 10000.0)
    elif trigger.severity in [Severity.CRITICAL, Severity.HIGH]:
        r_fin = 0.5
    elif trigger.severity == Severity.MEDIUM:
        r_fin = 0.25

    r_c = 0.0
    contract_tier = atlas.get("contract_tier", "")
    if contract_tier == "MONITORING_ONLY":
        r_c = 0.8
    elif contract_tier == "ON_TRADE":
        r_c = 0.3
    elif contract_tier == "PREMIUM":
        r_c = 0.5

    r_k = 0.1
    if trigger.severity in [Severity.CRITICAL, Severity.HIGH]:
        r_k = 0.3

    return RiskDimensions(physical=r_f, financial=r_fin, contractual=r_c, communication=r_k)


def should_bypass_oracle_prediction(state: JudgeState) -> bool:
    """Determine if Oracle ML prediction should be bypassed.
    
    The Oracle ALWAYS reasons (LLM + CODE), but the external ML prediction
    can be bypassed for latency/cost optimization.
    """
    settings = get_settings()
    trigger = state["trigger"]
    
    if trigger.severity == Severity.CRITICAL:
        logger.info("oracle_prediction_bypass_critical", event_id=trigger.event_id)
        return True
    
    if not settings.oracle_bypass_enabled:
        return False
    
    if trigger.severity == Severity.LOW:
        logger.info("oracle_prediction_bypass_low", event_id=trigger.event_id)
        return True
    
    if trigger.severity == Severity.MEDIUM and trigger.value is not None:
        atlas = state.get("atlas_context") or {}
        max_temp = atlas.get("max_operating_temp")
        if max_temp and max_temp > 0:
            ratio = trigger.value / max_temp
            if ratio < settings.oracle_bypass_threshold:
                logger.info(
                    "oracle_prediction_bypass_threshold",
                    event_id=trigger.event_id,
                    ratio=round(ratio, 3),
                )
                return True
    
    return False


async def oracle_node(state: JudgeState) -> dict[str, Any]:
    """Oracle Node: Full Deliberation (Reasoning + Prediction + Verdict).
    
    The Oracle is now the cognitive core. It:
    1. Optionally calls external Oracle API for ML predictions
    2. Calculates risk dimensions (CODE baseline)
    3. Runs LLM reasoning (Gemini) for deep analysis
    4. Applies dynamic confidence-based fusion (CODE + LLM)
    5. Applies verdict formula
    6. Classifies decision band and risk level
    
    CAOS Verifier will then audit this decision.
    """
    if state.get("sense_blocked"):
        return {}

    trigger = state["trigger"]
    logger.info("oracle_node_start", event_id=trigger.event_id)

    recycle_count = state.get("recycle_count", 0)
    prior_violations = state.get("guardrail_violations", []) if recycle_count > 0 else []
    if recycle_count > 0:
        logger.info(
            "oracle_recycle_replan",
            recycle_count=recycle_count,
            prior_violations=prior_violations,
        )

    # === Check for CAOS Verifier feedback (retry after rejection) ===
    verifier_feedback = state.get("verifier_feedback")
    verifier_retry_count = state.get("verifier_retry_count", 0)
    is_verifier_retry = verifier_feedback is not None and verifier_feedback.strip() != ""
    
    if is_verifier_retry:
        verifier_retry_count += 1
        logger.info(
            "oracle_verifier_retry",
            verifier_retry_count=verifier_retry_count,
            feedback_length=len(verifier_feedback),
        )

    # Preserve trace from previous pass so we can compare Pass 1 vs Pass 2
    previous_trace = list(state.get("reasoning_trace", []))
    reasoning_trace = list(previous_trace)  # carry forward
    
    # Annotate retry in the trace so it's clear this is a 2nd attempt
    if is_verifier_retry:
        reasoning_trace.append("═" * 50)
        reasoning_trace.append(f"🔄 [RETRY #{verifier_retry_count}] Oracle re-analisando após rejeição do CAOS")
        # Summarize the key feedback points
        feedback_lines = verifier_feedback.strip().split("\n")
        key_lines = [l.strip() for l in feedback_lines if l.strip().startswith(("❌", "✅", "⚠️", "PROPONHA"))]
        if key_lines:
            reasoning_trace.append("📋 Feedback recebido do CAOS:")
            for line in key_lines[:5]:
                reasoning_trace.append(f"   {line}")
        reasoning_trace.append("═" * 50)

    # === Step 0: Optional ML Prediction from external Oracle API ===
    oracle_forecast = None
    is_fast_track = False
    
    if should_bypass_oracle_prediction(state):
        logger.info("oracle_ml_bypassed", event_id=trigger.event_id)
        is_fast_track = True
        from caos.observability.metrics import get_metrics
        get_metrics().record_oracle(bypassed=True)
        reasoning_trace.append("📊 Oracle ML: BYPASS (fast-track por severidade)")
    else:
        oracle_client = get_oracle_client()
        try:
            oracle_forecast = await oracle_client.get_prediction(
                tenant_id=trigger.context.tenant_id,
                asset_id=trigger.context.asset_id,
                metric=trigger.metric or "failure_probability",
                current_value=trigger.value,
            )
            
            if oracle_forecast:
                confidence = oracle_forecast.get("confidence", 0.0)
                if confidence < 0.75:
                    logger.warning("oracle_low_confidence_ignored", confidence=confidence)
                    oracle_forecast = None
                    is_fast_track = True
                elif oracle_forecast.get("horizon_hours", 0) > 168:
                    logger.warning("oracle_horizon_too_far")
                    oracle_forecast = None
                    is_fast_track = True
                else:
                    reasoning_trace.append(
                        f"🔮 Oracle ML: predição recebida (confiança={confidence:.2f}, "
                        f"valor={oracle_forecast.get('predicted_value', 'N/A')})"
                    )
                    from caos.observability.metrics import get_metrics
                    get_metrics().record_oracle(bypassed=False)
                    from caos.safety.runtime import get_fail_safe
                    get_fail_safe().report_service_up("oracle")
            else:
                is_fast_track = True
        except Exception as e:
            logger.warning("oracle_prediction_error", error=str(e))
            from caos.safety.runtime import get_fail_safe
            get_fail_safe().report_service_down("oracle", str(e))
            is_fast_track = True

    # Store prediction in state for context building
    enriched_state = {
        **state,
        "oracle_forecast": oracle_forecast,
        "is_fast_track": is_fast_track,
    }

    # === Step 1: Hybrid Risk Calculation (CODE + LLM) ===
    llm = get_llm()
    llm_reasoning = None
    llm_response_empty = False
    atlas = state.get("atlas_context") or {}
    
    cb = get_circuit_breaker()
    if cb.is_tripped():
        llm = None
        reasoning_trace.append("⚡ Circuit breaker ativo: orçamento LLM diário excedido, usando modo código")
        logger.warning("circuit_breaker_tripped", status=cb.get_status())

    code_risk = calculate_risk_dimensions_code(enriched_state)

    if llm is not None:
        try:
            context_prompt = build_context_prompt(enriched_state)
            logger.debug("oracle_context_prompt_debug", prompt_length=len(context_prompt))
            
            oracle_system_cache = get_oracle_system_cache(llm)
            request_kwargs: dict[str, Any] = {}
            if oracle_system_cache:
                request_kwargs["cached_content"] = oracle_system_cache
                messages = [HumanMessage(content=context_prompt)]
            else:
                messages = [
                    SystemMessage(content=ORACLE_SYSTEM_PROMPT),
                    HumanMessage(content=context_prompt),
                ]

            if is_verifier_retry and verifier_feedback:
                reasoning_trace.append("♻️ Oracle recebeu FEEDBACK do CAOS para correção")
                
                caos_retry_msg = (
                    "\n\n" + "=" * 60 + "\n"
                    "⚠️ ATENÇÃO: O CAOS (Cognitive Autonomous Oversight System) NÃO VALIDOU sua decisão anterior.\n"
                    "O CAOS identificou lacunas e inconsistências na sua análise.\n"
                    "Refaça TODA sua análise considerando as lacunas apontadas abaixo.\n"
                    + "=" * 60 + "\n\n"
                    + verifier_feedback
                    + "\n\n" + "=" * 60 + "\n"
                    "INSTRUÇÕES: O CAOS apontou informações que você deixou de analisar e inconsistências no seu raciocínio.\n"
                    "Reveja os dados brutos, teste hipóteses que você não considerou, e chegue à sua própria conclusão.\n"
                    "O CAOS NÃO sugere ações — cabe a você decidir o que fazer com base na análise completa.\n"
                    + "=" * 60
                )
                
                messages.append(HumanMessage(content=caos_retry_msg))

            if oracle_system_cache:
                logger.debug("oracle_system_cache_used", cache_name=oracle_system_cache)

            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages, **request_kwargs),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                logger.warning("oracle_llm_timeout", timeout=120.0)
                raise TimeoutError("LLM timeout >120s — Reptilian Mode")

            # Record LLM token usage for circuit breaker budget tracking
            _record_llm_usage(cb, response, "oracle")

            llm_response_text = response.content
            
            if "SOLICITAR: CLIMA" in str(llm_response_text).upper():
                reasoning_trace.append("🔍 Oracle solicitou dados externos: CLIMA")
                
                from caos.integration.weather import get_weather_client
                weather_client = get_weather_client()
                
                location = atlas.get("location", "São Paulo")
                weather_data = await weather_client.get_forecast(location)
                
                reasoning_trace.append(f"🌤️ Dados do Clima recebidos: {weather_data.get('temperature')}°C, {weather_data.get('condition')}")
                
                messages.append(HumanMessage(content=f"DADOS RECEBIDOS: {weather_data}\nAgora forneça sua decisão final."))
                
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=120.0,
                )
                _record_llm_usage(cb, response, "oracle_weather")
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

            conf = llm_reasoning.get("confidence", 0.7)
            LLM_WEIGHT = max(0.1, min(0.9, conf))
            CODE_WEIGHT = 1.0 - LLM_WEIGHT
            fused = RiskDimensions(
                physical=LLM_WEIGHT * llm_risk.physical + CODE_WEIGHT * code_risk.physical,
                financial=LLM_WEIGHT * llm_risk.financial + CODE_WEIGHT * code_risk.financial,
                contractual=LLM_WEIGHT * llm_risk.contractual + CODE_WEIGHT * code_risk.contractual,
                communication=LLM_WEIGHT * llm_risk.communication + CODE_WEIGHT * code_risk.communication,
            )

            risk_dimensions = RiskDimensions(
                physical=max(fused.physical, code_risk.physical),
                financial=fused.financial,
                contractual=fused.contractual,
                communication=max(fused.communication, code_risk.communication),
            )

            _audit_delta = {
                "physical":      round(llm_risk.physical - code_risk.physical, 3),
                "financial":     round(llm_risk.financial - code_risk.financial, 3),
                "contractual":   round(llm_risk.contractual - code_risk.contractual, 3),
                "communication": round(llm_risk.communication - code_risk.communication, 3),
            }
            logger.info(
                "oracle_hybrid_audit",
                llm_risk={"R_F": llm_risk.physical, "R_Fin": llm_risk.financial,
                          "R_C": llm_risk.contractual, "R_K": llm_risk.communication},
                code_risk={"R_F": code_risk.physical, "R_Fin": code_risk.financial,
                           "R_C": code_risk.contractual, "R_K": code_risk.communication},
                fused_risk={"R_F": risk_dimensions.physical, "R_Fin": risk_dimensions.financial,
                            "R_C": risk_dimensions.contractual, "R_K": risk_dimensions.communication},
                delta=_audit_delta,
            )

            thought = llm_reasoning.get('thought', '')
            if thought:
                reasoning_trace.append(f"🧠 Cadeia de Pensamento:\n{thought}")
            
            reasoning_trace.append(f"🤖 Oracle LLM: {llm_reasoning.get('analysis', '')}")
            reasoning_trace.append(f"💡 Ação sugerida: {llm_reasoning.get('action', 'N/A')}")
            reasoning_trace.append(f"📋 {llm_reasoning.get('justification', '')}")
            
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
                "oracle_llm_success",
                analysis=llm_reasoning.get("analysis", ""),
                action=llm_reasoning.get("action", ""),
            )

        except Exception as e:
            logger.warning("oracle_llm_fallback", error=str(e))
            risk_dimensions = code_risk
            llm_reasoning = None
            reasoning_trace.append(f"⚠️ LLM indisponível, usando cálculo por código: {e}")
    else:
        risk_dimensions = code_risk
        reasoning_trace.append("📊 Modo código: LLM não configurada")

    _extra_state: dict[str, Any] = {
        "_llm_response_empty": llm_response_empty,
    }

    # === Detect operational root cause (door excess pattern) ===
    # When temperature elevation is caused by excessive door openings
    # (operational behaviour, not technical failure) AND the compressor is
    # healthy, the correct action is OBSERVE (wait for stabilisation).
    sim_raw = atlas.get("simulation_data")
    sim_data = sim_raw if isinstance(sim_raw, dict) else {}
    _door_events = sim_data.get("door_open_events_last_hour", 0)
    _comp_status = str(sim_data.get("compressor_status") or "").lower()
    _comp_amps = sim_data.get("compressor_current_amps")
    _comp_max = sim_data.get("compressor_max_rated_amps")
    _compressor_ok = (
        _comp_status == "running"
        and _comp_amps is not None
        and _comp_max is not None
        and _comp_amps <= _comp_max
    )

    # Enhanced operational detection logic
    _ambient_temp = sim_data.get("ambient_temperature_c") or sim_data.get("outdoor_temperature_c")
    _is_hot = _ambient_temp is not None and _ambient_temp > 30

    if (isinstance(_door_events, (int, float)) and _door_events > 10 or _is_hot) and _compressor_ok:
        _extra_state["operational_root_cause"] = "door_excess_or_ambient_heat"
        
        # Check if LLM reached the correct conclusion (OBSERVE)
        _llm_chose_observe = llm_reasoning and llm_reasoning.get("action") in (
            "observe", "observar", "aguardar", "monitorar", "reavaliar",
        )
        
        reasoning_trace.append(
            f"🚪 Causa operacional/ambiental detectada: "
            f"{f'{int(_door_events)} aberturas/hora' if _door_events > 10 else ''} "
            f"{' + ' if _door_events > 10 and _is_hot else ''}"
            f"{f'Clima {_ambient_temp}°C' if _is_hot else ''} "
            f"→ {'LLM escolheu OBSERVE ✅' if _llm_chose_observe else 'FORÇANDO OBSERVE (Code Logic) 🛡️'}"
        )
        
        # If LLM failed, we'll force the action in the state so act.py uses it
        if not _llm_chose_observe and llm_reasoning:
            logger.warning("oracle_llm_action_override", original=llm_reasoning.get("action"), forced="observe")
            # We update reasoning_trace to make it clear.
            reasoning_trace.append("⚠️ Ação foi ajustada para OBSERVE para evitar intervenção técnica desnecessária.")
        logger.info(
            "operational_root_cause_detected",
            cause="door_excess",
            door_events=_door_events,
            compressor_amps=_comp_amps,
            compressor_max=_comp_max,
            llm_chose_observe=_llm_chose_observe,
        )

    # === Recycle dampening (M13: per-axis, not uniform) ===
    # Physical risk gets minimal dampening to avoid dangerous sub-reaction.
    # Financial/communication get standard dampening. Contractual is moderate.
    if recycle_count > 0:
        dampen_physical = 0.95       # Almost no reduction — safety first
        dampen_financial = 0.75      # Standard reduction
        dampen_contractual = 0.85    # Moderate — contractual penalties are real
        dampen_communication = 0.70  # Most dampened — least critical
        risk_dimensions = RiskDimensions(
            physical=risk_dimensions.physical * dampen_physical,
            financial=risk_dimensions.financial * dampen_financial,
            contractual=risk_dimensions.contractual * dampen_contractual,
            communication=risk_dimensions.communication * dampen_communication,
        )
        reasoning_trace.append(
            f"♻️ Recycle #{recycle_count}: riscos ajustados por eixo "
            f"(R_F×{dampen_physical}, R_Fin×{dampen_financial}, R_C×{dampen_contractual}, R_K×{dampen_communication}) "
            f"para respeitar guardrails vetados: {[v if isinstance(v, str) else v.get('id','?') for v in prior_violations]}"
        )

    # === Step 2: Calculate A and O scores ===
    a_signals = 0
    a_total = 5
    if atlas.get("current_state") and len(atlas["current_state"]) > 0:
        a_signals += 1
    if atlas.get("manual_excerpts") and len(atlas["manual_excerpts"]) > 0:
        a_signals += 1
    if atlas.get("allowed_actions") and len(atlas["allowed_actions"]) > 0:
        a_signals += 1
    if atlas.get("contract_tier"):
        a_signals += 1
    if atlas.get("max_operating_temp") is not None:
        a_signals += 1
    a_score = a_signals / a_total
    if atlas.get("under_maintenance"):
        a_score = 0.0

    metric = (trigger.metric or "").lower()
    atlas_state = atlas.get("current_state") or {}
    if metric and trigger.value is not None:
        atlas_key = metric.replace("compressor_", "").replace("cabinet_", "")
        atlas_val = atlas_state.get(atlas_key) or atlas_state.get(metric)
        if atlas_val is not None:
            discrepancy = abs(trigger.value - atlas_val)
            threshold = max(trigger.value, atlas_val) * 0.15
            if discrepancy > threshold:
                reasoning_trace.append(
                    f"⚠️ Discrepância detectada: trigger={trigger.value} vs Atlas={atlas_val} "
                    f"(Δ={discrepancy:.1f}, tolerância={threshold:.1f}). "
                    f"Dados do Atlas podem estar desatualizados."
                )
                a_score = max(0.3, a_score * 0.6)
                logger.warning(
                    "oracle_data_discrepancy",
                    trigger_value=trigger.value,
                    atlas_value=atlas_val,
                    delta=discrepancy,
                )

    o_score = 1.0
    if oracle_forecast:
        o_score = oracle_forecast.get("confidence", 0.5)
    elif is_fast_track:
        o_score = 1.0

    # === Step 3: Verdict ===
    tenant_id = None
    if state["trigger"].context:
        tenant_id = state["trigger"].context.tenant_id
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
        "oracle_node_complete",
        event_id=trigger.event_id,
        verdict_score=result.verdict_score,
        decision_band=result.decision_band.value,
        risk_level=result.risk_level.value,
        used_llm=llm_reasoning is not None,
    )

    # === RLHF Recording ===
    try:
        from caos.core.rlhf import get_rlhf_optimizer, ExperienceRecord
        settings = get_settings()
        if settings.rlhf_enabled:
            optimizer = get_rlhf_optimizer()
            exp = ExperienceRecord(
                action_id=state.get("action_id", f"act_{trigger.event_id}"),
                event_id=trigger.event_id,
                tenant_id=tenant_id or "default",
                asset_id=trigger.context.asset_id if trigger.context else "unknown",
                
                atlas_score=a_score,
                oracle_score=o_score,
                severity_score=result.severity_score,
                risk_physical=risk_dimensions.physical,
                risk_financial=risk_dimensions.financial,
                risk_contractual=risk_dimensions.contractual,
                risk_communication=risk_dimensions.communication,
                guardrail_penalty=0.0,
                
                verdict_score=result.verdict_score,
                decision_band=result.decision_band.value,
                risk_level=result.risk_level.value,
                
                weights_used=judge.get_weights_used() if hasattr(judge, "get_weights_used") else {}
            )
            optimizer.record_experience(exp)
    except Exception as e:
        logger.warning("oracle_rlhf_record_failed", error=str(e))

    return {
        "oracle_forecast": oracle_forecast,
        "is_fast_track": is_fast_track,
        "risk_dimensions": risk_dimensions,
        "atlas_score": a_score,
        "oracle_score": o_score,
        "severity_score": result.severity_score,
        "verdict_score": result.verdict_score,
        "decision_band": result.decision_band,
        "risk_level": result.risk_level,
        "reasoning_trace": reasoning_trace,
        "llm_analysis": llm_reasoning,
        # Track verifier retry count (increment on feedback retry)
        "verifier_retry_count": verifier_retry_count,
        # Clear feedback after consuming it (so CAOS can generate new if needed)
        "verifier_feedback": None,
        **_extra_state,
    }
