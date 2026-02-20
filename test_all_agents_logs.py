#!/usr/bin/env python3
"""
Teste Completo dos Logs dos Agentes Simulados
==============================================

Demonstra os logs específicos de cada agente:
- ATLAS: Digital Twin, contexto de ativos, manuais, contratos
- SENTINEL: Alertas e cenários
- ORACLE: Predições e simulações

Inicia os simuladores e faz requisições para gerar logs.
"""

import asyncio
import subprocess
import time
from typing import Any

import httpx


SIMULATOR_URL = "http://localhost:9000"
CAOS_URL = "http://localhost:8080"


def print_section(title: str):
    """Print section header."""
    print(f"\n{'=' * 70}")
    print(f"🔷 {title}")
    print('=' * 70)


async def test_atlas_agent():
    """Testa endpoints do ATLAS e gera logs."""
    print_section("ATLAS - Digital Twin Agent")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Consultar contexto do ativo
        print("\n1. 🏭 Consultando contexto do CHILLER-04...")
        response = await client.get(
            f"{SIMULATOR_URL}/atlas/v1/tenants/tenant_local/assets/CHILLER-04/context"
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Temperatura: {data['current_state']['temperature']}°C")
            print(f"   ✅ Vibração: {data['current_state']['vibration']} mm/s")
            print(f"   ✅ Status: {data['current_state']['status']}")
        
        await asyncio.sleep(0.5)
        
        # 2. Buscar em manuais
        print("\n2. 📖 Buscando 'temperatura' nos manuais...")
        response = await client.get(
            f"{SIMULATOR_URL}/atlas/v1/tenants/tenant_local/assets/CHILLER-04/manuals",
            params={"query": "temperatura"}
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Encontrados {data['total']} resultados")
            for result in data['results'][:2]:
                print(f"      • {result}")
        
        await asyncio.sleep(0.5)
        
        # 3. Consultar contratos
        print("\n3. 📄 Consultando contrato do ativo...")
        response = await client.get(
            f"{SIMULATOR_URL}/atlas/v1/tenants/tenant_local/assets/CHILLER-04/contracts"
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Contrato: {data['contract_id']}")
            print(f"   ✅ Tier: {data['tier']}")
            print(f"   ✅ SLA: {data['sla_response_minutes']} minutos")
        
        # 4. Testar outros ativos
        for asset in ["PUMP-01", "GENSET-02"]:
            print(f"\n4. 🔍 Consultando contexto do {asset}...")
            response = await client.get(
                f"{SIMULATOR_URL}/atlas/v1/tenants/tenant_local/assets/{asset}/context"
            )
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ {data['location']} - Status: {data['current_state']['status']}")
            await asyncio.sleep(0.3)


async def test_sentinel_agent():
    """Testa endpoints do SENTINEL e gera logs."""
    print_section("SENTINEL - Alert Agent")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Listar cenários disponíveis
        print("\n1. 📋 Listando cenários de alerta...")
        response = await client.get(f"{SIMULATOR_URL}/sentinel/v1/alerts/scenarios")
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ {len(data['scenarios'])} cenários disponíveis")
        
        await asyncio.sleep(0.5)
        
        # 2. Disparar alertas por cenário
        scenarios = [
            ("chiller_overtemp", "🌡️ Chiller sobreaquecendo"),
            ("pump_pressure", "⚡ Bomba com pressão alta"),
            ("genset_overtemp", "🔴 Gerador crítico"),
            ("chiller_vibration", "📊 Vibração excessiva"),
        ]
        
        for scenario_name, description in scenarios:
            print(f"\n2. {description}...")
            response = await client.post(
                f"{SIMULATOR_URL}/sentinel/v1/alerts/scenario/{scenario_name}"
            )
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Alerta ID: {data['alert_id']}")
                print(f"   ✅ Severidade: {data['severity']}")
                print(f"   ✅ Valor: {data['value']} (limite: {data['threshold_violated']})")
            await asyncio.sleep(0.5)
        
        # 3. Gerar alerta customizado
        print("\n3. 🎯 Gerando alerta customizado...")
        response = await client.post(
            f"{SIMULATOR_URL}/sentinel/v1/alerts/generate",
            json={
                "asset_id": "CHILLER-04",
                "metric": "temperature",
                "severity": "CRITICAL",
                "value": 115.0
            }
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Alerta crítico gerado: {data['alert_id']}")
            print(f"   ✅ Mensagem: {data['message']}")


async def test_oracle_agent():
    """Testa endpoints do ORACLE e gera logs."""
    print_section("ORACLE - Prediction Agent")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Fazer predição simples
        print("\n1. 🔮 Predição de falha do CHILLER-04...")
        response = await client.post(
            f"{SIMULATOR_URL}/oracle/v1/predict",
            json={
                "tenant_id": "tenant_local",
                "asset_id": "CHILLER-04",
                "metric": "failure_probability",
                "horizon_hours": 24,
                "current_value": 95.0
            }
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Prediction ID: {data['prediction_id']}")
            print(f"   ✅ Probabilidade de falha: {data['predicted_value'] * 100:.1f}%")
            print(f"   ✅ Confiança: {data['confidence'] * 100:.1f}%")
            print(f"   ✅ Impacto financeiro: R$ {data['financial_impact']:,.2f}")
            print(f"   ✅ Recomendação: {data['recommendation']}")
        
        await asyncio.sleep(0.5)
        
        # 2. Predições para diferentes ativos
        for asset_id, current_temp in [("PUMP-01", 65.0), ("GENSET-02", 85.0)]:
            print(f"\n2. 🎯 Predição para {asset_id} (temp={current_temp}°C)...")
            response = await client.post(
                f"{SIMULATOR_URL}/oracle/v1/predict",
                json={
                    "asset_id": asset_id,
                    "metric": "failure_probability",
                    "horizon_hours": 48,
                    "current_value": current_temp
                }
            )
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Risco: {data['predicted_value'] * 100:.1f}%")
                print(f"   ✅ Ações recomendadas: {', '.join(data['recommended_actions'])}")
            await asyncio.sleep(0.5)
        
        # 3. Simulação what-if
        print("\n3. 🔬 Simulação what-if para CHILLER-04...")
        response = await client.post(
            f"{SIMULATOR_URL}/oracle/v1/simulate",
            json={
                "asset_id": "CHILLER-04",
                "metric": "failure_probability",
                "horizon_hours": 24
            }
        )
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Cenários analisados: {len(data['scenarios'])}")
            print(f"   ✅ Ação recomendada: {data['recommended']}")
            for scenario in data['scenarios']:
                print(f"      • {scenario['action']}: "
                      f"Risco={scenario['residual_risk']*100:.1f}%, "
                      f"Custo=R${scenario['cost']:,.2f}, "
                      f"Downtime={scenario['downtime_hours']}h")


async def check_logs_in_caos():
    """Verifica os logs capturados no CAOS."""
    print_section("Verificando Logs no CAOS")
    
    await asyncio.sleep(2)  # Aguardar logs serem processados
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{CAOS_URL}/v1/logs/", params={"limit": 50})
            if response.status_code == 200:
                data = response.json()
                print(f"\n📊 Total de logs capturados: {data['total']}\n")
                
                # Agrupar por agente
                agents = {}
                for log in data['logs']:
                    agent = log.get('context', {}).get('agent', 'CAOS')
                    if agent not in agents:
                        agents[agent] = []
                    agents[agent].append(log)
                
                # Mostrar estatísticas
                print("📈 Logs por agente:")
                for agent, logs in sorted(agents.items()):
                    print(f"   • {agent}: {len(logs)} logs")
                
                # Mostrar últimos logs de cada agente
                print("\n📝 Últimos logs por agente:\n")
                for agent in ["ATLAS", "SENTINEL", "ORACLE", "CAOS"]:
                    if agent in agents:
                        print(f"   🔷 {agent}:")
                        for log in agents[agent][:3]:
                            timestamp = log['timestamp'][11:19]
                            event = log['event']
                            context_str = ""
                            if log.get('context'):
                                ctx = log['context']
                                if 'asset_id' in ctx:
                                    context_str = f" - {ctx['asset_id']}"
                                if 'operation' in ctx:
                                    context_str += f" ({ctx['operation']})"
                            print(f"      [{timestamp}] {event}{context_str}")
                        print()
                
            else:
                print(f"   ❌ Erro ao consultar logs: {response.status_code}")
        except Exception as e:
            print(f"   ⚠️  CAOS não está rodando: {e}")
            print("   💡 Execute: ./dev.sh ou uvicorn main:app --app-dir src --port 8080 --reload")


async def main():
    """Executa todos os testes."""
    print("\n" + "="*70)
    print("🚀 TESTE COMPLETO DOS LOGS DOS AGENTES SIMULADOS")
    print("="*70)
    print("\n⚙️  Este teste irá:")
    print("   1. Consultar o ATLAS (Digital Twin)")
    print("   2. Gerar alertas no SENTINEL")
    print("   3. Fazer predições no ORACLE")
    print("   4. Verificar logs capturados no CAOS")
    print("\n📋 Certifique-se de que os simuladores estão rodando:")
    print("   uvicorn simulators.mock_agents:app --port 9000 --reload")
    print("\n📋 E que o CAOS está rodando:")
    print("   ./dev.sh ou uvicorn main:app --app-dir src --port 8080 --reload\n")
    
    input("Pressione ENTER para continuar...")
    
    try:
        # Testar cada agente
        await test_atlas_agent()
        await asyncio.sleep(1)
        
        await test_sentinel_agent()
        await asyncio.sleep(1)
        
        await test_oracle_agent()
        await asyncio.sleep(1)
        
        # Verificar logs
        await check_logs_in_caos()
        
        print("\n" + "="*70)
        print("✅ TESTE COMPLETO!")
        print("="*70)
        print("\n📊 Acesse o dashboard para ver os logs:")
        print("   http://localhost:8080/dashboard")
        print("\n📡 Ou consulte via API:")
        print("   curl 'http://localhost:8080/v1/logs/?limit=20' | jq")
        print()
        
    except httpx.ConnectError as e:
        print(f"\n❌ Erro de conexão: {e}")
        print("\n💡 Verifique se os serviços estão rodando:")
        print("   • Simuladores: uvicorn simulators.mock_agents:app --port 9000 --reload")
        print("   • CAOS: ./dev.sh")
    except Exception as e:
        print(f"\n❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
