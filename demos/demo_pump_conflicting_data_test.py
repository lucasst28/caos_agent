#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 7: Bomba d'Água — Dados Conflitantes
==============================================================
Cenário: Bomba Centrífuga #03, Manaus

Sensor de pressão primário lê 0 PSI (CRITICAL!), mas vazão está a 95%,
motor normal, vibração normal. Sensor SECUNDÁRIO lê 85 PSI (OK).

O LLM deve:
  ✓ Detectar contradição entre pressão 0 e vazão 95%
  ✓ Identificar sensor primário defeituoso
  ✓ NÃO recomendar shutdown da bomba
  ✓ R_F baixo (problema instrumental)
  ✓ Recomendar substituição do sensor
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 7: Bomba — Dados Conflitantes     ║")
    print("║         Manaus - PUMP-03                                    ║")
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
        print("   Ativo:      PUMP-03 (Bomba Centrífuga #03)")
        print("   Métrica:    pressure")
        print("   Valor:      0.0 PSI (ZERO!)")
        print("   Severidade: CRITICAL")
        print()
        print("   🎯 DESAFIO NÍVEL 7:")
        print("     → Pressão 0 PSI MAS vazão 95%: CONTRADIÇÃO!")
        print("     → Sensor defeituoso, não falha da bomba")
        print("     → NÃO parar a bomba (R$80k/dia de impacto)")

        payload = {
            "tenant_id": "ind-norte",
            "asset_id": "PUMP-03",
            "metric": "pressure",
            "value": 0.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "ALERTA CRÍTICO: Pressão ZERO no sensor primário da Bomba #03. Verificar integridade do sistema hidráulico.",
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  ❌ Erro: {resp.status_code} — {resp.text}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"\n  ✅ Evento processado: {event_id}")

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
        print("🧪 ANÁLISE: CONTRADIÇÃO DE SENSORES")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("🔍 Detectou contradição pressão 0 vs vazão normal",
             any(x in trace_text for x in ["contradição", "contradit", "inconsistên", "conflitan"])),

            ("📊 Identificou sensor primário como defeituoso",
             any(x in trace_text for x in ["sensor", "defeituoso", "falha do sensor", "wika", "descalibr"])),

            ("✅ Notou sensor secundário com leitura normal (85 PSI)",
             any(x in trace_text for x in ["85", "secundário", "endress", "pmc71", "normal"])),

            ("🚫 NÃO recomendou shutdown da bomba",
             not any(x in trace_text for x in ["desligar a bomba", "shutdown da bomba", "parar a bomba"])),

            ("🔧 Recomendou troca/calibração do sensor",
             any(x in trace_text for x in ["substitu", "calibr", "troc", "sensor", "reparar sensor"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)}")
        if passed >= 4:
            print("\n  🏆 PERFEITO! Detectou sensor defeituoso!")
        elif passed >= 3:
            print("\n  ✅ BOM! Identificou contradição.")
        else:
            print("\n  ❌ FRACO — Não detectou contradição de sensores.")


if __name__ == "__main__":
    asyncio.run(main())
