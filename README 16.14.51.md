# CAOS - Cognitive Anomaly Observation System

Sistema de supervisão e validação para o Ecossistema Viva AI.

## 📋 Visão Geral

O CAOS atua como um **filtro inteligente** entre a ingestão de dados e o processamento, garantindo que apenas dados válidos cheguem aos consumidores.

### Funcionalidades (Versão Simplificada para Atlas)

| Feature | Descrição |
|---------|-----------|
| 🛡️ **Rate Limiting** | Token Bucket - 100 req/min por client |
| ✅ **Validação** | Schema validation (asset_id, client obrigatórios) |
| ⚡ **Circuit Breaker** | Proteção contra falhas em cascata |
| 📝 **Auditoria** | Logs estruturados de todas as decisões |

> **Nota**: Esta versão não inclui ML (IsolationForest) nem LLM (GPT-4o) — não são necessários para o Atlas.

---

## 🏗️ Arquitetura

### Fluxo de Dados

```
┌─────────────────────────────────────────────────────────────────┐
│                    fila_viva (shared-redis)                      │
│                         Redis :6379                              │
│                                                                  │
│   telemetry.received → telemetry.validated → telemetry.rejected  │
└─────────────────────────────────────────────────────────────────┘
         ▲                      │                       │
         │                      ▼                       ▼
    ┌─────────┐          ┌─────────────┐         ┌───────────┐
    │  Atlas  │          │    CAOS     │         │ (auditoria│
    │ publica │          │   Filter    │         │ rejeitados)│
    └─────────┘          └─────────────┘         └───────────┘
                               │
                               ▼
                        ┌─────────────┐
                        │  Sentinel   │
                        │  Consumer   │
                        └─────────────┘
                               │
                               ▼
                        ┌─────────────┐
                        │ Sentinel DB │
                        └─────────────┘
```

### Streams Redis

| Stream | Descrição |
|--------|-----------|
| `telemetry.received` | Dados brutos publicados pelo Atlas |
| `telemetry.validated` | Dados aprovados pelo CAOS (vão para Sentinel) |
| `telemetry.rejected` | Dados bloqueados (para auditoria) |
| `atlas.daily_routine` | Eventos da rotina diária do Atlas (scraping/import) |
| `atlas.db_sync` | Eventos de sincronização Atlas DB → Redis |
| `sentinel.consumer_health` | Métricas de saúde dos consumers |
| `caos.alerts` | Alertas publicados pelo CAOS |

### Estrutura do Projeto

```
chaos_agent/
├── services/
│   └── atlas_supervisor/
│       ├── app_simple.py              # API FastAPI (porta 8001)
│       ├── supervisor_simple.py       # Lógica de supervisão
│       ├── stream_consumer.py         # Consumer Redis Streams
│       ├── routine_monitor.py         # Monitor da rotina diária
│       ├── sync_monitor.py            # Monitor de sincronização DB→Redis
│       ├── consumer_health_monitor.py # Monitor de saúde dos consumers
│       ├── rules.py                   # Regras de validação
│       └── Dockerfile
├── shared/
│   ├── schemas.py                # Modelos Pydantic
│   └── utils.py
├── docker-compose.yml
└── requirements_simple.txt
```

---

## 🐳 Containers

| Container | Porta | Descrição |
|-----------|-------|-----------|
| `caos-atlas-supervisor` | 8001 | API de auditoria |
| `caos-stream-filter` | - | Consumer que filtra telemetria |
| `caos-routine-monitor` | - | Monitor da rotina diária + sincronização |
| `caos-consumer-monitor` | - | Monitor de saúde dos consumers |

> **Dependência**: Requer `fila_viva` (shared-redis) rodando.
>
> **Nota**: Como os dados do Atlas são atualizados apenas **1x por dia** (às 07:00), o monitor de rotina verifica a execução da rotina diária E a sincronização DB→Redis como parte do mesmo fluxo.

---

## 🚀 Quick Start

### 1. Subir o Redis Compartilhado (fila_viva)

```bash
cd fila_viva && docker compose up -d
```

### 2. Subir o CAOS

```bash
cd chaos_agent && docker compose up -d
```

### 3. Subir o Sentinel (opcional, para testes end-to-end)

```bash
cd sentinel_agent && docker compose up -d
```

### Verificar Status

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "redis|caos|sentinel"
```

---

## 🧪 Testando

### Publicar Telemetria Válida

```bash
docker exec shared-redis redis-cli XADD telemetry.received '*' payload \
  '{"health_events_id":1,"health_events_client":"coca-cola","health_events_asset_serial_number":"COOLER-001","health_events_temperature_c":4.5}'
