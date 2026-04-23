
import asyncio
import structlog
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

# Setup mocked environment
from caos._registry import put
mock_llm = AsyncMock()
put("llm", mock_llm)

# Import logic
from caos.safety.layers import ReflexLayer
from caos.core.nodes import cortex
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerPayload, TriggerContext, TriggerSource, Severity

async def run_demo():
    print("\n🚀 CAOS DEMO: FASE 3 & 4 (Maturidade Lógica)")
    print("="*50)

    # 1. PARAMETRIC GUARDRAILS
    print("\nTESTE 1: Guardrails Paramétricos (Limite Dinâmico)")
    print("-" * 40)
    reflex = ReflexLayer()
    
    # Case A: Asset with Low Limit (50C)
    state_low = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_1", source=TriggerSource.SENTINEL, timestamp=datetime.now(timezone.utc),
            metric="temperature", value=56.0, severity="HIGH",
            context=TriggerContext(tenant_id="t1", asset_id="asset_sensitive")
        ),
        atlas_context={"max_operating_temp": 50.0}
    )
    result = reflex.check(state_low)
    print(f"🔹 Ativo (Max: 50°C) | Leitura: 56°C")
    print(f"👉 Resultado: {'⛔ VETO' if result.triggered else '✅ PASSOU'}")
    print(f"📝 Mensagem: {result.message}")

    # Case B: Asset with High Limit (1000C)
    state_high = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_2", source=TriggerSource.SENTINEL, timestamp=datetime.now(timezone.utc),
            metric="temperature", value=105.0, severity="HIGH",
            context=TriggerContext(tenant_id="t1", asset_id="asset_furnace")
        ),
        atlas_context={"max_operating_temp": 1000.0}
    )
    result = reflex.check(state_high)
    print(f"\n🔹 Ativo (Max: 1000°C) | Leitura: 105°C")
    print(f"👉 Resultado: {'⛔ VETO' if result.triggered else '✅ PASSOU'}")
    
    # 2. RAG & DYNAMIC FUSION
    print("\nTESTE 2: RAG & Fusão Dinâmica (Cortex)")
    print("-" * 40)
    
    # Mock LLM Response with Low Confidence and Analysis
    mock_llm.ainvoke.return_value = MagicMock(content="""
ANÁLISE: O sistema parece estar quente, mas não tenho certeza.
CONFIANÇA: 0.2
RISCO_FISICO: 0.1
RISCO_FINANCEIRO: 0.1
RISCO_CONTRATUAL: 0.1
RISCO_COMUNICACAO: 0.1
AÇÃO: notification
JUSTIFICATIVA: Incerteza nos dados.
""")

    state_cortex = JudgeState(
        trigger=TriggerPayload(
            event_id="evt_3", source=TriggerSource.SENTINEL, timestamp=datetime.now(timezone.utc),
            metric="temperature", value=115.0, severity="HIGH",
            context=TriggerContext(tenant_id="t1", asset_id="asset_standard")
        ),
        atlas_context={"max_operating_temp": 100.0}
    )
    
    # This will trigger RAG query inside cortex_node
    # And allow us to see the reasoning trace
    from caos.core.nodes.rag import get_rag_engine
    rag = get_rag_engine() # Seeding happens inside cortex or manual here
    # Ensure seeding
    if not rag.documents:
        rag.add_document("Manual Refrigeração: Checar ventilador se > 110C", {"t":"cool"}, "doc1")

    print("🔹 Executando Cortex com LLM (Confiança simulada: 0.2)...")
    result = await cortex.cortex_node(state_cortex)
    
    print("\n🔍 Rastreamento de Raciocínio (Snippet):")
    for line in result.get("reasoning_trace", []):
         if "Fusão híbrida" in line:
             print(f"👉 {line}")
         if "Manual Técnico" in line:
             print(f"📚 {line}")

if __name__ == "__main__":
    asyncio.run(run_demo())
