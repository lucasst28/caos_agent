
import httpx
import json
import time

BASE_URL = "http://localhost:8080"
TRIGGER_URL = f"{BASE_URL}/v1/events/trigger"
REASONING_URL = f"{BASE_URL}/v1/reasoning"

def run_agentic_demo():
    print("\n🕵️ CAOS AGENTIC DEMO: DESCOBERTA DE CLIMA")
    print("====================================================")
    
    # 1. SEND TRIGGER (FREEZER-01)
    # Note: Atlas não terá mais a temperatura ambiente por padrão.
    payload = {
        "tenant_id": "t1",
        "asset_id": "FREEZER-01",
        "metric": "temperature",
        "value": -8.0, 
        "severity": "HIGH",
    }
    
    print(f"📡 Enviando Alerta: {payload['asset_id']} em -5°C. O agente deve decidir se precisa de mais dados.")
    
    try:
        resp = httpx.post(TRIGGER_URL, json=payload, timeout=20.0)
        event_id = resp.json()["event_id"]
        print(f"✅ Evento carregado: {event_id}")
    except Exception as e:
        print(f"❌ Falha: {e}")
        return

    # 2. POLL FOR REASONING
    print("⏳ Aguardando o agente raciocinar e possivelmente buscar o clima...")
    data = {}
    for i in range(20):
        time.sleep(1)
        try:
            r = httpx.get(f"{REASONING_URL}/{event_id}", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                break
        except:
            pass
    
    if not data:
        print("❌ Timeout.")
        return

    # 3. ANALYZE TRACE
    print("\n🎞️ LINHA DO TEMPO DO RACIOCÍNIO (RAG & DISCOVERY):")
    print("-" * 40)
    trace = data.get("reasoning_trace", [])
    
    found_discovery = False
    found_weather_data = False
    
    for line in trace:
        print(f"  {line}")
        if "SOLICITAR: CLIMA" in line or "Agente solicitou" in line:
            found_discovery = True
        if "Dados do Clima recebidos" in line:
            found_weather_data = True

    print("-" * 40)
    
    # 4. FINAL VERDICT
    print(f"⚖️ VEREDITO FINAL: {data.get('decision')}")
    print(f"🤖 ANÁLISE LLM: {data.get('cortex', {}).get('llm_analysis', {}).get('analysis', 'N/A')}")
    
    if found_discovery and found_weather_data:
        print("\n✅ SUCESSO! O agente demonstrou autonomia ao solicitar e integrar os dados do clima.")
    else:
        print("\n⚠️ O loop agêntico não foi detectado no trace (talvez ele decidiu sem o clima?).")

if __name__ == "__main__":
    run_agentic_demo()
