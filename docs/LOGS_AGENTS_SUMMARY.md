# ✅ LOGS DOS AGENTES - RESUMO DA IMPLEMENTAÇÃO

## 🎯 O que foi feito

Adicionados **logs estruturados específicos** para cada um dos 4 agentes simulados no sistema CAOS:

### 1. **ATLAS** - Digital Twin Agent
- ✅ Logs de consulta de contexto de ativos
- ✅ Logs de busca em manuais técnicos  
- ✅ Logs de consulta de contratos
- ✅ Rastreamento de temperatura, vibração e status dos ativos

### 2. **SENTINEL** - Alert Agent  
- ✅ Logs de geração de alertas (WARNING level)
- ✅ Logs de cenários disparados
- ✅ Rastreamento de severidade, valores e thresholds excedidos

### 3. **ORACLE** - Prediction Agent
- ✅ Logs de predições de falha
- ✅ Logs de simulações what-if
- ✅ Rastreamento de probabilidade, confiança e impacto financeiro

### 4. **CAOS** - Sistema Central
- ✅ Já possui logs existentes (guardrails, eventos, decisões)
- ✅ Agora pode receber logs externos via HTTP

## 📡 Arquitetura de Logs

```
┌─────────────────┐         HTTP POST          ┌──────────────┐
│   SIMULADORES   │  ────────────────────────▶  │     CAOS     │
│  (Porta 9000)   │   /v1/logs/external       │ (Porta 8080) │
│                 │                             │              │
│  • ATLAS        │                             │ ┌──────────┐ │
│  • SENTINEL     │   Logs estruturados         │ │  deque   │ │
│  • ORACLE       │   (JSON)                    │ │ (1000)   │ │
└─────────────────┘                             │ └──────────┘ │
                                                │      │       │
                                                │      ▼       │
                                                │  Dashboard   │
                                                └──────────────┘
```

## 🚀 Como Usar

### 1. Iniciar os Serviços

```bash
# Terminal 1: Simuladores
./start_simulators.sh
# ou
uvicorn simulators.mock_agents:app --port 9000 --reload

# Terminal 2: CAOS
./dev.sh
# ou
uvicorn main:app --app-dir src --port 8080 --reload
```

### 2. Executar Testes

```bash
# Teste rápido (3 operações - uma de cada agente)
.venv/bin/python test_quick_agents.py

# Teste completo (demonstra todas as operações)
.venv/bin/python test_all_agents_logs.py
```

### 3. Ver Logs no Dashboard

Acesse: **http://localhost:8080/dashboard**

Os logs aparecerão automaticamente na seção inferior, agrupados por agente.

### 4. Consultar Logs via API

```bash
# Ver todos os logs
curl "http://localhost:8080/v1/logs/?limit=50" | jq

# Ver logs de um agente específico
curl -s "http://localhost:8080/v1/logs/?limit=50" | \
  jq '.logs[] | select(.context.agent == "ATLAS")'

# Contar logs por agente
curl -s "http://localhost:8080/v1/logs/?limit=100" | \
  jq -r '.logs[].context.agent' | sort | uniq -c

# Ver apenas alertas (WARNING)
curl "http://localhost:8080/v1/logs/?level=warning" | jq
```

## 📊 Exemplos de Logs Gerados

### ATLAS
```json
{
  "event": "atlas_context_retrieved",
  "level": "info",
  "agent": "ATLAS",
  "asset_id": "CHILLER-04",
  "temperature": 74.3,
  "vibration": 3.8,
  "status": "operational"
}
```

### SENTINEL
```json
{
  "event": "sentinel_alert_generated",
  "level": "warning",
  "agent": "SENTINEL",
  "alert_id": "alert_a3f8c2d1",
  "severity": "HIGH",
  "metric": "temperature",
  "value": 95.0,
  "threshold": 90.0,
  "exceeded_by": 5.0
}
```

### ORACLE
```json
{
  "event": "oracle_prediction_completed",
  "level": "info",
  "agent": "ORACLE",
  "prediction_id": "pred_f4a1b8c3",
  "failure_probability": 0.8542,
  "confidence": 0.9123,
  "risk_level": "CRITICAL"
}
```

## 🎮 Fluxo Completo de Teste

