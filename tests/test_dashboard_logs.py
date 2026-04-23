#!/usr/bin/env python3
"""
Script para gerar eventos via API e visualizar logs no dashboard.

Este script envia eventos para o endpoint /v1/events/trigger, que:
1. Processa através do CAOS brain
2. Gera logs que são capturados automaticamente
3. Logs ficam disponíveis em /v1/logs/
4. Dashboard exibe automaticamente
"""

import requests
import time
import json
from typing import Dict, Any


API_URL = "http://localhost:8080"


def test_api_connection() -> bool:
    """Testa se a API está respondendo."""
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def trigger_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Envia um evento para processamento via API."""
    response = requests.post(
        f"{API_URL}/v1/events/trigger",
        json=event_data,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_logs(limit: int = 20) -> Dict[str, Any]:
    """Busca logs da API."""
    response = requests.get(
        f"{API_URL}/v1/logs/",
        params={"limit": limit},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def main():
    """Executa teste de geração de logs."""
    print("=" * 70)
    print("🧪 Teste de Logs via API - CAOS")
    print("=" * 70)
    print()
    
    # Verificar conexão
    print("🔌 Verificando conexão com API...")
    if not test_api_connection():
        print("❌ Erro: API não está respondendo em", API_URL)
        print("   Certifique-se de que o servidor está rodando:")
        print("   uvicorn main:app --app-dir src --port 8080 --reload")
        return 1
    print("✅ API está online")
    print()
    
    # Eventos de teste
    events = [
        {
            "name": "🌡️ Temperatura Alta - Chiller",
            "data": {
                "tenant_id": "viva",
                "asset_id": "CHILLER-01",
                "severity": "HIGH",
                "metric": "temperature",
                "value": 85.5,
                "location": "Sala de Servidores",
                "payload": {
                    "description": "Temperatura acima do normal detectada",
                    "threshold": 75.0,
                },
            },
        },
        {
            "name": "⚡ Vibração Anormal - Bomba",
            "data": {
                "tenant_id": "viva",
                "asset_id": "PUMP-05",
                "severity": "MEDIUM",
                "metric": "vibration",
                "value": 12.8,
                "location": "Zona Industrial",
                "payload": {
                    "description": "Vibração acima do esperado",
                    "threshold": 10.0,
                },
            },
        },
        {
            "name": "🔴 Pressão Crítica - Compressor",
            "data": {
                "tenant_id": "viva",
                "asset_id": "COMPRESSOR-03",
                "severity": "CRITICAL",
                "metric": "pressure",
                "value": 150.0,
                "location": "Planta Principal",
                "payload": {
                    "description": "Pressão crítica no compressor",
                    "threshold": 120.0,
                },
            },
        },
    ]
    
    print(f"📋 Processando {len(events)} eventos...\n")
    
    results = []
    for i, event in enumerate(events, 1):
        print(f"{i}. {event['name']}")
        print(f"   Asset: {event['data']['asset_id']}")
        print(f"   Severity: {event['data']['severity']}")
        
        try:
            start = time.time()
            result = trigger_event(event['data'])
            elapsed = (time.time() - start) * 1000
            
            print(f"   ✅ Processado em {elapsed:.0f}ms")
            print(f"   Event ID: {result['event_id']}")
            print(f"   Decisão: {result['decision']}")
            print(f"   Verdict: {result['verdict_score']:.4f}")
            
            if result.get('requires_approval'):
                print(f"   ⚠️  Requer aprovação humana")
            
            results.append(result)
            
        except Exception as e:
            print(f"   ❌ Erro: {e}")
        
        print()
        time.sleep(0.5)  # Pequeno delay entre eventos
    
    # Verificar logs
    print("=" * 70)
    print("📊 Verificando Logs Capturados")
    print("=" * 70)
    print()
    
    time.sleep(1)  # Aguardar logs serem processados
    
    try:
        logs_data = get_logs(limit=50)
        total_logs = logs_data['total']
        
        print(f"✅ Total de logs capturados: {total_logs}")
        print()
        
        if total_logs > 0:
            print("🔍 Últimos 15 logs:")
            print()
            
            for i, log in enumerate(logs_data['logs'][:15], 1):
                icon = {
                    'info': '📘',
                    'warning': '⚠️',
                    'error': '❌',
                    'debug': '🐛',
                }.get(log['level'], '📝')
                
                timestamp = log['timestamp'][11:19]  # HH:MM:SS
                level = log['level'].upper()
                event = log['event'][:45]
                
                print(f"{i:2d}. {icon} [{level:7s}] {timestamp} - {event}")
        else:
            print("⚠️  Nenhum log capturado ainda")
            print("   Os logs aparecem após processar eventos via API")
    
    except Exception as e:
        print(f"❌ Erro ao buscar logs: {e}")
    
    print()
    print("=" * 70)
    print("📊 Visualizar no Dashboard")
    print("=" * 70)
    print()
    print("1. Abra: http://localhost:8080/dashboard")
    print("2. Clique em 'Logs' na sidebar esquerda")
    print("3. Todos os logs processados aparecerão lá!")
    print()
    print("Ou via API:")
    print(f"   curl '{API_URL}/v1/logs/?limit=50'")
    print()
    print("=" * 70)
    print(f"✅ {len(results)} eventos processados com sucesso!")
    print("=" * 70)
    
    return 0


if __name__ == "__main__":
    try:
        exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompido pelo usuário")
        exit(130)
    except Exception as e:
        print(f"\n\n❌ Erro: {e}")
        exit(1)
