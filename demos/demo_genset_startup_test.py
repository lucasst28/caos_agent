#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 6: Gerador Diesel — Falso Alarme em Startup
=====================================================================
Cenário: Gerador Diesel #02, Cobertura, Edifício Corporativo

Blackout na rede — gerador DEVE funcionar. Vibração 7.5mm/s, temp 78°C,
frequência 59.2Hz. Tudo subindo rápido... mas é NORMAL para startup diesel.

O LLM deve:
  ✓ Reconhecer que é um startup (apenas 45 segundos)
  ✓ Notar que vibração está DESCENDO (12→7.5 = estabilizando)
  ✓ NÃO recomendar shutdown (edifício precisa do gerador!)
  ✓ Indicar que indicadores vão estabilizar
  ✓ Notar UPS com apenas 8 min de bateria
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 6: Gerador Diesel (Startup)       ║")
    print("║         Edifício Corporativo - GENSET-02-TEST               ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    async with httpx.AsyncClient(timeout=90.0) as client:
        print("\n🔌 Verificando servidores...")
        try:
            await client.get(f"{CAOS_URL}/health")
            await client.get(f"{SIMULATORS_URL}/health")
            print("  ✅ Servidores OK")
        except Exception as e:
            print(f"  ❌ Servidor indisponível: {e}")
            sys.exit(1)

        print("\n📡 Enviando alerta ao CAOS:")
        print("   Ativo:      GENSET-02-TEST (Gerador Diesel)")
        print("   Métrica:    vibration")
        print("   Valor:      7.5 mm/s (limite: 10.0)")
        print("   Severidade: HIGH")
        print()
        print("   🎯 DESAFIO NÍVEL 6:")
        print("     → Reconhecer startup como causa dos picos")
        print("     → NÃO desligar o gerador durante blackout")
        print("     → Notar tendência de estabilização")

        payload = {
            "tenant_id": "viva-corp",
            "asset_id": "GENSET-02-TEST",
            "metric": "vibration",
            "value": 7.5,
            "severity": "HIGH",
            "source": "sentinel",
            "description": "Vibração alta (7.5mm/s) e temperatura subindo rápido (78°C) no Gerador Diesel #02 durante blackout.",
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  ❌ Erro: {resp.status_code} — {resp.text}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"\n  ✅ Evento processado: {event_id}")

        # Resultado
        print("\n" + "=" * 65)
        print("⚡ RESULTADO DO PIPELINE CAOS")
        print("=" * 65)
        print(f"  Decision:       {result.get('decision')}")
        print(f"  Risk Level:     {result.get('risk_level')}")
        print(f"  Verdict Score:  {result.get('verdict_score')}")

        trace = result.get("reasoning", result.get("reasoning_trace", []))
        print(f"\n  📝 TRACE ({len(trace)} etapas):")
        for step in trace:
            for line in str(step).split('\n'):
                print(f"    {line}")

        # Análise
        print("\n" + "=" * 65)
        print("🧪 ANÁLISE: RECONHECEU STARTUP NORMAL?")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("🔧 Reconheceu que é um startup/partida",
             any(x in trace_text for x in ["startup", "partida", "inicialização", "rampa", "arranque"])),

            ("📉 Notou que vibração está DESCENDO/estabilizando",
             any(x in trace_text for x in ["descendo", "diminuindo", "estabiliz", "reduzindo", "tendência de queda"])),

            ("🚫 NÃO recomendou shutdown",
             not any(x in trace_text for x in ["desligar o gerador", "shutdown do gerador", "parar o gerador"])),

            ("⏱️ Mencionou contexto temporal (45s, estabilização)",
             any(x in trace_text for x in ["45", "segundo", "transitório", "temporário", "estabiliz"])),

            ("🔋 Notou UPS/bateria limitada",
             any(x in trace_text for x in ["ups", "bateria", "8 min", "autonomia"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)}")
        if passed >= 4:
            print("\n  🏆 PERFEITO! Reconheceu startup normal!")
        elif passed >= 3:
            print("\n  ✅ BOM! Entendeu o contexto.")
        else:
            print("\n  ❌ FRACO — Tratou startup como falha real.")


if __name__ == "__main__":
    asyncio.run(main())
