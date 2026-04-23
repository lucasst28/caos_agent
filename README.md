# 🧠 CAOS Agent

**Centralized Autonomous Operating System** — Judge-LM for VivAIOT

CAOS is the decision-making core of the VivAIOT multi-agent architecture. It receives alerts from Sentinel, context from Atlas, predictions from Oracle, and produces actionable decisions dispatched to CARE for physical execution.

## Architecture

```
Sentinel → Pub/Sub → CAOS Brain → Cloud Workflows → CARE
                       ↑
                  Atlas (context)
                  Oracle (prediction)
```

### Cognitive Flow
```
Trigger → Sense → [Oracle?] → Cortex → Guardrails → Act → END
```

## Quick Start

```bash
# Clone
git clone <repo-url>
cd caos_agent

# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# Run
cp .env.example .env

```
V = (W_A × A + W_O × O - W_S × (1 + S)) / (1 + Σ(P_G × V_G))
```

Onde:
- **V**: Veredito (-1.0 a +1.0)
- **A**: Score do Atlas (contexto)
- **O**: Score do Oracle (predição)
- **S**: Score de Severidade (risco)
- **P_G × V_G**: Penalidades de Guardrails

## 📚 Documentação

Consulte `caos_agent_documentation.tex` para especificação técnica completa.
