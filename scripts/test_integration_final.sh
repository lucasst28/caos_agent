#!/usr/bin/env bash
# test_integration_final.sh
# Testa a integração completa dos logs dos agentes

echo "🔧 TESTE DE INTEGRAÇÃO COMPLETA DOS LOGS"
echo "=========================================="
echo ""

# Verificar se CAOS está rodando
echo "1️⃣  Verificando CAOS..."
if curl -s http://localhost:8080/health > /dev/null; then
    echo "   ✅ CAOS está rodando na porta 8080"
else
    echo "   ❌ CAOS não está rodando!"
    echo "   Execute: ./dev.sh"
    exit 1
fi

# Verificar se simuladores estão rodando
echo ""
echo "2️⃣  Verificando Simuladores..."
if curl -s http://localhost:9000/health > /dev/null; then
    echo "   ✅ Simuladores estão rodando na porta 9000"
else
    echo "   ❌ Simuladores não estão rodando!"
    echo "   Execute: ./start_simulators.sh"
    exit 1
fi

# Limpar logs antigos
echo ""
echo "3️⃣  Limpando logs antigos..."
curl -s -X DELETE "http://localhost:8080/v1/logs/" > /dev/null
echo "   ✅ Logs limpos"

# Aguardar um pouco
sleep 1

# Executar teste
echo ""
echo "4️⃣  Executando testes dos agentes..."
echo ""
.venv/bin/python test_quick_agents.py

# Aguardar processamento
sleep 2

# Verificar logs capturados
echo ""
echo "5️⃣  Verificando logs capturados..."
TOTAL=$(curl -s "http://localhost:8080/v1/logs/?limit=50" | jq '.total')
echo "   📊 Total de logs: $TOTAL"

if [ "$TOTAL" -gt 0 ]; then
    echo ""
    echo "   📈 Logs por agente:"
    curl -s "http://localhost:8080/v1/logs/?limit=50" | \
        jq -r '.logs[] | select(.context.agent != null) | .context.agent' | \
        sort | uniq -c | \
        awk '{printf "      • %-12s: %2d logs\n", $2, $1}'
    
    echo ""
    echo "   📝 Últimos eventos:"
    curl -s "http://localhost:8080/v1/logs/?limit=10" | \
        jq -r '.logs[] | "      [\(.timestamp[11:19])] \(.context.agent // "CAOS"): \(.event)"' | \
        head -5
else
    echo "   ⚠️  Nenhum log capturado ainda."
    echo ""
    echo "   💡 Os simuladores precisam ser reiniciados para carregar o novo código:"
    echo "      1. Pare os simuladores (Ctrl+C)"
    echo "      2. Inicie novamente: ./start_simulators.sh"
    echo "      3. Execute este script novamente"
fi

echo ""
echo "=========================================="
echo "✅ Teste completo!"
echo ""
echo "📊 Ver dashboard: http://localhost:8080/dashboard"
echo "📡 Ver API: curl 'http://localhost:8080/v1/logs/' | jq"
echo ""
