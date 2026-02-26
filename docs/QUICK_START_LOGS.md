# 🚀 GUIA RÁPIDO - Logs dos Agentes

## ✅ O que foi implementado

Adicionados **logs estruturados** para os 3 agentes simulados:
- **ATLAS** - Digital Twin (contexto, manuais, contratos)
- **SENTINEL** - Alertas (geração, cenários, severidades)
- **ORACLE** - Predições (falhas, simulações, riscos)

Os logs são enviados automaticamente ao CAOS via HTTP e aparecem no dashboard.

## 🎯 Como Testar (Passo a Passo)

### IMPORTANTE: Reiniciar Simuladores

Os simuladores que estão rodando precisam ser reiniciados para carregar o novo código com envio de logs.

```bash
# 1. Nos terminais onde os simuladores estãorodando, pressione Ctrl+C

# 2. Inicie novamente:
./start_simulators.sh
```

### Teste Rápido

```bash
# Limpar logs antigos
curl -X DELETE "http://localhost:8080/v1/logs/"

# Executar teste
.venv/bin/python test_quick_agents.py

# Ver logs capturados (aguarde 2 segundos)
sleep 2
curl "http://localhost:8080/v1/logs/?limit=20" | jq '.total, .logs[].context.agent'
```

### Teste Completo Automatizado

```bash
./test_integration_final.sh
```

Este script:
- ✅ Verifica se CAOS e Simuladores estão rodando
- ✅ Limpa logs antigos
- ✅ Executa testes dos 3 agentes
- ✅ Mostra estatísticas dos logs capturados
- ✅ Lista últimos eventos por agente

## 📊 Resultado Esperado

Após executar os testes, você verá:

```
📊 Total de logs: 9+ logs

📈 Logs por agente:
   • ATLAS       :  3 logs
   • ORACLE      :  2 logs
   • SENTINEL    :  2 logs

📝 Últimos eventos:
   [16:30:45] ORACLE: oracle_prediction_completed
   [16:30:45] ORACLE: oracle_prediction_requested
   [16:30:44] SENTINEL: sentinel_alert_generated
   [16:30:44] SENTINEL: sentinel_alert_requested
   [16:30:43] ATLAS: atlas_context_retrieved
```

## 🎮 Exemplos Práticos

### Gerar alerta do SENTINEL

```bash
curl -X POST "http://localhost:9000/sentinel/v1/alerts/scenario/chiller_overtemp"

# Ver log gerado (aguarde 1s)
sleep 1
curl -s "http://localhost:8080/v1/logs/?limit=5" | \
  jq '.logs[] | select(.context.agent == "SENTINEL")'
```

### Consultar ATLAS e ver logs

```bash
curl "http://localhost:9000/atlas/v1/tenants/tenant_local/assets/CHILLER-04/context"

# Ver logs do ATLAS
sleep 1
curl -s "http://localhost:8080/v1/logs/" | \
  jq '.logs[] | select(.context.agent == "ATLAS") | {event, asset: .context.asset_id}'
```

### Fazer predição no ORACLE

```bash
curl -X POST "http://localhost:9000/oracle/v1/predict" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "CHILLER-04", "metric": "failure_probability"}'

# Ver logs do ORACLE
sleep 1
curl -s "http://localhost:8080/v1/logs/" | \
  jq '.logs[] | select(.context.agent == "ORACLE") | {event, risk: .context.failure_probability}'
```

## 📈 Visualização no Dashboard

Acesse: **http://localhost:8080/dashboard**

Os logs aparecerão automaticamente na seção de logs, com:
- 🔷 Ícone do agente (ATLAS/SENTINEL/ORACLE/CAOS)
- ⏱️ Timestamp
- 📝 Evento
- 🎯 Contexto (asset_id, valores, etc)

**O dashboard atualiza automaticamente a cada 3 segundos.**

## 🐛 Troubleshooting

### "total": 0 (nenhum log)

**Causa**: Simuladores rodando com código antigo

**Solução**:
```bash
# Parar simuladores (Ctrl+C no terminal)
# Iniciar novamente:
./start_simulators.sh

# Testar:
./test_integration_final.sh
```

### Endpoint not found

**Causa**: CAOS está com código antigo

**Solução**:
```bash
# Parar CAOS (Ctrl+C)
# Iniciar novamente:
./dev.sh

# Ou reiniciar servidor uvicorn
```

### httpx.ConnectError

**Causa**: CAOS não está rodando

**Solução**:
```bash
./dev.sh
```

## 📚 Documentação Completa

- **LOGS_AGENTS_SUMMARY.md** - Resumo completo da implementação
- **SIMULATORS_LOGS.md** - Guia detalhado dos simuladores
- **test_quick_agents.py** - Teste rápido (3 operações)
- **test_all_agents_logs.py** - Teste completo (15+ operações)

## ✨ Próximos Passos

1. **Filtros no Dashboard**: Adicionar filtros por agente na UI
2. **Persistência**: Salvar logs em banco de dados
3. **Alertas Visuais**: Destacar logs de WARNING/ERROR
4. **Gráficos**: Visualizar distribuição de logs por agente
5. **Exportação**: Download de logs em CSV/JSON

---

**Status**: ✅ Implementado e testado  
**Requer**: Reiniciar simuladores para ativar  
**Última atualização**: Fevereiro 2026
