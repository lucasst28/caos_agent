# Simuladores dos Agentes - Guia de Logs

## 📋 Visão Geral

Os simuladores dos agentes externos (ATLAS, SENTINEL, ORACLE) agora possuem logs estruturados usando `structlog`, permitindo rastreamento completo de todas as operações no dashboard CAOS.

## 🎯 Agentes Simulados

### 1. **ATLAS** - Digital Twin Agent
Simula o gêmeo digital dos ativos industriais.

**Endpoints:**
- `GET /atlas/v1/tenants/{tenant_id}/assets/{asset_id}/context` - Contexto do ativo
- `GET /atlas/v1/tenants/{tenant_id}/assets/{asset_id}/manuals` - Busca em manuais
- `GET /atlas/v1/tenants/{tenant_id}/assets/{asset_id}/contracts` - Dados de contrato

**Logs Gerados:**
- `atlas_context_requested` - Quando contexto é solicitado
- `atlas_context_retrieved` - Quando contexto é retornado (com temperatura, vibração, status)
- `atlas_manual_search` - Quando busca em manuais é feita
- `atlas_manual_results` - Resultados da busca em manuais
- `atlas_contract_requested` - Quando contrato é solicitado
- `atlas_contract_retrieved` - Dados do contrato retornados

**Exemplo de Log:**
```json
{
  "event": "atlas_context_retrieved",
  "level": "info",
  "agent": "ATLAS",
  "asset_id": "CHILLER-04",
  "temperature": 74.3,
  "vibration": 3.8,
  "status": "operational",
  "contract_tier": "PREMIUM"
}
```

### 2. **SENTINEL** - Alert Agent
Simula o sistema de alertas e detecção de anomalias.

**Endpoints:**
- `POST /sentinel/v1/alerts/generate` - Gera alerta customizado
- `GET /sentinel/v1/alerts/scenarios` - Lista cenários pré-configurados
- `POST /sentinel/v1/alerts/scenario/{scenario_name}` - Dispara cenário

**Logs Gerados:**
- `sentinel_alert_requested` - Quando alerta é solicitado
- `sentinel_alert_generated` - Quando alerta é gerado (WARNING level)
- `sentinel_scenario_triggered` - Quando cenário é disparado
- `sentinel_scenario_not_found` - Quando cenário não existe

**Exemplo de Log:**
```json
{
  "event": "sentinel_alert_generated",
  "level": "warning",
  "agent": "SENTINEL",
  "alert_id": "alert_a3f8c2d1",
  "asset_id": "CHILLER-04",
  "severity": "HIGH",
  "metric": "temperature",
  "value": 95.0,
  "threshold": 90.0,
  "exceeded_by": 5.0
}
```

**Cenários Disponíveis:**
- `chiller_overtemp` - Chiller com temperatura alta (HIGH)
- `chiller_critical` - Chiller em temperatura crítica (CRITICAL)
- `pump_pressure` - Bomba com pressão alta (MEDIUM)
- `genset_overtemp` - Gerador sobreaquecendo (HIGH)
- `chiller_vibration` - Vibração excessiva (MEDIUM)
- `chiller_normal` - Operação normal (LOW)

### 3. **ORACLE** - Prediction Agent
Simula predições e análises preditivas.

**Endpoints:**
- `POST /oracle/v1/predict` - Faz predição de falha
- `POST /oracle/v1/simulate` - Simula cenários what-if

**Logs Gerados:**
- `oracle_prediction_requested` - Quando predição é solicitada
- `oracle_prediction_completed` - Predição completada com resultados
- `oracle_simulation_requested` - Quando simulação é solicitada
- `oracle_simulation_completed` - Simulação completada

**Exemplo de Log:**
```json
{
  "event": "oracle_prediction_completed",
  "level": "info",
  "agent": "ORACLE",
  "prediction_id": "pred_f4a1b8c3",
  "asset_id": "CHILLER-04",
  "failure_probability": 0.8542,
  "confidence": 0.9123,
  "risk_level": "CRITICAL",
  "financial_impact": 12450.00,
  "recommended_actions": ["shutdown", "notification", "ticket"]
}
```

## 🚀 Como Usar

### 1. Iniciar os Simuladores

```bash
# Opção 1: Script automático
./start_simulators.sh

# Opção 2: Comando direto
uvicorn simulators.mock_agents:app --port 9000 --reload
```

Acesse a documentação interativa: http://localhost:9000/docs

### 2. Iniciar o CAOS

Em outro terminal:

```bash
./dev.sh
# ou
uvicorn main:app --app-dir src --port 8080 --reload
```

### 3. Executar Teste Completo

```bash
# Testa todos os agentes e mostra logs
.venv/bin/python test_all_agents_logs.py
```

Este teste irá:
- ✅ Consultar contexto de 3 ativos diferentes no ATLAS
- ✅ Buscar em manuais técnicos
- ✅ Consultar contratos
- ✅ Gerar 4 alertas diferentes no SENTINEL
- ✅ Criar alertas customizados
- ✅ Fazer predições no ORACLE para múltiplos ativos
- ✅ Executar simulações what-if
- ✅ Verificar logs capturados no CAOS
- ✅ Mostrar estatísticas por agente

