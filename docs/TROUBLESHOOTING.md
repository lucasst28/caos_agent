# Guia de Troubleshooting - Dashboard CAOS

## 🔍 Como Verificar se os Botões Estão Funcionando

### Passo 1: Abrir o Console do Navegador
- **Mac**: `Cmd + Option + J`
- **Windows/Linux**: `Ctrl + Shift + J`
- Ou: Botão direito → "Inspecionar" → aba "Console"

### Passo 2: Recarregar a Página com Cache Limpo
- **Mac**: `Cmd + Shift + R`
- **Windows/Linux**: `Ctrl + Shift + R`

### Passo 3: Verificar os Logs no Console

Você deve ver algo assim:
```
📝 Registering DOMContentLoaded listener...
✅ Listener registered
=== DEBUG START ===
Document ready state: loading
CONFIG defined: true
=== DEBUG END ===
🔔 DOMContentLoaded event fired!
🚀 CAOS Dashboard initializing...
📍 API URL: http://localhost:8080
⏱️  Poll Interval: 3000
📄 Document ready state: interactive
✅ Clock started
✅ Navigation setup
✅ Settings setup
... (mais logs)
🎉 Initialization complete!
```

### Passo 4: Testar um Botão

Após ver "🎉 Initialization complete!", tente clicar em qualquer botão:
- Botões da sidebar (Overview, Agentes, Streams, etc.)
- Botão "Atualizar" (ícone de refresh no topo direito)
- Cards clicáveis (métricas "Validados" e "Rejeitados")

### Passo 5: Verificar Erros

Se houver erros em vermelho no console:

#### ❌ Erro: "TypeError: Cannot read properties of null"
**Causa**: Elemento não encontrado no DOM
**Solução**: Verificar se o HTML tem o elemento com o ID correto

#### ❌ Erro: "ReferenceError: X is not defined"
**Causa**: Função ou variável não foi declarada
**Solução**: Verificar se a função está exportada para window

#### ❌ Erro: "Failed to fetch"
**Causa**: API não está respondendo
**Solução**: Verificar se o servidor está rodando na porta 8080

### Passo 6: Teste Manual

No console, execute:
```javascript
// Testar se as funções existem
console.log('switchSection:', typeof switchSection);
console.log('pollAll:', typeof pollAll);
console.log('openAtlasConfig:', typeof window.openAtlasConfig);

// Testar navegação manualmente
switchSection('agents');

// Testar polling manualmente
pollAll();
```

## 📊 Status Atual

### ✅ O que foi corrigido:
1. ✅ Removido conflito entre `caos-control.js` e `caos-dashboard.js`
2. ✅ Removido `neural-bg.js` que dependia de THREE.js não carregado
3. ✅ Adicionado logs detalhados de debug
4. ✅ Corrigido caminhos dos assets (CSS e JS)
5. ✅ Adicionado `toggleChartInterval` ao escopo global

### 🔧 Arquivos carregados agora:
1. Chart.js (CDN)
2. debug.js (logs de diagnóstico)
3. caos-dashboard.js (arquivo principal)

## 🐛 Se Ainda Não Funcionar

### Opção 1: Verificar se JavaScript está habilitado
Alguns bloqueadores de script podem estar interferindo.

### Opção 2: Testar em modo incógnito
Isso elimina problemas com extensões do navegador.

### Opção 3: Verificar o servidor
```bash
# Verificar se o servidor está rodando
lsof -i :8080

# Ver logs do servidor
tail -f /tmp/caos.log

# Reiniciar servidor
pkill -f "uvicorn main:app"
.venv/bin/uvicorn main:app --app-dir src --port 8080 --reload
```

### Opção 4: Testar endpoints manualmente
```bash
# Health check
curl http://localhost:8080/health

# Dashboard HTML
curl -I http://localhost:8080/dashboard

# JavaScript
curl -I http://localhost:8080/dashboard/assets/js/caos-dashboard.js

# CSS
curl -I http://localhost:8080/dashboard/assets/css/caos-styles.css
```

## 📝 Reportar Problemas

Se os botões ainda não funcionarem, copie e cole:
1. Todos os logs do console (texto completo)
2. Qualquer mensagem de erro em vermelho
3. Resultado de: `console.log(typeof switchSection, typeof pollAll)`
