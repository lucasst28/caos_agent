#!/usr/bin/env python3
"""
Script de teste para gerar logs do CAOS que aparecerão no dashboard.

Este script:
1. Processa vários eventos através do CAOS
2. Gera logs de diferentes níveis (info, warning, error)
3. Todos os logs são capturados e ficam disponíveis em /v1/logs/
4. O dashboard busca e exibe esses logs automaticamente
"""

import asyncio
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import structlog
from caos.schemas.enums import Severity
from caos.core.brain import process_trigger

logger = structlog.get_logger(__name__)


async def test_event(event_id: str, asset_id: str, severity: Severity, description: str):
    """Process a test event and generate logs."""
    
    logger.info(
        "test_event_started",
        event_id=event_id,
        asset_id=asset_id,
        severity=severity.value,
        description=description,
    )
    
    try:
        trigger_data = {
            "event_id": event_id,
            "source": "manual",
            "timestamp": "2026-02-11T15:30:00Z",
            "severity": severity.value,
            "payload": {
                "description": description,
                "test": True,
            },
            "context": {
                "tenant_id": "test-tenant",
                "asset_id": asset_id,
                "location": "Lab",
            },
            "metric": "temperature",
            "value": 75.0,
        }
        
        # Process through CAOS brain
        final_state = await process_trigger(trigger_data)
        
        logger.info(
            "test_event_completed",
            event_id=event_id,
            decision=str(final_state.get("decision_band")),
            verdict_score=final_state.get("verdict_score"),
        )
        
        return final_state
        
    except Exception as e:
        logger.error(
            "test_event_failed",
            event_id=event_id,
            error=str(e),
        )
        raise


async def run_tests():
    """Run multiple test events to generate logs."""
    
    print("=" * 70)
    print("🧪 CAOS - Teste de Geração de Logs")
    print("=" * 70)
    print()
    print("Este teste irá:")
    print("  1. Processar vários eventos através do CAOS")
    print("  2. Gerar logs de INFO, WARNING e ERROR")
    print("  3. Todos os logs ficarão disponíveis em /v1/logs/")
    print("  4. O dashboard irá buscá-los e exibi-los automaticamente")
    print()
    print("=" * 70)
    print()
    
    tests = [
        {
            "event_id": "test_001",
            "asset_id": "CHILLER-01",
            "severity": Severity.LOW,
            "description": "Teste de evento de baixa severidade",
        },
        {
            "event_id": "test_002",
            "asset_id": "PUMP-05",
            "severity": Severity.MEDIUM,
            "description": "Teste de evento de severidade média",
        },
        {
            "event_id": "test_003",
            "asset_id": "SENSOR-12",
            "severity": Severity.HIGH,
            "description": "Teste de evento de alta severidade",
        },
        {
            "event_id": "test_004",
            "asset_id": "COMPRESSOR-03",
            "severity": Severity.CRITICAL,
            "description": "Teste de evento crítico",
        },
    ]
    
    results = []
    
    for i, test in enumerate(tests, 1):
        print(f"\n🔄 Teste {i}/{len(tests)}: {test['asset_id']} ({test['severity'].value})")
        print(f"   Descrição: {test['description']}")
        
        try:
            start_time = time.time()
            result = await test_event(**test)
            elapsed = (time.time() - start_time) * 1000
            
            print(f"   ✅ Processado em {elapsed:.2f}ms")
            print(f"   Decisão: {result.get('decision_band')}")
            print(f"   Verdict Score: {result.get('verdict_score')}")
            
            results.append({
                "test": test,
                "result": result,
                "success": True,
                "elapsed_ms": elapsed,
            })
            
        except Exception as e:
            print(f"   ❌ Erro: {e}")
            results.append({
                "test": test,
                "success": False,
                "error": str(e),
            })
        
        # Small delay between tests
        await asyncio.sleep(0.5)
    
    # Summary
    print()
    print("=" * 70)
    print("📊 Resumo dos Testes")
    print("=" * 70)
    
    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful
    
    print(f"\n✅ Testes bem-sucedidos: {successful}/{len(results)}")
    print(f"❌ Testes falhados: {failed}/{len(results)}")
    
    if successful > 0:
        avg_time = sum(r.get("elapsed_ms", 0) for r in results if r["success"]) / successful
        print(f"⏱️  Tempo médio de processamento: {avg_time:.2f}ms")
    
    print()
    print("=" * 70)
    print("📊 Visualizar Logs no Dashboard")
    print("=" * 70)
    print()
    print("1. Abra: http://localhost:8080/dashboard")
    print("2. Clique em 'Logs' na sidebar")
    print("3. Você verá todos os logs gerados acima!")
    print()
    print("Ou via API:")
    print("   curl 'http://localhost:8080/v1/logs/?limit=20'")
    print()
    print("=" * 70)
    
    return results


async def main():
    """Main entry point."""
    try:
        results = await run_tests()
        
        # Exit with appropriate code
        failed = sum(1 for r in results if not r["success"])
        sys.exit(0 if failed == 0 else 1)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Teste interrompido pelo usuário")
        sys.exit(130)
    except Exception as e:
        logger.error("test_suite_error", error=str(e))
        print(f"\n\n❌ Erro no teste: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Run with asyncio
    asyncio.run(main())
