#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 3: Falha em Cascata (Root Cause Analysis)
====================================================================
Cenário: Sorveteria Glacial Premium, São Paulo — FREEZER-05
Câmara fria de sorvetes com CASCATA DE FALHAS:

  CAUSA RAIZ:   Vazamento lento de refrigerante (55%, há semanas)
       ↓
  SINTOMA 1:    Compressor sobrecarregado (98%, 5.3A, vibração 4.6mm/s)
       ↓
  SINTOMA 2:    Motor aquecendo (92°C)
       ↓
  SINTOMA 3:    Temperatura subindo rápido (-4°C, era -18°C há 2 dias)
       ↓
  CONSEQUÊNCIA: R$120k em sorvetes premium em risco de descongelamento

ARMADILHAS:
  1. Manutenção agendada para AMANHÃ — mas não pode esperar!
  2. O compressor parece ser o problema — mas é SINTOMA, não causa
  3. Histórico mostra que o vazamento foi adiado em visitas anteriores

O LLM deve:
  ✓ Identificar o vazamento de refrigerante como CAUSA RAIZ
  ✓ Reconhecer que o compressor é CONSEQUÊNCIA, não causa
  ✓ Decidir que NÃO PODE esperar a manutenção de amanhã
  ✓ Recomendar ação de EMERGÊNCIA
  ✓ Notar o item adiado no histórico
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8000"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 3: Falha em Cascata (ROOT CAUSE)  ║")
    print("║          Sorveteria Glacial Premium - FREEZER-05            ║")
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
        print("   Ativo:      FREEZER-05 (Câmara Fria de Sorvetes)")
        print("   Métrica:    temperature")
        print("   Valor:      -4.0°C (era -18°C há 2 dias!)")
        print("   Severidade: CRITICAL")
        print()
        print("   🎯 DESAFIO NÍVEL 3:")
        print("     → Encontrar a CAUSA RAIZ (vazamento de gás)")
        print("     → Não confundir sintoma (compressor) com causa")
        print("     → Decidir NÃO esperar a manutenção de amanhã")
        print("     → Notar o histórico de manutenção adiada")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-05",
            "metric": "temperature",
            "value": -4.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "URGENTE: Temperatura FREEZER-05 em -4.0°C — subindo rapidamente nas últimas 24h (era -16°C). R$120k em sorvetes premium em risco."
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  ❌ Erro: {resp.status_code} — {resp.text}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"\n  ✅ Evento processado: {event_id}")

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

        # 5. Análise automática
        print("\n" + "=" * 65)
        print("🧪 ANÁLISE: O LLM ENCONTROU A CAUSA RAIZ?")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            # CAUSA RAIZ
            ("🔴 CAUSA RAIZ: Identificou vazamento de refrigerante",
             any(x in trace_text for x in ["vazamento", "refrigerante", "gás", "r-404", "leak", "carga baixa"])),
            ("   → Notou carga em 55% (crítico)",
             any(x in trace_text for x in ["55%", "carga", "metade"])),
            ("   → Notou pressão de sucção baixa (12 PSI)",
             any(x in trace_text for x in ["12 psi", "pressão de sucção", "sucção baixa"])),
            ("   → Notou superheat alto (15°C)",
             any(x in trace_text for x in ["superheat", "superaquecimento", "15°c"])),

            # CAUSALIDADE
            ("🔗 Entendeu que compressor é SINTOMA, não causa",
             any(x in trace_text for x in ["consequência", "sintoma", "resultado", "causado pelo", "devido ao", "cadeia"])),
            ("   → Notou COP péssimo (1.4)",
             any(x in trace_text for x in ["1.4", "cop", "eficiência"])),

            # URGÊNCIA
            ("⏰ Decidiu NÃO esperar manutenção de amanhã",
             any(x in trace_text for x in ["emergência", "não esperar", "imediato", "urgente", "não pode aguardar", "dispatch"])),
            ("💰 Mencionou R$120k em produtos em risco",
             any(x in trace_text for x in ["120", "sorvete", "produto", "perda"])),

            # HISTÓRICO
            ("📋 Notou item adiado no histórico de manutenção",
             any(x in trace_text for x in ["adiado", "deferred", "visita anterior", "recomendação", "micro-vazamento", "última manutenção"])),

            # AÇÃO CORRETA
            ("✅ Recomendou recarga de refrigerante (não trocar compressor)",
             any(x in trace_text for x in ["recarga", "recarregar", "reparar vazamento", "localizar"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 9:
            print("\n  🏆 PERFEITO! O LLM é um técnico SÊNIOR — encontrou a causa raiz!")
        elif passed >= 7:
            print("\n  ✅ MUITO BOM! Encontrou a causa raiz e agiu corretamente.")
        elif passed >= 5:
            print("\n  ⚠️ PARCIAL — Encontrou parte da cadeia mas falhou em algo.")
        else:
            print("\n  ❌ FRACO — Não conseguiu traçar a cadeia causal.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
