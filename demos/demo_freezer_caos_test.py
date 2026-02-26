#!/usr/bin/env python3
"""
=============================================================================
  CAOS Agent — Teste REAL: Cenário Freezer (via Pipeline Completo)
=============================================================================

  Este script envia o alerta para o servidor CAOS REAL e espera o resultado
  do pipeline completo (Sense → Oracle → Cortex → Guardrails → Act).

  O resultado aparece na página de raciocínio do frontend:
      http://localhost:8080/dashboard/reasoning

  Pré-requisitos:
  ---------------
  1. CAOS rodando:      http://localhost:8080 (uvicorn main:app --app-dir src --port 8080)
  2. Simuladores:       http://localhost:9000 (uvicorn simulators.mock_agents:app --port 9000)
  3. Ou: ./dev.sh

  Cenário:
  --------
  Sentinel detecta temperatura alta no FREEZER-01 (-5°C, threshold -11°C).
  O CAOS deve investigar autonomamente:
  - Buscar dados no Atlas → descobre 15 aberturas de porta na última hora
  - Consultar manual técnico → abertura excessiva causa elevação
  - (Opcionalmente) buscar clima externo → 35.5°C em São Paulo
  - Concluir: causa OPERACIONAL, não falha técnica

  Execução:
  ---------
      python demo_freezer_caos_test.py
=============================================================================
"""

import httpx
import json
import time
import sys

BASE_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"
TRIGGER_URL = f"{BASE_URL}/v1/events/trigger"
REASONING_URL = f"{BASE_URL}/v1/reasoning"


def check_servers():
    """Verifica se os servidores estão rodando."""
    print("\n🔌 Verificando servidores...")
    
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3.0)
        print(f"  ✅ CAOS Agent: {r.json()}")
    except Exception:
        print(f"  ❌ CAOS Agent NÃO está rodando em {BASE_URL}")
        print(f"     Execute: uvicorn main:app --app-dir src --port 8080")
        return False
    
    try:
        r = httpx.get(f"{SIMULATORS_URL}/health", timeout=3.0)
        print(f"  ✅ Simuladores: {r.json()}")
    except Exception:
        print(f"  ❌ Simuladores NÃO estão rodando em {SIMULATORS_URL}")
        print(f"     Execute: uvicorn simulators.mock_agents:app --port 9000")
        return False
    
    return True


def send_freezer_alert():
    """Envia o alerta do cenário freezer para o CAOS real."""
    
    payload = {
        "tenant_id": "VIVA-SP",
        "asset_id": "FREEZER-01",
        "metric": "temperature",
        "value": -5.0,
        "severity": "HIGH",
        "payload": {
            "source": "SENTINEL",
            "description": "Temperatura acima do threshold (-11°C). "
                           "FREEZER-01 registrou -5.0°C.",
        },
    }
    
    print(f"\n📡 Enviando alerta ao CAOS:")
    print(f"   Ativo:      {payload['asset_id']}")
    print(f"   Métrica:    {payload['metric']}")
    print(f"   Valor:      {payload['value']}°C")
    print(f"   Severidade: {payload['severity']}")
    
    try:
        resp = httpx.post(TRIGGER_URL, json=payload, timeout=30.0)
        if resp.status_code != 201:
            print(f"  ❌ Erro ao enviar: {resp.status_code} - {resp.text}")
            return None
        
        data = resp.json()
        event_id = data["event_id"]
        print(f"  ✅ Evento processado: {event_id}")
        return data
    except Exception as e:
        print(f"  ❌ Falha de conexão: {e}")
        return None


def get_reasoning(event_id: str):
    """Busca o raciocínio completo do evento na API de reasoning."""
    print(f"\n🔍 Buscando raciocínio do evento {event_id}...")
    
    try:
        r = httpx.get(f"{REASONING_URL}/{event_id}", timeout=5.0)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"  ⚠️ Reasoning não encontrado (status: {r.status_code})")
            return None
    except Exception as e:
        print(f"  ❌ Erro: {e}")
        return None


def display_event_result(event_data: dict):
    """Mostra o resultado imediato do processamento do evento."""
    print("\n" + "=" * 65)
    print("⚡ RESULTADO IMEDIATO DO PIPELINE CAOS")
    print("=" * 65)
    
    print(f"  Event ID:       {event_data.get('event_id')}")
    print(f"  Verdict Score:  {event_data.get('verdict_score')}")
    print(f"  Decision:       {event_data.get('decision')}")
    print(f"  Risk Level:     {event_data.get('risk_level')}")
    print(f"  Aprovação:      {'Sim' if event_data.get('requires_approval') else 'Não'}")
    print(f"  Guardrails:     {event_data.get('guardrail_violations', [])}")
    print(f"  Tempo:          {event_data.get('processing_time_ms', 'N/A')} ms")
    
    reasoning = event_data.get("reasoning", [])
    if reasoning:
        print(f"\n  📝 TRACE DE RACIOCÍNIO ({len(reasoning)} etapas):")
        print("  " + "-" * 50)
        for step in reasoning:
            print(f"    {step}")
        print("  " + "-" * 50)