```bash
# 1. Consultar contexto (ATLAS)
curl "http://localhost:9000/atlas/v1/tenants/tenant_local/assets/CHILLER-04/context"

# 2. Gerar alerta (SENTINEL)
curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/chiller_overtemp"

# 3. Fazer predição (ORACLE)
curl -X POST "http://localhost:9000/oracle/v1/predict" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "CHILLER-04", "metric": "failure_probability"}'

# 4. Ver logs capturados
curl "http://localhost:8080/v1/logs/?limit=20"
```

## 📁 Arquivos Criados/Modificados

### Criados
- ✅ `SIMULATORS_LOGS.md` - Documentação completa
- ✅ `test_all_agents_logs.py` - Teste completo
- ✅ `test_quick_agents.py` - Teste rápido
- ✅ `start_simulators.sh` - Script para iniciar simuladores
- ✅ `simulators/remote_logging.py` - Cliente de logs remotos
- ✅ `LOGS_AGENTS_SUMMARY.md` - Este arquivo

### Modificados
- ✅ `simulators/mock_agents.py` - Adicionados logs em todos os endpoints
- ✅ `src/caos/observability/logs.py` - Adicionado endpoint `/v1/logs/external`

## 🔍 Tipos de Logs por Agente

| Agente | Eventos | Nível | Quando |
|--------|---------|-------|--------|
| ATLAS | `atlas_context_requested` | info | Ao solicitar contexto |
| ATLAS | `atlas_context_retrieved` | info | Ao retornar contexto |
| ATLAS | `atlas_manual_search` | info | Ao buscar em manuais |
| ATLAS | `atlas_manual_results` | info | Ao retornarresultados |
| ATLAS | `atlas_contract_requested` | info | Ao solicitar contrato |
| ATLAS | `atlas_contract_retrieved` | info | Ao retornar contrato |
| SENTINEL | `sentinel_alert_requested` | info | Ao solicitar alerta |
| SENTINEL | `sentinel_alert_generated` | **warning** | Ao gerar alerta |
| SENTINEL | `sentinel_scenario_triggered` | info | Ao disparar cenário |
| SENTINEL | `sentinel_scenario_not_found` | warning | Cenário inexistente |
| ORACLE | `oracle_prediction_requested` | info | Ao solicitar predição |
| ORACLE | `oracle_prediction_completed` | info | Ao completar predição |
| ORACLE | `oracle_simulation_requested` | info | Ao solicitar simulação |
| ORACLE | `oracle_simulation_completed` | info | Ao completar simulação |

## 💡 Dicas

1. **Monitoramento Contínuo**: Use o dashboard para ver logs em tempo real (refresh automático a cada 3s)

2. **Filtrar por Agente**: No console, filtre logs de agente específico:
   ```bash
   curl -s "http://localhost:8080/v1/logs/" | jq '.logs[] | select(.context.agent == "SENTINEL")'
   ```

3. **Ver Alertas Críticos**: Filtre apenas logs de WARNING:
   ```bash
   curl "http://localhost:8080/v1/logs/?level=warning"
   ```

4. **Limpar Logs**: Para testes, limpe os logs:
   ```bash
   curl -X DELETE "http://localhost:8080/v1/logs/"
   ```

5. **Loop de Testes**: Gere logs continuamente:
   ```bash
   while true; do
     .venv/bin/python test_quick_agents.py
     sleep 10
   done
   ```

## 🐛 Troubleshooting

### Logs não aparecem no dashboard

1. Verifique se ambos os serviços estão rodando:
   ```bash
   curl http://localhost:9000/health  # Simuladores
   curl http://localhost:8080/health  # CAOS
   ```

2. Teste o endpoint de logs externos:
   ```bash
   curl -X POST "http://localhost:8080/v1/logs/external" \
     -H "Content-Type: application/json" \
     -d '{"event": "test", "level": "info", "agent": "TEST"}'
   ```

3. Verifique se já há logs:
   ```bash
   curl "http://localhost:8080/v1/logs/?limit=5"
   ```

### Simuladores não iniciam

```bash
# Matar processo na porta 9000
lsof -ti:9000 | xargs kill -9

# Reinstalar dependências
source .venv/bin/activate
pip install httpx structlog fastapi uvicorn
```

## 📚 Documentação Adicional

- **SIMULATORS_LOGS.md** - Guia completo de uso dos simuladores
- **FRONTEND_INTEGRATION.md** - Integração frontend-backend
- **TEST_RESULTS.md** - Resultados de validação

---

**Status**: ✅ Implementado e funcionando
**Última atualização**: Fevereiro 2026
**Versão**: 1.0.0
