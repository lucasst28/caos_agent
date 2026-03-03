#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 1: Sensor Defeituoso (Falso Positivo)
================================================================
Cenário: Padaria São Jorge — FREEZER-03
O sensor principal (PT100) lê -2°C (alarme!!), mas TODOS os outros
indicadores mostram que o freezer está perfeito:
  - Sonda de produto: -19.2°C (normal)
  - Compressor: 35% (relaxado)
  - Energia: 5.2 kWh (abaixo do normal)
  - Evaporador: -20.5°C (normal)
  - Porta: vedação 98%, 1 abertura/hora

O LLM deve detectar a CONTRADIÇÃO e concluir que o sensor está
defeituoso — não o equipamento. Isso evita um chamado técnico
desnecessário (R$350-500).
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║   CAOS Agent — Teste Nível 1: Sensor Defeituoso (TRAP!)     ║")
    print("║              Padaria São Jorge - FREEZER-03                 ║")
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

        # 2. Enviar alerta
        print("\n📡 Enviando alerta ao CAOS:")
        print("   Ativo:      FREEZER-03 (Freezer de Frios)")
        print("   Métrica:    temperature")
        print("   Valor:      -2.0°C (sensor principal — SUSPEITO)")
        print("   Severidade: HIGH")
        print("   🎯 DESAFIO:  O LLM vai cair na armadilha ou vai perceber?")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-03",
            "metric": "temperature",
            "value": -2.0,
            "severity": "HIGH",
            "source": "sentinel",
            "description": "Temperatura FREEZER-03 em -2.0°C — sensor principal acima do limite de -11°C."
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
        print(f"  Guardrails:     {result.get('guardrail_violations', [])}")
        print(f"  Tempo:          {result.get('processing_time_ms', 0):.2f} ms")

        # 4. Trace de raciocínio
        trace = result.get("reasoning", result.get("reasoning_trace", []))
        print(f"\n  📝 TRACE DE RACIOCÍNIO ({len(trace)} etapas):")
        print("  " + "-" * 50)
        for step in trace:
            for line in str(step).split('\n'):
                print(f"    {line}")
        print("  " + "-" * 50)

        # 5. Buscar reasoning store
        await asyncio.sleep(2)
        print(f"\n🔍 Buscando raciocínio do evento {event_id}...")

        r_detail = await client.get(f"{CAOS_URL}/v1/reasoning/{event_id}")
        if r_detail.status_code == 200:
            detail = r_detail.json()

            print("\n" + "=" * 65)
            print("🧠 RACIOCÍNIO COMPLETO (Reasoning Store)")
            print("=" * 65)

            # Cortex analysis
            cortex = detail.get("cortex", {})
            if cortex:
                llm = cortex.get("llm_analysis", {})
                if isinstance(llm, dict):
                    print(f"\n  🤖 [CORTEX] Análise LLM:")
                    analysis = llm.get('analysis', 'N/A')
                    print(f"     Análise:     {analysis[:200]}{'...' if len(str(analysis)) > 200 else ''}")
                    print(f"     Ação:        {llm.get('action', 'N/A')}")
                    print(f"     Justificativa: {llm.get('justification', 'N/A')}")
                    print(f"     Confiança:   {llm.get('confidence', 'N/A')}")
                    print(f"     R_Físico:    {llm.get('physical', 'N/A')}")
                    print(f"     R_Financeiro: {llm.get('financial', 'N/A')}")

            # Verdict
            print(f"\n  ⚖️ VEREDITO FINAL:")
            print(f"     Score:      {detail.get('verdict_score', 'N/A')}")
            print(f"     Decisão:    {detail.get('decision', 'N/A')}")
            print(f"     Risco:      {detail.get('risk_level', 'N/A')}")

        # 6. Análise automática — O LLM caiu na armadilha?
        print("\n" + "=" * 65)
        print("🧪 ANÁLISE: O LLM CAIU NA ARMADILHA?")
        print("=" * 65)

        # Combine trace + reasoning store for comprehensive keyword search
        all_texts = [str(t) for t in trace]
        if r_detail.status_code == 200:
            d = r_detail.json()
            cortex_d = d.get("cortex", {})
            llm_d = cortex_d.get("llm_analysis", {}) if isinstance(cortex_d, dict) else {}
            if isinstance(llm_d, dict):
                for k in ["analysis", "justification", "thought"]:
                    v = llm_d.get(k, "")
                    if v:
                        all_texts.append(str(v))
            for t_item in d.get("reasoning_trace", []):
                all_texts.append(str(t_item))
        trace_text = " ".join(all_texts).lower()

        checks = [
            ("🎯 Identificou sensor defeituoso/falso positivo",
             any(x in trace_text for x in ["sensor", "falso positivo", "calibra", "pt100", "sonda", "defeito", "descalibr"])),
            ("🔍 Notou contradição entre sensores (principal vs secundário)",
             any(x in trace_text for x in ["contradi", "secundár", "sonda", "-19", "probe", "ntc", "discrepância"])),
            ("⚙️ Notou que compressor está relaxado (contradiz alarme)",
             any(x in trace_text for x in ["35%", "baixa carga", "baixo", "repouso", "normal", "40%", "relaxado"])),
            ("⚡ Notou energia normal/baixa (contradiz alarme)",
             any(x in trace_text for x in ["5.2", "energia", "consumo", "abaixo", "kwh"])),
            ("❌ NÃO sugeriu manutenção/ticket do compressor",
             not any(x in trace_text for x in ["manutenção do compressor", "trocar compressor", "falha do compressor"])),
            ("✅ Sugeriu calibração/substituição do sensor",
             any(x in trace_text for x in ["calibra", "substitu", "sensor", "recalibr", "troca do sensor"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 5:
            print("\n  🏆 EXCELENTE! O LLM NÃO caiu na armadilha — identificou o sensor!")
        elif passed >= 3:
            print("\n  ⚠️ PARCIAL — O LLM percebeu algo mas não foi assertivo.")
        else:
            print("\n  ❌ O LLM caiu na armadilha — tratou como falha real.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
