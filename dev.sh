#!/bin/bash
# =============================================
# CAOS Agent - Dev Launcher
# Inicia o simulador + servidor CAOS local
# =============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Cores
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}🧠 CAOS Agent - Ambiente Local${NC}"
echo "=================================="

# Ativar venv
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Criando virtual environment...${NC}"
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]" -q
else
    source .venv/bin/activate
fi

# Verificar .env
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env não encontrado! Copie de .env.example${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}🔧 Iniciando Simuladores (Atlas + Sentinel + Oracle)...${NC}"
uvicorn simulators.mock_agents:app --port 9000 --reload &
SIM_PID=$!
sleep 2

echo -e "${GREEN}🚀 Iniciando CAOS Agent...${NC}"
uvicorn main:app --app-dir src --port 8080 --reload &
CAOS_PID=$!
sleep 2

echo ""
echo "=================================="
echo -e "${GREEN}✅ Tudo rodando!${NC}"
echo ""
echo -e "  �️  Dashboard:  ${BLUE}http://localhost:8080/dashboard${NC}"
echo -e "  🧠 CAOS Agent:  ${BLUE}http://localhost:8080/docs${NC}"
echo -e "  🔧 Simuladores: ${BLUE}http://localhost:9000/docs${NC}"
echo ""
echo -e "  📡 Atlas:    http://localhost:9000/atlas/v1/..."
echo -e "  🚨 Sentinel: http://localhost:9000/sentinel/v1/..."
echo -e "  🔮 Oracle:   http://localhost:9000/oracle/v1/..."
echo ""
echo -e "${YELLOW}Ctrl+C para parar tudo${NC}"
echo "=================================="

# Cleanup on exit
trap "kill $SIM_PID $CAOS_PID 2>/dev/null; echo ''; echo 'Parado.'" EXIT INT TERM

# Wait
wait
