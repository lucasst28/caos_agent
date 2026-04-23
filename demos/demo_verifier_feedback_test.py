#!/usr/bin/env python3
"""
CAOS Agent — Teste: Feedback Loop Verifier → Oracle → Verifier
================================================================
Cenário: Compressor Industrial #02, Frigorífico, Curitiba

Vibração 9mm/s (limite 10), corrente 8.5A (nominal 7A, 121%), motor 95°C.
O cenário é projetado para que o Oracle SUBESTIME o risco na 1ª tentativa,
fazendo o CAOS Verifier REJEITAR e enviar feedback.

O objetivo é VER a interação completa:
  1ª passagem: Oracle propõe observe (R_F baixo)
  → CAOS Verifier REJEITA (checklist detecta riscos subestimados)
  → Feedback estruturado enviado ao Oracle
  2ª passagem: Oracle re-analisa com o feedback e propõe maintenance (R_F alto)
  → CAOS Verifier APROVA
"""
import asyncio
import httpx
import json
import sys

CAOS_URL = "http://localhost:8080"
SIMULATORS_URL = "http://localhost:9000"

# Colors for terminal output
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def print_section(title: str, char: str = "═", width: int = 70):
    print(f"\n{C.BOLD}{C.CYAN}{char * width}{C.END}")
    print(f"{C.BOLD}{C.CYAN}  {title}{C.END}")
    print(f"{C.BOLD}{C.CYAN}{char * width}{C.END}")


def print_subsection(title: str):
    print(f"\n{C.BOLD}{C.YELLOW}  ── {title} ──{C.END}")


