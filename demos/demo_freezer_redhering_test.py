#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 4: Red Herrings + Information Overload
=================================================================
Cenário: Supermercado Estrela, Belo Horizonte — FREEZER-06
Temperatura a -6°C (alarme) com MÚLTIPLAS pistas — a maioria FALSAS!

  ✅ CAUSA REAL:    Queda de tensão na rede elétrica (198V vs 220V)
                    → Compressor sub-performa → HVAC do prédio também afetado

  ❌ RED HERRING 1: Manutenção feita 2 dias atrás (mas foi BEM sucedida)
  ❌ RED HERRING 2: Reclamação de barulho (mas é do freezer ao LADO)
  ❌ RED HERRING 3: Funcionário novo (mas foi treinado e abriu porta 3x = normal)
  ❌ RED HERRING 4: Carnaval + 45% mais clientes (contribui pouco para 12°C de desvio)

O LLM deve:
  ✓ Identificar a queda de tensão como causa PRINCIPAL
  ✓ Correlacionar com HVAC e luzes do prédio também afetados
  ✓ NÃO culpar a manutenção recente
  ✓ NÃO culpar o funcionário novo
  ✓ Descartar ou minimizar o impacto do Carnaval
  ✓ Recomendar ação na rede elétrica (concessionária/estabilizador)
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8000"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 4: Red Herrings + Info Overload   ║")
    print("║          Supermercado Estrela - FREEZER-06                  ║")
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
        print("   Ativo:      FREEZER-06 (Freezer de Congelados)")
        print("   Métrica:    temperature")
        print("   Valor:      -6.0°C (acima de -11°C)")
        print("   Severidade: HIGH")
        print()
        print("   🎯 DESAFIO NÍVEL 4 — 4 Red Herrings:")
        print("     ❌ Manutenção 2 dias atrás (foi bem sucedida)")
        print("     ❌ Barulho estranho (é do freezer ao lado)")
        print("     ❌ Funcionário novo Pedro (abriu porta 3x = normal)")
        print("     ❌ Carnaval + 45% mais clientes (desvio de 1-3°C, não 12°C)")
        print("     ✅ Tensão 198V vs 220V (REAL CAUSA)")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-06",
            "metric": "temperature",
            "value": -6.0,
            "severity": "HIGH",
            "source": "sentinel",
            "description": "Temperatura FREEZER-06 em -6.0°C — acima do limite de -11°C. Manutenção foi realizada há 2 dias."
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
        print("🧪 ANÁLISE: O LLM FILTROU O RUÍDO?")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            # CAUSA REAL
            ("⚡ Identificou queda de tensão / subtensão",
             any(x in trace_text for x in ["tensão", "198", "voltagem", "subtensão", "voltage", "elétric"])),
            ("🏢 Correlacionou com problemas do prédio (HVAC/luzes)",
             any(x in trace_text for x in ["hvac", "prédio", "outros equipamentos", "luzes", "iluminação", "building"])),
            ("📉 Notou corrente BAIXA do compressor (3.8A vs esperado ~4.5A)",
             any(x in trace_text for x in ["3.8", "corrente baixa", "baixa corrente", "abaixo do esperado"])),
            ("🔌 Recomendou ação na rede elétrica (concessionária/estabilizador)",
             any(x in trace_text for x in ["concessionária", "estabilizador", "rede elétrica", "energia", "fornecimento"])),

            # RED HERRINGS DESCARTADOS
            ("✅ NÃO culpou a manutenção recente",
             not any(x in trace_text for x in ["manutenção causou", "erro da manutenção", "técnico errou", "falha na manutenção"])),
            ("✅ Descartou ou minimizou barulho (é do outro freezer)",
             any(x in trace_text for x in ["adjacente", "outro", "vibração normal", "2.0", "barulho"]) or "barulho" not in trace_text),
            ("✅ Descartou ou minimizou funcionário novo como causa",
             any(x in trace_text for x in ["treinad", "3 abertura", "normal"]) or "pedro" not in trace_text),
            ("✅ Reconheceu que Carnaval/portas contribuem pouco para 12°C de desvio",
             any(x in trace_text for x in ["marginal", "menor", "pouco", "secundári", "carnaval"]) or "carnaval" not in trace_text),

            # QUALIDADE DA ANÁLISE
            ("🧠 Notou que consumo de energia está BAIXO (7.5 vs 9.5 normal)",
             any(x in trace_text for x in ["7.5", "consumo baixo", "energia baixa", "menos energia", "abaixo da média", "consumo de energia", "9.5", "9.8"])),
            ("🔧 NÃO recomendou mexer no compressor",
             not any(x in trace_text for x in ["trocar compressor", "reparar compressor", "manutenção do compressor"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 9:
            print("\n  🏆 PERFEITO! O LLM filtrou TODOS os red herrings!")
        elif passed >= 7:
            print("\n  ✅ MUITO BOM! Encontrou a causa real e descartou a maioria dos ruídos.")
        elif passed >= 5:
            print("\n  ⚠️ PARCIAL — Encontrou a causa mas caiu em alguns red herrings.")
        else:
            print("\n  ❌ FRACO — Não conseguiu separar sinal de ruído.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
