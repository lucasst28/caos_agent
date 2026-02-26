
import httpx
import json
import time

BASE_URL = "http://localhost:8080"
TRIGGER_URL = f"{BASE_URL}/v1/events/trigger"
REASONING_URL = f"{BASE_URL}/v1/reasoning"

def run_demo():
    print("\n🌐 CAOS FULL STACK DEMO (Com Simuladores)")
    print("==========================================")
    
    # 1. SEND TRIGGER
    payload = {
        "tenant_id": "t1",
        "asset_id": "CHILLER-04",  # Asset known to mock Atlas
        "metric": "temperature",
        "value": 108.5,            # High temp to provoke thought
        "severity": "HIGH",
        "payload": {
            "source": "SENTINEL",
            "description": "Vibration anomaly detected"
        }
    }
    
    print(f"📡 Enviando Trigger (Sentinel): {payload['asset_id']} Temp={payload['value']}°C")
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
        print("Certifique-se que o 'dev.sh' está rodando.")
        return

    # 2. POLL FOR REASONING
    print("⏳ Aguardando processamento e indexação no Reasoning Store...")
    found = False
    for i in range(12):
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

    # 3. DISPLAY RESULTS
    print("\n🧠 ANÁLISE COMPLETA DO CAOS:")
    print("-" * 40)
    
    # A. Atlas Context
    sense = data.get("sense", {})
    atlas_ctx = sense.get("atlas_context", {}) if sense else {}
    current_state = atlas_ctx.get("current_state", {})
    
    print(f"📚 [ATLAS] Contexto do Ativo:")
    print(f"   - Max Temp: {atlas_ctx.get('max_operating_temp')}°C")
    print(f"   - Status Atual: {current_state.get('status', 'N/A')}")
    print(f"   - Manutenção: {atlas_ctx.get('under_maintenance')}")

    # B. Oracle Forecast (if any)
    oracle = data.get("oracle", {})
    forecast = oracle.get("forecast")
    if forecast:
        print(f"🔮 [ORACLE] Previsão de Falha:")
        print(f"   - Probabilidade: {forecast.get('probability_of_failure', 0)*100:.1f}%")
        print(f"   - RUL (Vida Útil Restante): {forecast.get('predicted_rul_days')} dias")
    else:
        print("🔮 [ORACLE] Bypassed ou Indisponível")

    # C. Cortex / RAG
    cortex = data.get("cortex", {})
    risk = cortex.get("risk_dimensions", {})
    print(f"🤖 [CORTEX] Matriz de Risco:")
    print(f"   - Físico: {risk.get('physical', 0):.2f}")
    print(f"   - Financeiro: {risk.get('financial', 0):.2f}")
    
    # Check for RAG in trace
    trace = data.get("reasoning_trace", [])
    rag_found = False
    for line in trace:
        if "Manual Técnico" in line or "Contexto RAG" in line:
            rag_found = True
            break
            
    if rag_found:
        print("   - 📘 RAG: Manual Técnico consultado com sucesso.")
    else:
        print("   - ⚠️ RAG: Não utilizado ou não encontrado no trace.")
    
    # D. Final Verdict
    print("-" * 40)
    print(f"⚖️ VEREDITO FINAL: {data.get('decision')} (Risco {data.get('risk_level')})")
    
    act = data.get("act", {})
    print(f"💡 Ação: {act.get('action_id', 'N/A')}")
    print(f"⏱️ Tempo de Processamento: {data.get('processing_time_ms'):.2f}ms")

if __name__ == "__main__":
    run_demo()
