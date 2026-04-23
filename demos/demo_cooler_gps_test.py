#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 6: Cooler Uganda — GPS Displacement (Furto)
=====================================================================
Cenário: Cooler SPB-0500 #2712, Wildstar Inn, Kampala, Uganda

Dados reais do BD de produção: deslocamento de 7.787 metros.
Temperatura do cabinet: 26°C (operacional: -2 a 7°C).

O LLM deve:
  ✓ Identificar deslocamento anômalo (7.787m = furto provável)
  ✓ Classificar como possível furto/roubo
  ✓ Recomendar ações de segurança
  ✓ R_F alto (ativo perdido/em risco)
  ✓ Mencionar localização GPS ou outlet
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 6: Cooler GPS (Possível Furto)    ║")
    print("║         Wildstar Inn, Kampala - SPB0500221212712            ║")
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
        print("   Ativo:      SPB0500221212712 (Cooler SPB-0500)")
        print("   Métrica:    gps_displacement")
        print("   Valor:      7787.40 metros")
        print("   Severidade: HIGH")
        print()
        print("   🎯 DESAFIO NÍVEL 6:")
        print("     → Identificar 7.787m como furto/roubo")
        print("     → Recomendar ações de segurança")
        print("     → NÃO confundir com movimentação logística")

        payload = {
            "tenant_id": "ubl-uganda",
            "asset_id": "SPB0500221212712",
            "metric": "gps_displacement",
            "value": 7787.40,
            "severity": "HIGH",
            "source": "sentinel",
            "description": "Alerta GPS: Cooler SPB-0500 deslocou 7.787 metros da posição registrada no outlet Wildstar Inn, Kampala.",
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
        print("🧪 ANÁLISE: DETECÇÃO DE FURTO")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("📍 Identificou deslocamento anômalo (7.787m / ~8km)",
             any(x in trace_text for x in ["7787", "7.787", "deslocamento", "displacement", "quilômetro", "8km", "8 km"])),

            ("🚨 Classificou como possível furto/roubo",
             any(x in trace_text for x in ["furto", "roubo", "theft", "roubado", "furtado", "remoção não autorizada"])),

            ("🔒 Recomendou ação de segurança",
             any(x in trace_text for x in ["segurança", "bloquei", "rastreament", "polí", "security", "lock"])),

            ("📌 Mencionou outlet ou localização",
             any(x in trace_text for x in ["wildstar", "kampala", "uganda", "outlet", "inn"])),

            ("⚠️ R_F alto (ativo em risco/perdido)",
             result.get("risk_level") in ("HIGH", "VETO") or
             any(x in trace_text for x in ["alto", "high", "crítico", "elevado"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)}")
        if passed >= 4:
            print("\n  🏆 PERFEITO! Detectou furto corretamente!")
        elif passed >= 3:
            print("\n  ✅ BOM! Identificou anomalia GPS.")
        else:
            print("\n  ❌ FRACO — Não identificou furto.")


if __name__ == "__main__":
    asyncio.run(main())