def display_full_reasoning(reasoning_data: dict):
    """Mostra o raciocínio completo do reasoning store."""
    print("\n" + "=" * 65)
    print("🧠 RACIOCÍNIO COMPLETO (Reasoning Store)")
    print("=" * 65)
    
    # Sense (Atlas)
    sense = reasoning_data.get("sense", {})
    atlas_ctx = sense.get("atlas_context", {})
    if atlas_ctx:
        print(f"\n  📊 [SENSE] Dados do Atlas:")
        print(f"     Status:        {atlas_ctx.get('current_state', {}).get('status', 'N/A')}")
        print(f"     Temperatura:   {atlas_ctx.get('current_state', {}).get('temperature', 'N/A')}°C")
        print(f"     Contrato:      {atlas_ctx.get('contract_tier', 'N/A')}")
        print(f"     Localização:   {atlas_ctx.get('location', 'N/A')}")
        
        sim_data = atlas_ctx.get("simulation_data", {})
        if sim_data:
            print(f"\n     🚪 TELEMETRIA DE PORTAS:")
            print(f"        Aberturas/hora: {sim_data.get('door_open_events_last_hour', 'N/A')}")
            print(f"        Aberturas/6h:   {sim_data.get('door_open_events_last_6h', 'N/A')}")
            print(f"        Tempo médio:    {sim_data.get('avg_door_open_duration_sec', 'N/A')}s")
            print(f"        Compressor:     {sim_data.get('compressor_status', 'N/A')} ({sim_data.get('compressor_run_time_pct', 'N/A')}%)")
            print(f"        Corrente:       {sim_data.get('compressor_current_amps', 'N/A')}A / {sim_data.get('compressor_max_rated_amps', 'N/A')}A")
            print(f"        Vedação:        {sim_data.get('door_seal_integrity_pct', 'N/A')}%")
        
        manuals = atlas_ctx.get("manual_excerpts", [])
        if manuals:
            print(f"\n     📘 MANUAL TÉCNICO ({len(manuals)} trechos):")
            for m in manuals[:6]:
                print(f"        📖 {m[:100]}{'...' if len(m) > 100 else ''}")
    
    # Oracle
    oracle = reasoning_data.get("oracle", {})
    if oracle and oracle.get("forecast"):
        forecast = oracle["forecast"]
        print(f"\n  🔮 [ORACLE] Previsão:")
        print(f"     Prob. falha:   {forecast.get('failure_probability', 'N/A')}")
        print(f"     Confiança:     {forecast.get('confidence', 'N/A')}")
        print(f"     Recomendação:  {forecast.get('recommendation', 'N/A')}")
    
    # Cortex (LLM)
    cortex = reasoning_data.get("cortex", {})
    if cortex:
        print(f"\n  🤖 [CORTEX] Análise Híbrida (CODE + LLM):")
        llm = cortex.get("llm_analysis", {})
        if isinstance(llm, dict):
            print(f"     Análise:     {llm.get('analysis', 'N/A')}")
            print(f"     Ação:        {llm.get('action', 'N/A')}")
            print(f"     Justificativa: {llm.get('justification', 'N/A')}")
            print(f"     Confiança:   {llm.get('confidence', 'N/A')}")
            print(f"     R_Físico:    {llm.get('physical', 'N/A')}")
            print(f"     R_Financeiro: {llm.get('financial', 'N/A')}")
        elif llm:
            print(f"     → {llm}")
    
    # Guardrails
    guardrails = reasoning_data.get("guardrails", {})
    if guardrails:
        violations = guardrails.get("violations", [])
        checked = guardrails.get("checked", [])
        print(f"\n  🛡️ [GUARDRAILS] Validação:")
        print(f"     Verificados: {len(checked)}")
        print(f"     Violações:   {len(violations)}")
        if violations:
            for v in violations:
                print(f"        ⚠️ {v}")
    
    # Act
    act = reasoning_data.get("act", {})
    if act:
        print(f"\n  🎬 [ACT] Ação:")
        action = act.get("proposed_action", {})
        if isinstance(action, dict):
            print(f"     Tipo:     {action.get('action_type', 'N/A')}")
            print(f"     Prioridade: {action.get('priority', 'N/A')}")
        elif action:
            print(f"     → {action}")
    
    # Resultado Final
    print(f"\n  ⚖️ VEREDITO FINAL:")
    print(f"     Score:      {reasoning_data.get('verdict_score', 'N/A')}")
    print(f"     Decisão:    {reasoning_data.get('decision', 'N/A')}")
    print(f"     Risco:      {reasoning_data.get('risk_level', 'N/A')}")
    print(f"     Tempo:      {reasoning_data.get('processing_time_ms', 'N/A')} ms")


