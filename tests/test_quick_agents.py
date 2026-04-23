#!/usr/bin/env python3
"""
Teste Rápido - Gera Logs de Cada Agente
========================================
Demonstração rápida de logs dos 3 agentes simulados.
"""

import asyncio
import httpx

SIMULATOR_URL = "http://localhost:9000"

async def quick_test():
    """Teste rápido - um exemplo de cada agente."""
    print("\n🚀 TESTE RÁPIDO DE LOGS DOS AGENTES\n")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 1. ATLAS - Consultar contexto
            print("1️⃣  ATLAS - Consultando contexto do ativo...")
            resp = await client.get(
                f"{SIMULATOR_URL}/atlas/v1/tenants/tenant_local/assets/CHILLER-04/context"
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ Temp: {data['current_state']['temperature']}°C, "
                      f"Status: {data['current_state']['status']}")
            
            await asyncio.sleep(0.5)
            
            # 2. SENTINEL - Gerar alerta
            print("\n2️⃣  SENTINEL - Gerando alerta de temperatura alta...")
            resp = await client.post(
                f"{SIMULATOR_URL}/sentinel/v1/alerts/scenario/chiller_overtemp"
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ Alerta: {data['alert_id']}, "
                      f"Severidade: {data['severity']}, "
                      f"Valor: {data['value']}°C")
            
            await asyncio.sleep(0.5)
            
            # 3. ORACLE - Fazer predição
            print("\n3️⃣  ORACLE - Fazendo predição de falha...")
            resp = await client.post(
                f"{SIMULATOR_URL}/oracle/v1/predict",
                json={
                    "asset_id": "CHILLER-04",
                    "metric": "failure_probability",
                    "horizon_hours": 24,
                    "current_value": 95.0
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ Predição: {data['prediction_id']}, "
                      f"Risco: {data['predicted_value']*100:.1f}%, "
                      f"Confiança: {data['confidence']*100:.1f}%")
            
            print("\n✅ Teste completo! Verifique os logs em:")
            print("   • Dashboard: http://localhost:8080/dashboard")
            print("   • API: curl 'http://localhost:8080/v1/logs/?limit=10'\n")
            
        except httpx.ConnectError:
            print("❌ Erro: Simuladores não estão rodando!")
            print("   Execute: ./start_simulators.sh\n")

if __name__ == "__main__":
    asyncio.run(quick_test())