```

### Publicar Telemetria Inválida (sem asset)

```bash
docker exec shared-redis redis-cli XADD telemetry.received '*' payload \
  '{"health_events_id":2,"health_events_client":"pepsi"}'
```

### Verificar Streams

```bash
# Quantidade de mensagens validadas
docker exec shared-redis redis-cli XLEN telemetry.validated

# Quantidade de mensagens rejeitadas
docker exec shared-redis redis-cli XLEN telemetry.rejected
```

### Verificar Logs

```bash
# CAOS Filter
docker logs caos-stream-filter --tail 20

# Sentinel Consumer
docker logs sentinel-consumer --tail 20
```

---

## 🔌 API Endpoints

### Health Check

```bash
curl http://localhost:8001/health
```

### Auditar Request (API)

```bash
curl -X POST http://localhost:8001/audit/request \
  -H "Content-Type: application/json" \
  -d '{"path":"/assets","method":"GET","client":"coca-cola"}'
```

### Ver Regras Ativas

```bash
curl http://localhost:8001/rules
```

### Ver Status dos Circuit Breakers

```bash
curl http://localhost:8001/circuit-breakers
```

---

## ⚙️ Variáveis de Ambiente

| Variável | Default | Descrição |
|----------|---------|-----------|
| `REDIS_URL` | `redis://shared-redis:6379/0` | URL do Redis |
| `CAOS_INPUT_STREAM` | `telemetry.received` | Stream de entrada |
| `CAOS_OUTPUT_STREAM` | `telemetry.validated` | Stream de saída (aprovados) |
| `CAOS_REJECTED_STREAM` | `telemetry.rejected` | Stream de rejeitados |
| `LOG_LEVEL` | `INFO` | Nível de log |

---

## 📊 Regras de Validação

### Campos Obrigatórios

- `health_events_asset_serial_number` (ou `assetId`)
- `health_events_client` (ou `tenantId`)

### Rate Limits

| Contexto | Limite |
|----------|--------|
| API por client | 100 req/min |
| Telemetria por asset | 60 eventos/min |

### Motivos de Bloqueio

| Status | Reason |
|--------|--------|
| `BLOCKED` | `asset_serial_number é obrigatório` |
| `BLOCKED` | `client é obrigatório` |
| `BLOCKED` | `Rate limit excedido para {client}` |
| `BLOCKED` | `Circuit breaker aberto para {service}` |

---

## � Monitor de Rotina Diária

O CAOS monitora a execução da rotina diária do Atlas (`daily_routine.sh`) que roda às 07:00 BRT.

### Como Funciona

1. **Atlas** publica eventos no stream `atlas.daily_routine`:
   - `daily_routine:STARTED` - Rotina iniciou
   - `scraping:STARTED/COMPLETED/FAILED` - Etapa de scraping
   - `import:STARTED/COMPLETED/FAILED` - Etapa de importação
   - `daily_routine:COMPLETED/FAILED` - Resultado final

2. **CAOS Monitor** consome esses eventos e:
   - Verifica se a rotina foi executada até o deadline (08:00 BRT)
   - Publica alertas no stream `caos.alerts`

### Alertas Gerados

| Severidade | Tipo | Condição |
|------------|------|----------|
| `CRITICAL` | `daily_routine_missed` | Rotina não executou até o deadline |
| `HIGH` | `daily_routine_failed` | Rotina executou mas falhou |
| `MEDIUM` | `daily_routine_stuck` | Rotina iniciou mas não terminou |
| `INFO` | `daily_routine_completed` | Rotina concluída com sucesso |

### Variáveis de Ambiente do Monitor

| Variável | Default | Descrição |
|----------|---------|-----------|
| `CAOS_ROUTINE_STREAM` | `atlas.daily_routine` | Stream de eventos |
| `CAOS_ALERTS_STREAM` | `caos.alerts` | Stream de alertas |
| `EXPECTED_START_HOUR_UTC` | `10` | Horário esperado (UTC) |
| `DEADLINE_HOUR_UTC` | `11` | Deadline para execução (UTC) |
| `CHECK_INTERVAL_SEC` | `300` | Intervalo de verificação (5 min) |

### Testar o Monitor

```bash
# Simular início da rotina
docker exec shared-redis redis-cli XADD atlas.daily_routine '*' \
    event_type daily_routine status STARTED message "Rotina iniciada" timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Simular conclusão da rotina
docker exec shared-redis redis-cli XADD atlas.daily_routine '*' \
    event_type daily_routine status COMPLETED message "Rotina concluída" timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Ver alertas gerados
docker exec shared-redis redis-cli XRANGE caos.alerts - + COUNT 5
```

---

## 🔄 Monitor de Sincronização DB → Redis