## 📊 Visualizando os Logs

### No Dashboard

Acesse: http://localhost:8080/dashboard

Os logs aparecerão automaticamente na seção de logs, agrupados por agente.

### Via API

```bash
# Ver todos os logs
curl "http://localhost:8080/v1/logs/?limit=50" | jq

# Ver logs de um agente específico
curl "http://localhost:8080/v1/logs/?limit=50" | jq '.logs[] | select(.context.agent == "ATLAS")'

# Ver logs por nível
curl "http://localhost:8080/v1/logs/?level=warning" | jq
```

### Exemplo de Análise

```bash
# Contar logs por agente
curl -s "http://localhost:8080/v1/logs/?limit=100" | \
  jq -r '.logs[].context.agent' | sort | uniq -c

# Ver últimos alertas do SENTINEL
curl -s "http://localhost:8080/v1/logs/" | \
  jq '.logs[] | select(.context.agent == "SENTINEL") | 
      {timestamp, event, asset: .context.asset_id, severity: .context.severity}'

# Ver predições do ORACLE
curl -s "http://localhost:8080/v1/logs/" | \
  jq '.logs[] | select(.context.agent == "ORACLE") | 
      {timestamp, event, asset: .context.asset_id, risk: .context.failure_probability}'
```

## 🎮 Exemplos de Uso

### Simular Alerta de Temperatura Alta

```bash
curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/chiller_overtemp"
```

Logs gerados:
- `sentinel_scenario_triggered` (SENTINEL)
- `sentinel_alert_generated` (SENTINEL, WARNING)

### Consultar Contexto e Fazer Predição

```bash
# 1. Consultar contexto
curl "http://localhost:9000/atlas/v1/tenants/tenant_local/assets/CHILLER-04/context"

# 2. Fazer predição baseada no contexto
curl -X POST "http://localhost:9000/oracle/v1/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "asset_id": "CHILLER-04",
    "metric": "failure_probability",
    "horizon_hours": 24,
    "current_value": 95.0
  }'
```

Logs gerados:
- `atlas_context_requested` (ATLAS)
- `atlas_context_retrieved` (ATLAS)
- `oracle_prediction_requested` (ORACLE)
- `oracle_prediction_completed` (ORACLE)

### Fluxo Completo de Investigação

```bash
# 1. Alerta crítico
curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/chiller_critical"

# 2. Consultar manual
curl "http://localhost:9000/atlas/v1/tenants/tenant_local/assets/CHILLER-04/manuals?query=temperatura"

# 3. Verificar contrato (ações permitidas)
curl "http://localhost:9000/atlas/v1/tenants/tenant_local/assets/CHILLER-04/contracts"

# 4. Simular cenários de ação
curl -X POST "http://localhost:9000/oracle/v1/simulate" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "CHILLER-04"}'

# 5. Ver todos os logs gerados
curl "http://localhost:8080/v1/logs/?limit=20"
```

## 📈 Monitoramento Contínuo

Para gerar logs continuamente, você pode usar um loop:

```bash
# Loop de alertas (Ctrl+C para parar)
while true; do
  curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/chiller_overtemp"
  sleep 5
  curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/pump_pressure"
  sleep 5
  curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/genset_overtemp"
  sleep 5
done
```

## 🔍 Logs de Inicialização

Quando os simuladores iniciam, um log especial é gerado:

```json
{
  "event": "simulators_starting",
  "level": "info",
  "service": "mock_agents",
  "agents": ["ATLAS", "SENTINEL", "ORACLE"],
  "assets": ["CHILLER-04", "PUMP-01", "GENSET-02"],
  "version": "0.1.0-sim"
}
```

## 🐛 Troubleshooting

### Logs não aparecem no dashboard

1. Certifique-se de que ambos os serviços estão rodando:
   ```bash
   # Terminal 1: Simuladores
   ./start_simulators.sh
   
   # Terminal 2: CAOS
   ./dev.sh
   ```

2. Verifique se o structlog está configurado no CAOS:
   ```python
   # src/main.py deve ter LogCapturingProcessor
   ```

3. Teste a conexão:
   ```bash
   curl http://localhost:9000/health
   curl http://localhost:8080/health
   ```

### Simuladores não iniciam

```bash
# Verificar se a porta está em uso
lsof -ti:9000 | xargs kill -9

# Reinstalar dependências
source .venv/bin/activate
pip install -r requirements.txt
```

## 📚 Recursos

- **Documentação Interativa**: http://localhost:9000/docs
- **Dashboard CAOS**: http://localhost:8080/dashboard
- **API de Logs**: http://localhost:8080/v1/logs/
- **Health Check Simuladores**: http://localhost:9000/health
- **Health Check CAOS**: http://localhost:8080/health

---

**Última atualização**: Fevereiro 2026  
**Versão**: 0.1.0-sim
