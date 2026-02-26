#!/usr/bin/env bash
# start_simulators.sh
# Inicia os simuladores dos agentes ATLAS, SENTINEL e ORACLE

set -e

echo "🚀 Iniciando Simuladores dos Agentes..."
echo ""
echo "Agentes disponíveis:"
echo "  • ATLAS    - Digital Twin     (porta 9000/atlas/v1/...)"
echo "  • SENTINEL - Alertas          (porta 9000/sentinel/v1/...)"
echo "  • ORACLE   - Predições        (porta 9000/oracle/v1/...)"
echo ""

# Verificar se o ambiente virtual existe
if [ ! -d ".venv" ]; then
    echo "❌ Ambiente virtual não encontrado!"
    echo "   Execute: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Ativar ambiente virtual
source .venv/bin/activate

echo "✅ Ambiente virtual ativado"
echo ""
echo "📡 Iniciando servidor na porta 9000..."
echo "   Acesse a documentação: http://localhost:9000/docs"
echo ""

# Iniciar uvicorn
exec uvicorn simulators.mock_agents:app \
    --port 9000 \
    --reload \
    --log-level info