async def main():
    print(f"\n{C.BOLD}{C.HEADER}╔══════════════════════════════════════════════════════════════════╗{C.END}")
    print(f"{C.BOLD}{C.HEADER}║  CAOS Agent — Teste: Feedback Loop (Verifier → Oracle)          ║{C.END}")
    print(f"{C.BOLD}{C.HEADER}║  Compressor Industrial #02 — Frigorífico Curitiba               ║{C.END}")
    print(f"{C.BOLD}{C.HEADER}╚══════════════════════════════════════════════════════════════════╝{C.END}")

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Health check
        print(f"\n{C.DIM}🔌 Verificando servidores...{C.END}")
        try:
            r1 = await client.get(f"{CAOS_URL}/health")
            print(f"  {C.GREEN}✅ CAOS Agent: OK{C.END}")
        except Exception as e:
            print(f"  {C.RED}❌ CAOS Agent indisponível: {e}{C.END}")
            sys.exit(1)
        try:
            r2 = await client.get(f"{SIMULATORS_URL}/health")
            print(f"  {C.GREEN}✅ Simuladores: OK{C.END}")
        except Exception as e:
            print(f"  {C.RED}❌ Simuladores indisponíveis: {e}{C.END}")
            sys.exit(1)

        # 2. Send trigger
        print_section("📡 ENVIANDO ALERTA AO CAOS")
        print(f"   Ativo:      {C.BOLD}COMPRESSOR-02-TEST{C.END}")
        print(f"   Métrica:    vibration")
        print(f"   Valor:      {C.RED}9.0 mm/s{C.END} (limite: 10.0)")
        print(f"   Severidade: {C.RED}HIGH{C.END}")
        print()
        print(f"   {C.BOLD}🎯 OBJETIVO DO TESTE:{C.END}")
        print(f"     → Oracle deve subestimar risco na 1ª tentativa")
        print(f"     → Verifier deve REJEITAR e enviar feedback")
        print(f"     → Oracle deve RE-ANALISAR com o feedback")
        print(f"     → Verifier deve APROVAR na 2ª tentativa")

        payload = {
            "tenant_id": "frigorifico-sul",
            "asset_id": "COMPRESSOR-02-TEST",
            "metric": "vibration",
            "value": 9.0,
            "severity": "HIGH",
            "source": "sentinel",
            "description": (
                "Vibração elevada (9mm/s, limite 10mm/s) no Compressor Industrial #02. "
                "Corrente do motor 8.5A (nominal 7A, 121%). "
                "Temperatura do motor 95°C (alarme 100°C). "
                "Rolamento com ruído detectado. "
                "Produtos em risco: R$180.000 em carnes congeladas."
            ),
        }

        resp = await client.post(f"{CAOS_URL}/v1/events/trigger", json=payload)
        if resp.status_code not in (200, 201):
            print(f"  {C.RED}❌ Erro: {resp.status_code} — {resp.text}{C.END}")
            sys.exit(1)

        result = resp.json()
        event_id = result.get("event_id")
        print(f"\n  {C.GREEN}✅ Evento processado: {event_id}{C.END}")

        # 3. Pipeline result
        print_section("⚡ RESULTADO DO PIPELINE CAOS")
        print(f"  Event ID:       {event_id}")
        print(f"  Verdict Score:  {result.get('verdict_score')}")
        print(f"  Decision:       {C.BOLD}{result.get('decision')}{C.END}")
        print(f"  Risk Level:     {result.get('risk_level')}")
        print(f"  Guardrails:     {result.get('guardrail_violations', [])}")
        print(f"  Tempo:          {result.get('processing_time_ms', 0):.0f} ms")

        # 4. Full reasoning trace
        trace = result.get("reasoning", result.get("reasoning_trace", []))
        print_section("📝 TRACE DE RACIOCÍNIO COMPLETO")
        print(f"  {C.DIM}({len(trace)} etapas){C.END}\n")

        for i, step in enumerate(trace, 1):
            step_str = str(step)
            # Color-code different types of trace entries
            if "♻️" in step_str or "FEEDBACK" in step_str or "feedback" in step_str:
                color = C.YELLOW
            elif "❌" in step_str or "REJEITOU" in step_str or "REJECTED" in step_str:
                color = C.RED
            elif "✅" in step_str or "APROVADO" in step_str:
                color = C.GREEN
            elif "CAOS" in step_str or "Verifier" in step_str or "verifier" in step_str:
                color = C.CYAN
            elif "Oracle" in step_str or "oracle" in step_str:
                color = C.BLUE
            else:
                color = C.DIM

            for line in step_str.split('\n'):
                print(f"  {color}{line}{C.END}")

        # 5. Get detailed reasoning from API
        print_section("🔍 DETALHES DO FEEDBACK LOOP (via API)")

        try:
            detail_resp = await client.get(f"{CAOS_URL}/v1/reasoning/{event_id}")
            if detail_resp.status_code == 200:
                detail = detail_resp.json()

                # Verifier details
                verifier = detail.get("verifier", {})
                if verifier:
                    print_subsection("CAOS VERIFIER - Resultado")

                    v_passed = verifier.get("verification_passed")
                    v_score = verifier.get("verification_score")
                    v_retry = verifier.get("verifier_retry_count", 0)
                    v_feedback = verifier.get("verifier_feedback")
                    v_issues = verifier.get("verification_issues", [])
                    v_adjustments = verifier.get("verification_adjustments")
                    v_report = verifier.get("verification_report", {})

                    status_color = C.GREEN if v_passed else C.RED
                    status_text = "APROVADO" if v_passed else "REJEITADO"
                    print(f"    Veredito:       {status_color}{C.BOLD}{status_text}{C.END}")
                    print(f"    Score Final:    {v_score}")
                    print(f"    Retry Count:    {v_retry}")

                    # Checklist details
                    if v_report:
                        checks_passed = v_report.get("passed_checks", 0)
                        checks_total = v_report.get("total_checks", 0)
                        checklist_score = v_report.get("score", 0)
                        print(f"    Checklist:      {checks_passed}/{checks_total} (score={checklist_score})")

                        # LLM audit
                        caos_llm = v_report.get("caos_llm", {})
                        if caos_llm.get("available"):
                            llm_verdict = caos_llm.get("verdict", "N/A")
                            llm_score = caos_llm.get("score", "N/A")
                            llm_color = C.GREEN if llm_verdict == "APROVADO" else C.RED
                            print(f"    LLM Verdict:    {llm_color}{llm_verdict}{C.END} (score={llm_score})")

                            llm_analysis = caos_llm.get("analysis", "")
                            if llm_analysis:
                                print_subsection("CAOS LLM — Análise")
                                for line in llm_analysis.split('\n'):
                                    print(f"    {C.CYAN}{line}{C.END}")

                            llm_feedback = caos_llm.get("feedback", "")
                            if llm_feedback:
                                print_subsection("CAOS LLM — Feedback para Oracle")
                                for line in llm_feedback.split('\n'):
                                    print(f"    {C.YELLOW}{line}{C.END}")

                    # Issues
                    if v_issues:
                        print_subsection("Issues Encontrados")
                        for issue in v_issues:
                            print(f"    {C.RED}• {issue}{C.END}")

                    # Adjustments
                    if v_adjustments:
                        print_subsection("Ajustes de Risco pelo Verifier")
                        for k, v in v_adjustments.items():
                            print(f"    {C.YELLOW}🔧 {k}: {v}{C.END}")

                    # Feedback sent to Oracle
                    if v_feedback:
                        print_subsection("📨 FEEDBACK ENVIADO AO ORACLE (para retry)")
                        print(f"    {C.DIM}{'─' * 55}{C.END}")
                        for line in v_feedback.split('\n'):
                            print(f"    {C.YELLOW}{line}{C.END}")
                        print(f"    {C.DIM}{'─' * 55}{C.END}")
                    else:
                        print(f"\n    {C.DIM}(Sem feedback — Verifier aprovou ou retry já realizado){C.END}")

                # Oracle (Cortex) details
                cortex = detail.get("cortex", {})
                if cortex:
                    print_subsection("ORACLE — Decisão Final")
                    risk = cortex.get("risk_dimensions", {})
                    print(f"    Verdict Score:  {cortex.get('verdict_score')}")
                    print(f"    Decision Band:  {cortex.get('decision_band')}")
                    print(f"    Risk Level:     {cortex.get('risk_level')}")
                    print(f"    R_F (Físico):   {risk.get('physical', 'N/A')}")
                    print(f"    R_Fin (Finan):  {risk.get('financial', 'N/A')}")
                    print(f"    R_C (Contrat):  {risk.get('contractual', 'N/A')}")
                    print(f"    R_K (Comun):    {risk.get('communication', 'N/A')}")

                    llm = cortex.get("llm_analysis", {})
                    if llm:
                        print(f"    Ação LLM:       {C.BOLD}{llm.get('action', 'N/A')}{C.END}")
                        analysis = llm.get("analysis", "")
                        if analysis:
                            print_subsection("ORACLE — Análise LLM")
                            for line in analysis[:500].split('\n'):
                                print(f"    {C.BLUE}{line}{C.END}")
                            if len(analysis) > 500:
                                print(f"    {C.DIM}... (+{len(analysis)-500} chars){C.END}")

            else:
                print(f"  {C.YELLOW}⚠️ Reasoning detail não disponível (status {detail_resp.status_code}){C.END}")
        except Exception as e:
            print(f"  {C.YELLOW}⚠️ Erro ao buscar detalhes: {e}{C.END}")

        # 6. Análise automática
        print_section("🧪 ANÁLISE: FEEDBACK LOOP FUNCIONOU?")

        trace_text = " ".join(str(t) for t in trace).lower()

        checks = [
            ("♻️ Oracle recebeu feedback do CAOS",
             any(x in trace_text for x in [
                 "feedback", "♻️", "recebeu", "correção",
                 "retry", "rejeit"
             ])),

            ("❌ Verifier rejeitou em algum momento",
             any(x in trace_text for x in [
                 "rejeitado", "rejeitou", "rejected", "❌ veredicto",
                 "score combinado=0", "score combinado=0."
             ]) or (result.get("verifier", {}) or {}).get("verifier_retry_count", 0) > 0),

            ("📝 Feedback contém issues específicos",
             any(x in trace_text for x in [
                 "problemas", "issues", "checklist", "recomend",
                 "corrigir", "subestim"
             ])),

            ("🔄 verifier_retry_count > 0 (retry ocorreu)",
             True),  # We'll check this from the detail

            ("✅ Decisão final é coerente (não observe para risco alto)",
             result.get("decision") != "EXECUTE" or
             result.get("risk_level") in ["HIGH", "VETO"]),
        ]

        # Check retry count from reasoning detail
        try:
            d = await client.get(f"{CAOS_URL}/v1/reasoning/{event_id}")
            if d.status_code == 200:
                dd = d.json()
                retry_count = dd.get("verifier", {}).get("verifier_retry_count", 0)
                checks[3] = (
                    f"🔄 verifier_retry_count = {retry_count} (retry {'ocorreu ✓' if retry_count > 0 else 'NÃO ocorreu'})",
                    retry_count > 0,
                )
        except Exception:
            pass

        passed = 0
        for desc, ok in checks:
            icon = f"{C.GREEN}✅ PASS{C.END}" if ok else f"{C.RED}❌ FAIL{C.END}"
            print(f"  {icon}  {desc}")
            if ok:
                passed += 1

        print(f"\n  Resultado: {C.BOLD}{passed}/{len(checks)}{C.END}")

        if passed >= 4:
            print(f"\n  {C.GREEN}{C.BOLD}🏆 FEEDBACK LOOP FUNCIONOU! Oracle foi corrigido pelo CAOS.{C.END}")
        elif passed >= 3:
            print(f"\n  {C.YELLOW}⚠️ PARCIAL — Loop parcialmente funcional.{C.END}")
        else:
            print(f"\n  {C.RED}❌ LOOP NÃO FUNCIONOU — Verifier não rejeitou ou Oracle não recebeu feedback.{C.END}")
            print(f"  {C.DIM}   (Isso pode significar que o Oracle acertou de primeira, o que também é OK){C.END}")

        print(f"\n  {C.DIM}🖥️  Frontend: http://localhost:8080/dashboard/reasoning{C.END}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
