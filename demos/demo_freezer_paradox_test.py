#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 5: Armadilha Dupla (Paradoxo)
========================================================
Cenário: Laticínios São Jorge, Curitiba — FREEZER-07
Temperatura a +5°C — parece catastrófico!

  ARMADILHA 1 (pânico): +5°C num freezer de laticínios! EMERGÊNCIA!
    → Errado: é um ciclo de degelo programado, produtos estão na backup

  ARMADILHA 2 (descarte): "Ah, só um degelo, tudo sob controle"
    → TAMBÉM errado: o degelo deveria ter acabado há 45 minutos!
    → O relé do aquecedor está TRAVADO em ON (100%, 45°C)
    → Serpentina do evaporador a 38°C (dano se >50°C)

O LLM deve:
  ✓ Reconhecer que é um degelo programado (não panicar)
  ✓ Reconhecer que produtos estão seguros (realocados)
  ✓ DETECTAR que o degelo está com overshoot (75min vs 30min)
  ✓ Identificar o relé travado como causa do overshoot
  ✓ Entender que o risco é no EQUIPAMENTO, não nos produtos
  ✓ Recomendar desligar o AQUECEDOR (não o equipamento todo)
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8000"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 5: Armadilha Dupla (PARADOXO)     ║")
    print("║          Laticínios São Jorge - FREEZER-07                  ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    async with httpx.AsyncClient(timeout=90.0) as client:
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
        print("   Ativo:      FREEZER-07 (Câmara Fria de Laticínios)")
        print("   Métrica:    temperature")
        print("   Valor:      +5.0°C (sim, POSITIVO!)")
        print("   Severidade: CRITICAL")
        print()
        print("   🎯 DESAFIO NÍVEL 5 — Armadilha Dupla:")
        print("     ❌ Armadilha 1: Panicar (+5°C! Produtos vão estragar!)")
        print("     ❌ Armadilha 2: Descartar (é só um degelo, relaxa)")
        print("     ✅ Resposta certa: Degelo legítimo MAS com relé travado!")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-07",
            "metric": "temperature",
            "value": 5.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "ALERTA: Temperatura FREEZER-07 em +5.0°C — extremamente acima do limite de -11°C."
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  ❌ Erro: {resp.status_code} — {resp.text}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"\n  ✅ Evento processado: {event_id}")

        # 3. Resultado
        print("\n" + "=" * 65)
        print("⚡ RESULTADO IMEDIATO DO PIPELINE CAOS")
        print("=" * 65)
        print(f"  Event ID:       {event_id}")
        print(f"  Verdict Score:  {result.get('verdict_score')}")
        print(f"  Decision:       {result.get('decision')}")
        print(f"  Risk Level:     {result.get('risk_level')}")
        print(f"  Guardrails:     {result.get('guardrail_violations', [])}")
        print(f"  Tempo:          {result.get('processing_time_ms', 0):.2f} ms")

        # 4. Trace
        trace = result.get("reasoning", result.get("reasoning_trace", []))
        print(f"\n  📝 TRACE DE RACIOCÍNIO ({len(trace)} etapas):")
        print("  " + "-" * 50)
        for step in trace:
            for line in str(step).split('\n'):
                print(f"    {line}")
        print("  " + "-" * 50)

        # 5. Análise
        print("\n" + "=" * 65)
        print("🧪 ANÁLISE: O LLM NAVEGOU AS DUAS ARMADILHAS?")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            # ARMADILHA 1: Não panicar
            ("😌 NÃO panicou: reconheceu ciclo de degelo programado",
             any(x in trace_text for x in ["degelo", "defrost", "ciclo", "descongelamento"])),
            ("📦 Reconheceu que produtos estão seguros (realocados)",
             any(x in trace_text for x in ["realocad", "backup", "movid", "seguros", "transferid"])),
            ("👤 Notou que operador já confirmou o degelo",
             any(x in trace_text for x in ["operador", "marcos", "confirmou", "reconhec", "acknowledged", "programado", "agendado", "planejado"])),

            # ARMADILHA 2: Não descartar
            ("⏰ DETECTOU overshoot: 75min vs 30min programado",
             any(x in trace_text for x in ["75", "overshoot", "excedeu", "ultrapass", "além do previsto", "além do tempo"])),
            ("🔥 Identificou relé do aquecedor TRAVADO",
             any(x in trace_text for x in ["relé", "travado", "preso", "stuck", "aquecedor", "heater"])),
            ("🌡️ Notou aquecedor a 45°C (acima do limite de 35°C)",
             any(x in trace_text for x in ["45", "aquecedor", "heater", "acima do limite"])),
            ("🔧 Notou serpentina/coil a 38°C (risco de dano)",
             any(x in trace_text for x in ["serpentina", "coil", "38", "dano", "evaporador"])),

            # RESPOSTA NUANÇADA
            ("🎯 Entendeu: risco é no EQUIPAMENTO, não nos produtos",
             any(x in trace_text for x in ["equipamento", "evaporador", "dano", "serpentina", "mecânic"]) and
             any(x in trace_text for x in ["seguro", "realocad", "backup", "não é emergência"])),
            ("⚡ Recomendou desligar o AQUECEDOR especificamente",
             any(x in trace_text for x in ["desligar aquecedor", "desligar o aquecedor", "disjuntor", "cortar aquecedor", "desligar heater"])),
            ("🔄 Recomendou religar o compressor depois",
             any(x in trace_text for x in ["ligar compressor", "religar", "reiniciar", "compressor manual"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 9:
            print("\n  🏆 LENDÁRIO! O LLM navegou a armadilha dupla PERFEITAMENTE!")
        elif passed >= 7:
            print("\n  ✅ EXCELENTE! Encontrou a nuance e agiu corretamente.")
        elif passed >= 5:
            print("\n  ⚠️ BOM — Evitou uma armadilha mas caiu parcialmente na outra.")
        elif passed >= 3:
            print("\n  😐 PARCIAL — Caiu em pelo menos uma armadilha.")
        else:
            print("\n  ❌ FALHOU — Caiu nas duas armadilhas.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
