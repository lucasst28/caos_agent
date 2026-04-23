#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 6: Chiller HVAC — Decisão Econômica vs Segurança
==========================================================================
Cenário: Chiller Central #04, Edifício Corporativo, São Paulo

Temperatura: 95°C (limite 100°C). Rolamento aquecendo (82°C, limite 95°C).
Shutdown custa R$50k/hora de downtime (12 andares sem ar-condicionado).
Redução de carga de 30% baixaria a temperatura para ~85°C.

O LLM deve:
  ✓ Reconhecer a proximidade do limite (5°C de margem)
  ✓ Identificar risco ao rolamento (82°C, tendência de desgaste)
  ✓ NÃO recomendar shutdown total (custo proibitivo: R$50k/hora)
  ✓ Propor redução de carga como alternativa
  ✓ Ponderar R_F vs R_Fin com dados concretos
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 6: Chiller Econômico vs Segurança ║")
    print("║         Edifício Corporativo - CHILLER-04-TEST              ║")
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
        print("   Ativo:      CHILLER-04-TEST (Chiller HVAC Central)")
        print("   Métrica:    temperature")
        print("   Valor:      95.0°C (limite: 100°C)")
        print("   Severidade: HIGH")
        print()
        print("   🎯 DESAFIO NÍVEL 6:")
        print("     → Ponderar custo de shutdown (R$50k/h) vs risco físico")
        print("     → Propor redução de carga (não shutdown total)")
        print("     → Identificar risco ao rolamento (82°C)")

        payload = {
            "tenant_id": "viva-corp",
            "asset_id": "CHILLER-04-TEST",
            "metric": "temperature",
            "value": 95.0,
            "severity": "HIGH",
            "source": "sentinel",
            "description": "Chiller Central com temperatura em 95°C (limite 100°C). Rolamento em 82°C. Edifício 100% ocupado.",
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
        print("⚡ RESULTADO DO PIPELINE CAOS")
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
        print("🧪 ANÁLISE: PONDERAÇÃO ECONÔMICA vs SEGURANÇA")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("🌡️ Reconheceu proximidade do limite (95°C vs 100°C)",
             any(x in trace_text for x in ["95", "100", "próximo", "margem", "5°c", "limite"])),

            ("⚙️ Identificou risco ao rolamento",
             any(x in trace_text for x in ["rolamento", "bearing", "82", "desgaste"])),

            ("💰 Mencionou custo de shutdown (R$50k/h ou downtime)",
             any(x in trace_text for x in ["50", "downtime", "custo", "parada", "impacto financeiro"])),

            ("📉 Propôs redução de carga (não shutdown total)",
             any(x in trace_text for x in ["reduz", "carga", "reduce", "load", "parcial", "redução"])),

            ("⚖️ R_F e R_Fin equilibrados (ambos considerados)",
             any(x in trace_text for x in ["risco_f", "r_f", "físico"]) and
             any(x in trace_text for x in ["risco_fin", "r_fin", "financeiro"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 5:
            print("\n  🏆 PERFEITO! O LLM fez ponderação econômica correta!")
        elif passed >= 4:
            print("\n  ✅ MUITO BOM! Ponderou bem custo vs risco.")
        elif passed >= 3:
            print("\n  ⚠️ PARCIAL — Faltou considerar algum aspecto.")
        else:
            print("\n  ❌ FRACO — Não conseguiu ponderar economia vs segurança.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
