
import shutil
import os
import time
from caos.core.nodes.rag import RagEngine
from caos.safety.runtime import CircuitBreaker, MemoryBackend

DEMO_PATH = "./demo_rag_data"

def clean_slate():
    if os.path.exists(DEMO_PATH):
        shutil.rmtree(DEMO_PATH)
    print(f"🧹 Diretório limpo: {DEMO_PATH}")

def run_demo():
    print("\n💾 CAOS DEMO: FASE 5 (Persistência & Infra)")
    print("==============================================")
    
    clean_slate()
    
    # 1. SESSION A: INGESTION
    print("\n[Sessão A] Iniciando Agente (Frio)...")
    rag = RagEngine(persist_path=DEMO_PATH)
    
    print("📝 Aprendendo novo manual técnico...")
    manual_text = (
        "PROTOCOLO DE SEGURANÇA - REATOR DE FUSÃO: "
        "Se a vibração exceder 50Hz, desligue os injetores de plasma imediatamente. "
        "Risco de colapso magnético."
    )
    rag.add_document(manual_text, {"source": "manual_v2.pdf"}, "doc_fusion_01")
    print("✅ Documento indexado e salvo em disco.")
    
    # Simulate work
    results = rag.query("vibração reator", k=1)
    print(f"🔍 Teste de memória imediata: '{results[0][:50]}...'")
    
    # 2. SIMULATE CRASH
    print("\n💥 SIMULANDO FALHA DE SISTEMA / RESTART DO CONTAINER...")
    del rag
    time.sleep(1)
    print("... Sistema reiniciando ...")
    
    # 3. SESSION B: RECOVERY
    print("\n[Sessão B] Iniciando Agente (Recuperação)...")
    rag_new = RagEngine(persist_path=DEMO_PATH)
    # Note: load_from_disk is called automatically in __init__ now
    
    print(f"📂 Memória recuperada. Documentos no índice: {len(rag_new.documents)}")
    
    # 4. QUERY AFTER RESTART
    print("🔍 Consultando 'vibração reator' após restart...")
    results_new = rag_new.query("vibração reator", k=1)
    
    if results_new and "desligue os injetores" in results_new[0]:
        print(f"✅ RECUPERADO COM SUCESSO: \n   \"{results_new[0]}\"")
    else:
        print("❌ FALHA NA RECUPERAÇÃO.")

    # 5. REDIS BACKEND CHECK
    print("\n[Infra] Verificando Backend do Circuit Breaker")
    try:
        from caos.config import get_settings
        from caos.safety.runtime import RedisBackend
        s = get_settings()
        if s.redis_url:
            print(f"✅ Configurado para REDIS: {s.redis_url}")
        else:
            print("⚠️ Configurado para MEMÓRIA (Dev/Test). Defina REDIS_URL para produção.")
    except Exception as e:
        print(f"Erro ao verificar config: {e}")

    print("\n==============================================")
    print("Demonstração Concluída.")
    clean_slate()

if __name__ == "__main__":
    run_demo()
