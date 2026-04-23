#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 8: Armazém Frigorífico — Onda de Calor
================================================================
Cenário: Armazém Gelatto, Ribeirão Preto

3 de 5 freezers com temperatura acima do limite. Causa SISTÊMICA:
onda de calor a 42°C sobrecarregando a refrigeração central.
Cada freezer individualmente está com compressor/refrigerante OK.

O LLM deve:
  ✓ Identificar causa sistêmica (calor externo)
  ✓ NÃO diagnosticar falha individual nos freezers
  ✓ Correlacionar 3/5 unidades afetadas = padrão
  ✓ Recomendar ação no HVAC/ambiente
  ✓ Solicitar dados de clima (SOLICITAR: CLIMA)
  ✓ R_Fin alto (R$450k em produtos)
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 8: Armazém — Onda de Calor        ║")
    print("║         Gelatto Ribeirão Preto - WAREHOUSE-AC-01            ║")
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
        print("   Ativo:      WAREHOUSE-AC-01 (Sistema HVAC do Armazém)")
        print("   Métrica:    temperature")
        print("   Valor:      -6.0°C (média das unidades afetadas)")
        print("   Severidade: CRITICAL")
        print()
        print("   🎯 DESAFIO NÍVEL 8:")
        print("     → 3/5 unidades afetadas = causa SISTÊMICA")
        print("     → Compressores individuais OK = NÃO é falha individual")
        print("     → Causa: onda de calor 42°C")
        print("     → Ação: ambiente/HVAC, não freezers individuais")

        payload = {
            "tenant_id": "gelatto-rp",
            "asset_id": "WAREHOUSE-AC-01",
            "metric": "temperature",
            "value": -6.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "MULTI-ALARME: 3 de 5 unidades do armazém com temperatura acima do limite. UNIT-A: -5°C, UNIT-B: -7°C, UNIT-C: -6°C. R$450k em produtos.",
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
        print("🧪 ANÁLISE: CAUSA SISTÊMICA vs INDIVIDUAL")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("🔥 Identificou causa sistêmica (externo/calor)",
             any(x in trace_text for x in ["sistêmic", "extern", "onda de calor", "calor externo", "ambiente", "42"])),

            ("📊 Correlacionou 3/5 unidades afetadas",
             any(x in trace_text for x in ["3", "múltipla", "simultâne", "padrão", "conjunto", "unidades"])),

            ("✅ NÃO diagnosticou falha individual nos freezers",
             not any(x in trace_text for x in ["falha no compressor", "compressor defeituoso", "refrigerante baixo"])),

            ("🏢 Recomendou ação no HVAC/condensador/ambiente",
             any(x in trace_text for x in ["hvac", "condensador", "ventilação", "ambient", "irrigar", "resfri"])),

            ("🌡️ Solicitou ou usou dados de clima",
             any(x in trace_text for x in ["solicitar", "clima", "weather", "42°c", "temperatura extern"])),

            ("💰 Mencionou risco financeiro (R$450k)",
             any(x in trace_text for x in ["450", "produto", "perda", "financeiro", "armazém"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)}")
        if passed >= 5:
            print("\n  🏆 PERFEITO! Identificou causa sistêmica!")
        elif passed >= 4:
            print("\n  ✅ BOM! Percebeu o padrão coletivo.")
        else:
            print("\n  ❌ FRACO — Tratou como falha individual.")


if __name__ == "__main__":
    asyncio.run(main())
