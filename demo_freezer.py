
import httpx
import json
import time
import sys

BASE_URL = "http://localhost:8080"
TRIGGER_URL = f"{BASE_URL}/v1/events/trigger"
REASONING_URL = f"{BASE_URL}/v1/reasoning"

def run_demo():
    print("\n❄️ CAOS DEMO: CENÁRIO FREEZER (Context Intelligence)")
    print("====================================================")
    
    # 1. SEND TRIGGER
    # Sentinel detecta temperatura alta (-5C, quando deveria ser -18C)
    payload = {
        "tenant_id": "t1",
        "asset_id": "FREEZER-01",
        "metric": "temperature",
        "value": -5.0, 
        "severity": "HIGH",
        "payload": {
            "source": "SENTINEL",
            "description": "Temperature above threshold (-10C)"
        }
    }
    
    print(f"📡 Enviando Alerta: {payload['asset_id']} Temp={payload['value']}°C (High)")
    try:
        resp = httpx.post(TRIGGER_URL, json=payload, timeout=20.0)
        if resp.status_code != 201:
            print(f"❌ Erro ao enviar trigger: {resp.text}")
            return
        event_data = resp.json()
        event_id = event_data["event_id"]
        print(f"✅ Evento recebido: {event_id}. Processando...")
    except Exception as e:
        print(f"❌ Falha de conexão: {e}")
        return

    # 2. POLL FOR REASONING
    print("⏳ Aguardando análise do CAOS...")
    found = False
    data = {}
    for i in range(15):
        time.sleep(1)
        try:
            r = httpx.get(f"{REASONING_URL}/{event_id}", timeout=5.0)
            if r.status_code == 200:
                found = True
                data = r.json()
                break
        except:
            pass
    
    if not found:
        print("❌ Timeout aguardando reasoning.")
        return

    # 3. ANALYZE RESULTS
    print("\n🧠 RACIOCÍNIO DO AGENTE:")
    print("-" * 40)
    
    # A. Contexto Atlas (Telemetria)
    sense = data.get("sense", {})
    atlas_ctx = sense.get("atlas_context", {})
    sim_data = atlas_ctx.get("simulation_data", {})
    
    print(f"📚 [ATLAS] Dados Operacionais:")
    print(f"   - Temp Ambiente: {sim_data.get('ambient_temperature', 'N/A')}°C")
    print(f"   - Aberturas Porta/Hora: {sim_data.get('door_open_events_last_hour', 'N/A')}")
    
    # B. RAG (Manual)
    trace = data.get("reasoning_trace", [])
    rag_snippet = next((line for line in trace if "Manual Técnico" in line or "Contexto RAG" in line), None)
    
    if rag_snippet:
         print(f"📘 [RAG] Conhecimento Recuperado:")
         # Extract clean text from trace if possible, or just print confirmation
         print(f"   - Manual encontrado e injetado no prompt.")
    else:
         print(f"⚠️ [RAG] Manual não citado explicitamente no trace.")

    # C. LLM Analysis & Decision
    print(f"🤖 [CORTEX] Análise:")
    llm_analysis = data.get("cortex", {}).get("llm_analysis", "N/A")
    # Parse string representation if it's a dict string
    if isinstance(llm_analysis, str) and llm_analysis.startswith("{"):
         try:
             # It might be a repr() of a dict, not valid JSON. Just print it.
             pass
         except:
             pass
    
    print(f"   -> {llm_analysis}")
    
    decision = data.get("decision")
    risk = data.get("risk_level")
    
    print("-" * 40)
    print(f"⚖️ VEREDITO: {decision} (Risco {risk})")
    
    # Validation logic
    success = False
    if "OPERACIONAL" in str(llm_analysis).upper() or "MONITOR" in str(llm_analysis).upper():
        success = True
    if decision == "APPROVED" or decision == "Review Required": # Often "Review Required" for anomalies, but shouldn't be BLOCKED/CRITICAL
        # Actually user wants to see logic. 
        # If risk is not CRITICAL despite HIGH severity alert, it's a win.
        pass

    if risk != "CRITICAL":
         print("✅ SUCESSO: O agente entendeu que não é uma falha crítica!")
    else:
         print("⚠️ ALERTA: O agente ainda considerou Risco Crítico.")

if __name__ == "__main__":
    run_demo()