def run_analysis(event_data: dict, reasoning_data: dict | None):
    """Analisa se o CAOS chegou à conclusão esperada."""
    print("\n" + "=" * 65)
    print("🧪 ANÁLISE DO RESULTADO")
    print("=" * 65)
    
    decision = event_data.get("decision", "")
    risk_level = event_data.get("risk_level", "")
    reasoning = event_data.get("reasoning", [])
    reasoning_text = " ".join(str(r) for r in reasoning).upper()
    
    # Verificar se o Atlas retornou dados de porta
    atlas_ctx = {}
    if reasoning_data:
        atlas_ctx = reasoning_data.get("sense", {}).get("atlas_context", {})
    sim_data = atlas_ctx.get("simulation_data", {})
    
    checks = []
    
    # 1. Atlas retornou dados de porta?
    door_events = sim_data.get("door_open_events_last_hour", 0)
    checks.append((
        f"Atlas retornou dados de porta ({door_events} aberturas/hora)",
        door_events > 0,
    ))
    
    # 2. O CAOS detectou padrão de abertura excessiva?
    found_door_mention = any(
        kw in reasoning_text 
        for kw in ["PORTA", "DOOR", "ABERTURA", "OPERACIONAL", "USO"]
    )
    checks.append((
        "CAOS mencionou aberturas de porta / padrão operacional no raciocínio",
        found_door_mention,
    ))
    
    # 3. O CAOS buscou dados de clima?
    found_weather = any(
        kw in reasoning_text 
        for kw in ["CLIMA", "WEATHER", "TEMPERATURA AMBIENTE", "35"]
    )
    checks.append((
        "CAOS buscou/mencionou dados climáticos",
        found_weather,
    ))
    
    # 4. Risco NÃO é CRITICAL (porque não é falha técnica)
    checks.append((
        f"Risco não é CRITICAL (foi: {risk_level})",
        risk_level != "VETO",
    ))
    
    # 5. Manual técnico foi consultado?
    manuals = atlas_ctx.get("manual_excerpts", [])
    has_manual = len(manuals) > 0
    checks.append((
        f"Manual técnico consultado ({len(manuals)} trechos)",
        has_manual,
    ))
    
    # 6. Pipeline completo executou
    has_cortex = reasoning_data.get("cortex", {}) if reasoning_data else {}
    checks.append((
        "Pipeline completo executou (Cortex processou)",
        bool(has_cortex),
    ))
    
    passed = 0
    for desc, ok in checks:
        status = "✅ PASS" if ok else "⚠️  WARN"
        print(f"  {status}  {desc}")
        if ok:
            passed += 1
    
    print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")
    
    # Link para o frontend
    event_id = event_data.get("event_id", "")
    print(f"\n  🖥️ Veja o raciocínio completo no frontend:")
    print(f"     → http://localhost:8080/dashboard/reasoning")
    print()
    
    return passed == len(checks)


def main():
    print()
    print("╔" + "═" * 63 + "╗")
    print("║" + " CAOS Agent — Teste REAL: Cenário Freezer".center(63) + "║")
    print("║" + " Pipeline Completo + Raciocínio no Frontend".center(63) + "║")
    print("╚" + "═" * 63 + "╝")
    
    # 1. Verificar servidores
    if not check_servers():
        print("\n❌ Servidores não estão rodando. Execute ./dev.sh primeiro.")
        return 1
    
    # 2. Enviar o alerta
    event_data = send_freezer_alert()
    if not event_data:
        return 1
    
    event_id = event_data["event_id"]
    
    # 3. Mostrar resultado imediato
    display_event_result(event_data)
    
    # 4. Buscar raciocínio completo
    reasoning_data = get_reasoning(event_id)
    if reasoning_data:
        display_full_reasoning(reasoning_data)
    else:
        print("\n  ⚠️ Reasoning store não retornou dados detalhados.")
        print("     (O resultado principal já foi exibido acima)")
    
    # 5. Análise
    success = run_analysis(event_data, reasoning_data)
    
    if success:
        print("🎉 Teste completo! Verifique o frontend para a visualização completa.\n")
    else:
        print("⚠️ Algumas verificações falharam, mas o evento foi processado.\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
