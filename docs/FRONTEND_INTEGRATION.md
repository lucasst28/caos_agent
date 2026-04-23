# Integração Frontend + Backend CAOS

## ✅ Implementação Concluída

A integração entre o frontend (dashboard) e o backend (API CAOS) foi implementada com sucesso. Agora você pode visualizar todos os logs do sistema em tempo real através do dashboard.

## 🎯 O que foi Implementado

### 1. **Sistema de Captura de Logs** (`src/caos/observability/logs.py`)
- Novo módulo para capturar logs estruturados do sistema
- Coletor em memória thread-safe com buffer circular (até 1000 logs)
- Processador customizado do structlog para capturar automaticamente todos os logs

### 2. **Endpoint de API para Logs** (`/v1/logs/`)
- `GET /v1/logs/` - Retorna logs do sistema com paginação e filtros
- `DELETE /v1/logs/` - Limpa logs (útil para testes)
- Parâmetros:
  - `level`: Filtrar por nível (info, warning, error, debug)
  - `limit`: Número máximo de logs (1-1000, padrão: 100)
  - `offset`: Offset para paginação

### 3. **Frontend Atualizado** (`frontend/assets/js/caos-dashboard.js`)
- Função `fetchLogs()` atualizada para buscar logs do endpoint `/v1/logs/`
- Mapeamento automático de níveis de log para tipos do frontend
- Detecção automática de agentes (atlas, sentinel, oracle, care, caos) baseado no contexto
- Logs são automaticamente adicionados aos painéis do dashboard

### 4. **Servidor de Arquivos Estáticos** (`src/main.py`)
- Dashboard disponível em: `http://localhost:8080/dashboard`
- Arquivos estáticos servidos via FastAPI
- CORS configurado para permitir acesso local

## 🚀 Como Usar

### Iniciar o Servidor

```bash
# Ativar ambiente virtual
source .venv/bin/activate

# Iniciar servidor na porta 8080
uvicorn main:app --app-dir src --port 8080 --reload
```

### Acessar o Dashboard

Abra no navegador:
```
http://localhost:8080/dashboard
```

O dashboard irá:
1. Conectar-se automaticamente à API em `http://localhost:8080`
2. Buscar logs a cada 3 segundos (configurável)
3. Exibir logs em tempo real na seção "Logs"
4. Mostrar métricas, eventos e traces do sistema

### Testar a API Diretamente

```bash
# Health check
curl http://localhost:8080/health

# Buscar logs
curl "http://localhost:8080/v1/logs/?limit=10"

# Filtrar logs por nível
curl "http://localhost:8080/v1/logs/?level=error&limit=20"

# Criar evento de teste (gera logs)
curl -X POST "http://localhost:8080/v1/events/trigger" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "viva",
    "asset_id": "CHILLER-01",
    "severity": "HIGH",
    "metric": "temperature",
    "value": 95.5,
    "location": "Sala de Servidores",
    "payload": {
      "description": "Temperatura crítica",
      "threshold": 85.0
    }
  }'

# Verificar logs gerados
curl "http://localhost:8080/v1/logs/?limit=20"

# Limpar logs
curl -X DELETE "http://localhost:8080/v1/logs/"
```

## 📊 Estrutura de Logs

Cada log contém:
```json
{
  "timestamp": "2026-02-11T15:30:00.123456Z",
  "level": "info",
  "logger_name": "caos.core.brain",
  "event": "trigger_processing_started",
  "message": "trigger_processing_started | event_id=evt_abc123",
  "context": {
    "event_id": "evt_abc123",
    "tenant_id": "viva",
    "asset_id": "CHILLER-01"
  }
}
```

## 🎨 Visualização no Dashboard

Os logs são exibidos em dois locais:

1. **Painel Resumido** (Overview)
   - Últimos 30 logs
   - Filtros por tipo (info, warning, error, success)
   - Filtros por agente (atlas, sentinel, caos, oracle, care)

2. **Seção Completa** (Menu "Logs")
   - Todos os logs (até 200)
   - Busca por texto
   - Filtros por nível
   - Scroll automático para novos logs

## 🔧 Configuração

### Alterar URL da API (no navegador)

Abra o console do navegador e execute:
```javascript
// Alterar URL da API
localStorage.setItem('caos_api_url', 'http://seu-servidor:porta');

// Alterar intervalo de polling (em ms)
localStorage.setItem('caos_poll_interval', '5000');

// Recarregar página
location.reload();
```

### Alterar Tamanho do Buffer de Logs

Em `src/caos/observability/logs.py`:
```python
# Alterar máximo de logs em memória (padrão: 1000)
_log_collector = LogCollector(max_size=5000)
```

## 🏗️ Arquitetura

```
┌─────────────────┐
│   Frontend      │
│  (Dashboard)    │
│  Port: 8080     │
└────────┬────────┘
         │ HTTP GET/POST
         │
┌────────▼────────────────────────┐
│   FastAPI Backend               │
│   - /health                     │
│   - /v1/logs/                   │
│   - /v1/events/trigger          │
│   - /v1/metrics                 │
│   - /v1/traces                  │
│   - /dashboard (static files)   │
└────────┬────────────────────────┘
         │
┌────────▼────────────────────────┐
│   Structlog + LogCollector      │
│   - Captura todos os logs       │
│   - Buffer circular em memória  │
│   - Thread-safe                 │
└─────────────────────────────────┘
```

## 📝 Notas

- **Persistência**: Os logs são armazenados apenas em memória. Ao reiniciar o servidor, os logs são perdidos.
- **Produção**: Para ambientes de produção, considere usar um sistema de agregação de logs como Cloud Logging, ELK Stack ou Grafana Loki.
- **Performance**: O buffer circular limita automaticamente o tamanho em memória. Logs antigos são descartados quando o limite é atingido.
- **CORS**: Configurado para aceitar requisições de `localhost:3000` e `localhost:8080`.

## 🐛 Troubleshooting

### Dashboard não carrega logs
1. Verifique se o servidor está rodando: `lsof -i :8080`
2. Teste o endpoint: `curl "http://localhost:8080/v1/logs/?limit=5"`
3. Abra o console do navegador (F12) e veja erros de rede

### Logs não aparecem
1. Gere um evento de teste para produzir logs
2. Verifique se o structlog está configurado corretamente
3. Confirme que `LogCapturingProcessor` está na cadeia de processadores

### Erro CORS
1. Verifique se a URL no frontend está correta
2. Confirme que o CORS está habilitado no backend
3. Use o dashboard servido pelo próprio backend em `/dashboard`

## 🎉 Sucesso!

Agora você tem uma integração completa entre frontend e backend, com visualização em tempo real de todos os logs do sistema CAOS!