O CAOS monitora a sincronização do banco de dados do Atlas para o Redis (executada pelo `atlas_publisher.py` do Sentinel).

### Como Funciona

1. **Sentinel** (atlas_publisher.py) publica eventos no stream `atlas.db_sync`:
   - `db_sync:STARTED` - Sincronização iniciou
   - `db_sync:COMPLETED` - Sincronização concluída (inclui contagem de registros)
   - `db_sync:FAILED` - Sincronização falhou

2. **CAOS Monitor** consome esses eventos e:
   - Monitora se a sincronização está ocorrendo regularmente
   - Alerta se não ocorrer sincronização em X minutos
   - Alerta em caso de falha

### Alertas Gerados

| Severidade | Tipo | Condição |
|------------|------|----------|
| `HIGH` | `db_sync_failed` | Sincronização falhou |
| `MEDIUM` | `db_sync_stale` | Sem sincronização há mais de 15 min |

### Variáveis de Ambiente

| Variável | Default | Descrição |
|----------|---------|-----------|
| `CAOS_SYNC_STREAM` | `atlas.db_sync` | Stream de eventos |
| `MAX_SYNC_INTERVAL_MIN` | `15` | Tempo máximo sem sync (min) |
| `CHECK_INTERVAL_SEC` | `60` | Intervalo de verificação |

### Testar o Monitor

```bash
# Simular sincronização bem-sucedida
docker exec shared-redis redis-cli XADD atlas.db_sync '*' \
    event_type db_sync status COMPLETED \
    message "Sincronização concluída" \
    details '{"health_events_sent": 100, "door_sent": 20}'

# Simular falha
docker exec shared-redis redis-cli XADD atlas.db_sync '*' \
    event_type db_sync status FAILED \
    message "Connection refused to Atlas DB"

# Ver alertas
docker exec shared-redis redis-cli XRANGE caos.alerts - + COUNT 10
```

---

## � Monitor de Saúde dos Consumers

O CAOS monitora a saúde dos consumers que processam telemetria (ex: `sentinel-consumer`).

### Como Funciona

1. **Sentinel Consumer** publica métricas a cada 30s no stream `sentinel.consumer_health`:
   - Contagem de mensagens processadas/sucesso/erro/skipped
   - Uptime do consumer
   - Último erro (se houver)

2. **CAOS Monitor** consome essas métricas e:
   - Monitora taxa de erro (alerta se > 5%)
   - Detecta consumers inativos (sem health > 2 min)
   - Monitora mensagens pendentes nos streams

### Alertas Gerados

| Severidade | Tipo | Condição |
|------------|------|----------|
| `CRITICAL` | `consumer_inactive` | Consumer sem enviar health há mais de 2 min |
| `HIGH` | `consumer_high_error_rate` | Taxa de erro > 5% |
| `MEDIUM` | `stream_high_pending` | Mais de 100 mensagens pendentes |

### Variáveis de Ambiente

| Variável | Default | Descrição |
|----------|---------|-----------|
| `CAOS_CONSUMER_HEALTH_STREAM` | `sentinel.consumer_health` | Stream de métricas |
| `MAX_ERROR_RATE_PERCENT` | `5` | Taxa máxima de erro |
| `MAX_INACTIVE_SECONDS` | `120` | Tempo máximo sem health |

---

## �🔃 Ordem de Inicialização

```bash
# 1. Redis compartilhado (OBRIGATÓRIO primeiro)
cd fila_viva && docker compose up -d

# 2. CAOS (depende do Redis)
cd chaos_agent && docker compose up -d

# 3. Sentinel (depende do Redis)
cd sentinel_agent && docker compose up -d

# 4. Atlas (opcional, publica telemetria)
cd atlas_agent && docker compose up -d
```

---

## 📈 Monitoramento

### Métricas do Supervisor

```bash
curl http://localhost:8001/health | python3 -m json.tool
```

Retorna:
```json
{
  "status": "ok",
  "version": "1.0.0-simple",
  "uptime_seconds": 3600,
  "features": {
    "rate_limiting": true,
    "validation": true,
    "circuit_breaker": true,
    "anomaly_detection": false,
    "llm_judge": false
  }
}
```

---

## 🛠️ Desenvolvimento Local

### Sem Docker

```bash
# Instalar dependências
pip install -r requirements_simple.txt

# Rodar API
REDIS_URL=redis://localhost:6379/0 uvicorn services.atlas_supervisor.app_simple:app --port 8001

# Rodar Consumer (outro terminal)
REDIS_URL=redis://localhost:6379/0 python services/atlas_supervisor/stream_consumer.py
```

### Rebuild após mudanças

```bash
docker compose up -d --build
```

---

## 📝 Licença

Projeto interno Viva AI.
