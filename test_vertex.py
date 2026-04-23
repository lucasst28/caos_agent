import os
from langchain_google_vertexai import ChatVertexAI
from google.cloud import aiplatform

print("Carregando variáveis de ambiente...")
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "agente-caos")
location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
model_name = os.environ.get("VERTEX_AI_MODEL", "gemini-1.5-flash-002")

print(f"Projeto: {project_id}")
print(f"Local: {location}")
print(f"Modelo: {model_name}")

try:
    print("\n1. Inicializando aiplatform...")
    aiplatform.init(project=project_id, location=location)
    
    print("2. Inicializando modelo...")
    llm = ChatVertexAI(
        model_name=model_name,
        project=project_id,
        location=location
    )
    
    print("3. Enviando mensagem de teste: 'Olá, diga que você está vivo!'...")
    response = llm.invoke("Olá, diga que você está vivo em 1 frase curta!")
    
    print("\n✅ SUCESSO! Conexão estabelecida e modelo respondeu:")
    print("🤖 MODELO: ", response.content)

except Exception as e:
    print("\n❌ ERRO! Não foi possível se conectar ao Vertex AI:")
    print(str(e))
