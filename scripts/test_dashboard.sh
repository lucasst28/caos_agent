#!/bin/bash
# Script para testar o dashboard do CAOS

echo "🧪 Testando Dashboard CAOS"
echo "=========================="
echo ""

# Verificar se servidor está rodando
if ! lsof -i :8080 > /dev/null 2>&1; then
    echo "❌ Servidor não está rodando na porta 8080"
    echo "   Inicie com: ./dev.sh ou uvicorn main:app --app-dir src --port 8080 --reload"
    exit 1
fi

echo "✅ Servidor rodando na porta 8080"
echo ""

# Testar endpoints básicos
echo "🔍 Testando endpoints..."
echo ""

# Health
echo -n "  • /health ... "
if curl -s -f http://localhost:8080/health > /dev/null; then
    echo "✅"
else
    echo "❌"
fi

# Dashboard HTML
echo -n "  • /dashboard ... "
if curl -s -f http://localhost:8080/dashboard > /dev/null; then
    echo "✅"
else
    echo "❌"
fi

# CSS
echo -n "  • CSS ... "
if curl -s -f http://localhost:8080/dashboard/assets/css/caos-styles.css > /dev/null; then
    echo "✅"
else
    echo "❌"
fi

# JS
echo -n "  • JavaScript ... "
if curl -s -f http://localhost:8080/dashboard/assets/js/caos-dashboard.js > /dev/null; then
    echo "✅"
else
    echo "❌"
fi

# Logs API
echo -n "  • API Logs ... "
if curl -s -f "http://localhost:8080/v1/logs/?limit=5" > /dev/null; then
    echo "✅"
else
    echo "❌"
fi

echo ""
echo "=========================="
echo "📊 Dashboard: http://localhost:8080/dashboard"
echo "📚 API Docs:  http://localhost:8080/docs"
echo ""
echo "💡 Dica: Abra o Console do Navegador (F12) para ver logs de JavaScript"
echo "=========================="
