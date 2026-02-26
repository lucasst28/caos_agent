#!/bin/bash
# Script para testar a integração CAOS frontend/backend

echo "====================================="
echo "CAOS - Teste de Integração"
echo "====================================="
echo ""

# Verificar se o servidor está rodando
if lsof -i :8080 > /dev/null 2>&1; then
    echo "✅ Servidor já está rodando na porta 8080"
else
    echo "⚠️  Servidor não está rodando. Iniciando..."
    source .venv/bin/activate
    nohup uvicorn main:app --app-dir src --port 8080 --reload > /tmp/caos-server.log 2>&1 &
    echo "   PID: $!"
    echo "   Aguardando servidor iniciar..."
    sleep 5
fi

echo ""
echo "🧪 Testando endpoints..."
echo ""

# Test health
echo "1. Health Check:"
HEALTH=$(curl -s http://localhost:8080/health)
if [ $? -eq 0 ]; then
    echo "   ✅ $HEALTH"
else
    echo "   ❌ Falhou"
fi

echo ""
echo "2. Logs Endpoint:"
LOGS=$(curl -s "http://localhost:8080/v1/logs/?limit=5")
if [ $? -eq 0 ]; then
    TOTAL=$(echo "$LOGS" | python3 -c "import sys, json; print(json.load(sys.stdin)['total'])" 2>/dev/null || echo "0")
    echo "   ✅ Total de logs: $TOTAL"
else
    echo "   ❌ Falhou"
fi

echo ""
echo "3. Gerando evento de teste..."
EVENT=$(curl -s -X POST "http://localhost:8080/v1/events/trigger" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "viva",
    "asset_id": "TEST-ASSET",
    "severity": "MEDIUM",
    "metric": "temperature",
    "value": 75.5,
    "location": "Lab",
    "payload": {"description": "Teste de integração"}
  }')

if [ $? -eq 0 ]; then
    EVENT_ID=$(echo "$EVENT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('event_id', 'N/A'))" 2>/dev/null)
    DECISION=$(echo "$EVENT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('decision', 'N/A'))" 2>/dev/null)
    echo "   ✅ Evento processado: $EVENT_ID"
    echo "   Decisão: $DECISION"
else
    echo "   ❌ Falhou"
fi

echo ""
echo "4. Verificando logs após evento:"
sleep 1
NEW_LOGS=$(curl -s "http://localhost:8080/v1/logs/?limit=20")
NEW_TOTAL=$(echo "$NEW_LOGS" | python3 -c "import sys, json; print(json.load(sys.stdin)['total'])" 2>/dev/null || echo "0")
echo "   ✅ Total de logs agora: $NEW_TOTAL"

echo ""
echo "====================================="
echo "📊 Acesse o dashboard em:"
echo "   http://localhost:8080/dashboard"
echo ""
echo "📚 API docs em:"
echo "   http://localhost:8080/docs"
echo "====================================="
