#!/usr/bin/env python3
"""
CAOS Agent — Teste Nível 2: Múltiplas Falhas Simultâneas
=========================================================
Cenário: Distribuidora Gelatto, Ribeirão Preto — FREEZER-04
Câmara fria industrial com TRÊS falhas combinadas:

  FATOR 1: Compressor degradando (COP 2.1, vibração 3.8mm/s, gás 78%)
  FATOR 2: Vedação da porta comprometida (72%, condensação detectada)
  FATOR 3: Temperatura ambiente extrema (38°C + umidade 75%)

Nenhum fator sozinho explica o desvio total (-8°C vs ideal -20°C).
O LLM deve identificar os TRÊS fatores e sua contribuição combinada.
"""
import asyncio
import httpx
import sys

CAOS_URL = "http://localhost:8000"
SIMULATORS_URL = "http://localhost:9000"


async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  CAOS Agent — Teste Nível 2: Múltiplas Falhas Simultâneas   ║")
    print("║          Distribuidora Gelatto - FREEZER-04                 ║")
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
        print("   Ativo:      FREEZER-04 (Câmara Fria Industrial)")
        print("   Métrica:    temperature")
        print("   Valor:      -8.0°C (acima de -11°C)")
        print("   Severidade: CRITICAL")
        print("   🎯 DESAFIO:  LLM deve encontrar 3 causas simultâneas!")
        print()
        print("   Fatores escondidos nos dados:")
        print("     1️⃣  Compressor degradando (COP 2.1, vibração 3.8mm/s, gás 78%)")
        print("     2️⃣  Vedação porta 72% + condensação (ar quente entrando)")
        print("     3️⃣  Temperatura ambiente 38°C + umidade 75%")

        payload = {
            "tenant_id": "viva-foods",
            "asset_id": "FREEZER-04",
            "metric": "temperature",
            "value": -8.0,
            "severity": "CRITICAL",
            "source": "sentinel",
            "description": "Temperatura FREEZER-04 em -8.0°C — acima do limite de -11°C. Tendência de subida nas últimas 6h."
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
        print("🧪 ANÁLISE: O LLM ENCONTROU AS 3 FALHAS?")
        print("=" * 65)

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("🔧 Fator 1: Identificou degradação do compressor",
             any(x in trace_text for x in ["compressor", "cop", "eficiência", "vibração", "desgaste", "degradação", "degradando"])),
            ("   → Notou vibração elevada (3.8 mm/s)",
             any(x in trace_text for x in ["3.8", "vibração elevada", "vibração alta"])),
            ("   → Notou refrigerante/gás baixo (78%)",
             any(x in trace_text for x in ["refrigerante", "gás", "78%", "carga baixa", "micro-vazamento"])),
            ("   → Notou COP baixo (2.1)",
             any(x in trace_text for x in ["cop", "2.1", "eficiência baixa", "performance"])),
            ("🚪 Fator 2: Identificou vedação comprometida",
             any(x in trace_text for x in ["vedação", "72%", "porta", "seal", "gap", "folga"])),
            ("   → Notou condensação (ar quente entrando)",
             any(x in trace_text for x in ["condensação", "ar quente", "infiltração", "úmido"])),
            ("🌡️ Fator 3: Identificou temperatura ambiente extrema",
             any(x in trace_text for x in ["38", "ambiente", "calor", "verão", "temperatura externa"])),
            ("❄️ Notou gelo no evaporador (consequência da combinação)",
             any(x in trace_text for x in ["gelo", "frost", "evaporador", "acúmulo"])),
            ("📈 Notou tendência de subida na temperatura",
             any(x in trace_text for x in ["tendência", "subindo", "piorando", "progressiv", "aumenta", "crescente", "últimas"])),
            ("🔗 Conclusão: Reconheceu MÚLTIPLOS fatores combinados",
             any(x in trace_text for x in ["combin", "múltipl", "conjunto", "simultanea", "multi-fator", "fatores"])),
        ]

        passed = 0
        for desc, ok in checks:
            icon = "✅ PASS " if ok else "❌ FAIL "
            print(f"  {icon} {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {passed}/{len(checks)} verificações passaram")

        if passed >= 8:
            print("\n  🏆 EXCEPCIONAL! O LLM identificou todas as falhas combinadas!")
        elif passed >= 6:
            print("\n  ✅ BOM! O LLM encontrou a maioria dos fatores.")
        elif passed >= 4:
            print("\n  ⚠️ PARCIAL — Encontrou alguns fatores mas perdeu outros.")
        else:
            print("\n  ❌ FRACO — Não conseguiu correlacionar a falha composta.")

        print(f"\n  🖥️ Frontend: http://localhost:8080/dashboard/reasoning")


if __name__ == "__main__":
    asyncio.run(main())
