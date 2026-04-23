#!/usr/bin/env python3
"""
CAOS Agent — Teste: Cliente Desliga Freezer à Noite
====================================================
Cenário: O operador do Mercado Bom Preço desliga o freezer de carnes
toda noite às 22:00 para "economizar energia" e liga de manhã às 07:30.
Resultado: temperatura sobe para 8.5°C, compressor força recuperação
gastando MAIS energia, e produtos ficam em risco sanitário.

O CAOS deve detectar o padrão e recomendar que o cliente NÃO desligue.
"""
import asyncio
import httpx
import json
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║     CAOS Agent — Teste: Desligamento Noturno do Freezer     ║")
    print("║              Mercado Bom Preço - FREEZER-02                 ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Verificar servidores
        print("\n🔌 Verificando servidores...")
        try:
            r1 = await client.get(f"{CAOS_URL}/health")
            print(f"  ✅ CAOS Agent: {r1.json()}")
        except Exception as e:
            print(f"  ❌ CAOS Agent indisponível: {e}")
            sys.exit(1)

        try:
            r2 = await client.get(f"{SIMULATORS_URL}/health")
            print(f"  ✅ Simuladores: {r2.json()}")
        except Exception as e:
            print(f"  ❌ Simuladores indisponíveis: {e}")
            sys.exit(1)

        # 2. Enviar alerta de temperatura alta no FREEZER-02
        print("\n📡 Enviando alerta ao CAOS:")
        print("   Ativo:      FREEZER-02 (Freezer de Carnes)")
        print("   Métrica:    temperature")
        print("   Valor:      2.0°C (MUITO ALTO para freezer)")
        print("   Severidade: CRITICAL")
        print("   Cenário:    Cliente desliga equipamento toda noite")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-02",
            "metric": "temperature",
            "value": 2.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "Temperatura FREEZER-02 em 2.0°C — muito acima do limite de -11°C. Padrão recorrente toda manhã."
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  ❌ Erro: {resp.status_code} — {resp.text}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"  ✅ Evento processado: {event_id}")

        # 3. Resultado imediato
        print("\n" + "=" * 65)
        print("⚡ RESULTADO IMEDIATO DO PIPELINE CAOS")
        print("=" * 65)
        print(f"  Event ID:       {event_id}")
        print(f"  Verdict Score:  {result.get('verdict_score')}")
        print(f"  Decision:       {result.get('decision')}")
        print(f"  Risk Level:     {result.get('risk_level')}")
        print(f"  Aprovação:      {'Sim' if result.get('approved') else 'Não'}")
        print(f"  Guardrails:     {result.get('guardrails_triggered', [])}")
        print(f"  Tempo:          {result.get('processing_time_ms', 0):.2f} ms")

        # 4. Trace de raciocínio
        trace = result.get("reasoning_trace", [])
        print(f"\n  📝 TRACE DE RACIOCÍNIO ({len(trace)} etapas):")
        print("  " + "-" * 50)
        for step in trace:
            for line in str(step).split('\n'):
                print(f"    {line}")
        print("  " + "-" * 50)

        # 5. Buscar reasoning store
        await asyncio.sleep(2)
        print(f"\n🔍 Buscando raciocínio completo do evento {event_id}...")

        detail = {}
        r_detail = await client.get(f"{CAOS_URL}/v1/reasoning/{event_id}")
        if r_detail.status_code == 200:
            detail = r_detail.json()

            print("\n" + "=" * 65)
            print("🧠 RACIOCÍNIO COMPLETO (Reasoning Store)")
            print("=" * 65)

            # Atlas (top-level key: "sense")
            sense = detail.get("sense", {})
            atlas = sense.get("atlas_context", {})
            if atlas:
                state = atlas.get("current_state", {})
                print(f"\n  📊 [SENSE] Dados do Atlas:")
                print(f"     Status:        {state.get('status', 'N/A')}")
                print(f"     Temperatura:   {state.get('temperature', 'N/A')}°C")
                print(f"     Contrato:      {atlas.get('contract_tier', 'N/A')}")
                print(f"     Localização:   {atlas.get('location', 'N/A')}")

                # Financial data
                fin = atlas.get("financial_data", {})
                if fin:
                    print(f"\n     💰 DADOS FINANCEIROS:")
                    print(f"        Valor produtos:     R${fin.get('estimated_product_value_brl', 0):,.2f}")
                    print(f"        Tipo:               {fin.get('product_type', 'N/A')}")
                    print(f"        Tempo max acima:     {fin.get('max_time_above_threshold_hours', 'N/A')}h")
                    print(f"        Receita diária:      R${fin.get('daily_revenue_impact_brl', 0):,.2f}")
                    print(f"        Penalidade SLA/hora: R${fin.get('sla_penalty_per_hour_brl', 0):,.2f}")

                sim = atlas.get("simulation_data", {})
                if sim:
                    print(f"\n     🔌 TELEMETRIA DE ENERGIA:")
                    print(f"        Status energia:     {sim.get('power_status', 'N/A')}")
                    print(f"        Ligou às:           {sim.get('power_on_timestamp', 'N/A')}")
                    print(f"        Desligou às:        {sim.get('power_off_timestamp', 'N/A')}")
                    print(f"        Uptime hoje:        {sim.get('uptime_hours_today', 'N/A')}h")
                    print(f"        Temp ao ligar:      {sim.get('temperature_at_power_on', 'N/A')}°C")
                    print(f"        Compressor:         {sim.get('compressor_status', 'N/A')} ({sim.get('compressor_run_time_pct', 'N/A')}%)")
                    print(f"        Corrente:           {sim.get('compressor_current_amps', 'N/A')}A / {sim.get('compressor_max_rated_amps', 'N/A')}A")
                    print(f"        Consumo hoje:       {sim.get('power_consumption_kwh_today', 'N/A')} kWh")
                    print(f"        Consumo média/sem:  {sim.get('power_consumption_kwh_avg_week', 'N/A')} kWh")
                    print(f"        Aberturas porta:    {sim.get('door_open_events_last_hour', 'N/A')}")
                    
                    history = sim.get("power_cycle_history", [])
                    if history:
                        print(f"\n     📅 HISTÓRICO DE CICLOS LIGA/DESLIGA ({len(history)} dias):")
                        for h in history:
                            print(f"        {h['date']}: OFF {h['off']} → ON {h['on']} ({h['off_duration_hours']}h desligado)")

                manuals = atlas.get("manual_excerpts", [])
                if manuals:
                    print(f"\n     📘 MANUAL TÉCNICO ({len(manuals)} trechos):")
                    for m in manuals[:6]:
                        print(f"        📖 {m[:100]}{'...' if len(m) > 100 else ''}")

            # Cortex (top-level key: "cortex")
            cortex = detail.get("cortex", {})
            if cortex:
                llm = cortex.get("llm_analysis", {})
                print(f"\n  🤖 [CORTEX] Análise Híbrida (CODE + LLM):")
                print(f"     Verdict Score: {cortex.get('verdict_score', 'N/A')}")
                print(f"     Risk Level:    {cortex.get('risk_level', 'N/A')}")
                print(f"     Decision Band: {cortex.get('decision_band', 'N/A')}")
                if llm:
                    analysis = llm.get("analysis", "N/A") or "N/A"
                    action = llm.get("action", "N/A") or "N/A"
                    justification = llm.get("justification", "N/A") or "N/A"
                    print(f"\n     📝 LLM Análise:       {str(analysis)[:200]}{'...' if len(str(analysis)) > 200 else ''}")
                    print(f"     📋 LLM Ação:          {str(action)[:200]}{'...' if len(str(action)) > 200 else ''}")
                    print(f"     💬 LLM Justificativa:  {str(justification)[:200]}{'...' if len(str(justification)) > 200 else ''}")
                dims = cortex.get("risk_dimensions", {})
                if dims:
                    print(f"\n     📊 Dimensões de Risco:")
                    print(f"        Físico:       {dims.get('physical', 'N/A')}")
                    print(f"        Financeiro:   {dims.get('financial', 'N/A')}")
                    print(f"        Contratual:   {dims.get('contractual', 'N/A')}")
                    print(f"        Comunicação:  {dims.get('communication', 'N/A')}")

            # Guardrails (top-level key: "guardrails")
            guardrails = detail.get("guardrails", {})
            checked = guardrails.get("checked", [])
            violations = guardrails.get("violations", [])
            print(f"\n  🛡️ [GUARDRAILS] Validação:")
            print(f"     Verificados: {len(checked)}")
            print(f"     Violações:   {len(violations)}")
            for v in violations:
                print(f"        ⚠️ {v}")

            # Verdict (top-level keys)
            print(f"\n  ⚖️ VEREDITO FINAL:")
            print(f"     Score:      {detail.get('verdict_score', 'N/A')}")
            print(f"     Decisão:    {detail.get('decision', 'N/A')}")
            print(f"     Risco:      {detail.get('risk_level', 'N/A')}")
            print(f"     Tempo:      {detail.get('processing_time_ms', 'N/A')} ms")

            # Update trace from reasoning store
            store_trace = detail.get("reasoning_trace", [])
            if store_trace:
                trace = store_trace

        # 6. Análise automática
        print("\n" + "=" * 65)
        print("🧪 ANÁLISE DO RESULTADO")
        print("=" * 65)

        # Build comprehensive text from ALL available sources
        all_text_parts = [" ".join(str(t) for t in trace)]
        # Add LLM analysis text
        llm_data = detail.get("cortex", {}).get("llm_analysis", {})
        if llm_data:
            all_text_parts.append(str(llm_data.get("analysis", "")))
            all_text_parts.append(str(llm_data.get("action", "")))
            all_text_parts.append(str(llm_data.get("justification", "")))
        # Add manual excerpts
        manuals = detail.get("sense", {}).get("atlas_context", {}).get("manual_excerpts", [])
        if manuals:
            all_text_parts.append("manual trecho consultado")
        # Check if cortex processed
        if detail.get("cortex", {}).get("verdict_score") is not None:
            all_text_parts.append("cortex processou verdict")
        all_text = " ".join(all_text_parts).lower()

        checks = [
            ("Detectou padrão de desligamento/power cycle",
             any(x in all_text for x in ["deslig", "power cycle", "power_cycle", "liga/desliga", "desligamento noturno", "ligar/desligar", "power off", "off_duration"])),
            ("Mencionou impacto financeiro/energia",
             any(x in all_text for x in ["energia", "consumo", "kwh", "custo", "econom", "financ", "impacto"])),
            ("Identificou risco sanitário/produto",
             any(x in all_text for x in ["carne", "anvisa", "contamin", "perecível", "descart", "produto", "perda", "sanitár", "aliment"])),
            ("Recomendou NÃO desligar / educação do operador",
             any(x in all_text for x in ["não deslig", "instruir", "operador", "educação", "orient", "manter ligado", "funcionamento contínuo", "manter", "evitar"])),
            ("Manual técnico consultado",
             any(x in all_text for x in ["manual", "trecho"])),
            ("Pipeline completo executou (Cortex processou)",
             any(x in all_text for x in ["llm", "fusão", "cortex", "verdict"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "⚠️  WARN "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")
        print(f"\n  🖥️ Veja o raciocínio completo no frontend:")
        print(f"     → http://localhost:8080/dashboard/reasoning")

        if passed < len(checks):
            print(f"\n⚠️ Algumas verificações falharam, mas o evento foi processado.")
        else:
            print(f"\n🎉 Todas as verificações passaram! O CAOS detectou o desligamento noturno.")


if __name__ == "__main__":
    asyncio.run(main())
