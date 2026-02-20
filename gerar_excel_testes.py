#!/usr/bin/env python3
"""Gera o Excel com todos os cenários de teste do CAOS Agent."""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.Workbook()

# ============================================================
# CORES E ESTILOS
# ============================================================
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
LEVEL_FILLS = {
    "Nível 0": PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid"),
    "Nível 0.5": PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid"),
    "Nível 1": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
    "Nível 2": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
    "Nível 3": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
    "Nível 4": PatternFill(start_color="F4B084", end_color="F4B084", fill_type="solid"),
    "Nível 5": PatternFill(start_color="D5A6BD", end_color="D5A6BD", fill_type="solid"),
}
PASS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
PASS_FONT = Font(name="Calibri", bold=True, color="006100")
BODY_FONT = Font(name="Calibri", size=10)
WRAP = Alignment(wrap_text=True, vertical="top")
THIN_BORDER = Border(
    left=Side(style="thin", color="B2B2B2"),
    right=Side(style="thin", color="B2B2B2"),
    top=Side(style="thin", color="B2B2B2"),
    bottom=Side(style="thin", color="B2B2B2"),
)

# ============================================================
# ABA 1: CENÁRIOS DE TESTE
# ============================================================
ws = wb.active
ws.title = "Cenários de Teste"

headers = [
    "Nível",
    "Cenário",
    "Ativo",
    "Cliente / Local",
    "Descrição da Situação",
    "Dados Principais do Equipamento",
    "O que o CAOS Deve Fazer",
    "Resultado Esperado da LLM",
    "Tipo de Decisão Esperada",
    "Score Obtido",
    "Status",
]

# Cabeçalho
for col, h in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=h)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = THIN_BORDER

# Largura das colunas
widths = [8, 32, 14, 28, 50, 55, 50, 50, 18, 12, 10]
for i, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = w

# ============================================================
# DADOS DOS CENÁRIOS
# ============================================================
scenarios = [
    # Nível 0 — Base
    {
        "nivel": "Nível 0",
        "cenario": "Abertura Excessiva de Portas",
        "ativo": "FREEZER-01",
        "cliente": "Distribuidora Vila Nova, São Paulo",
        "descricao": (
            "Sentinel detecta temperatura alta (-5°C, threshold -11°C). "
            "A causa é operacional: 15 aberturas de porta na última hora, "
            "vedação a 85%, calor externo de 35.5°C."
        ),
        "dados": (
            "• Temperatura: -5°C (alarme: -11°C)\n"
            "• Aberturas de porta: 15/hora (normal: 3-5)\n"
            "• Vedação da porta: 85%\n"
            "• Compressor: 92% carga, 4.1A / 4.5A\n"
            "• Temperatura ambiente: 35.5°C\n"
            "• Consumo energia: 12 kWh/dia"
        ),
        "deve_fazer": (
            "✓ Correlacionar 15 aberturas com elevação de temp.\n"
            "✓ Notar padrão operacional, não falha técnica\n"
            "✓ Consultar manual técnico\n"
            "✓ Opcionalmente buscar dados climáticos\n"
            "✓ Recomendar orientação ao operador"
        ),
        "resultado_llm": (
            "Causa OPERACIONAL — excesso de aberturas de porta + calor externo. "
            "Recomendar treinamento do operador, não manutenção técnica."
        ),
        "decisao": "SUGGEST",
        "score": "6/6",
        "status": "✅ PASS",
    },

    # Nível 0.5 — Desligamento Noturno
    {
        "nivel": "Nível 0.5",
        "cenario": "Desligamento Noturno pelo Operador",
        "ativo": "FREEZER-02",
        "cliente": "Mercado Bom Preço, Campinas",
        "descricao": (
            "Operador desliga o freezer de carnes toda noite às 22h para 'economizar energia' "
            "e liga de manhã às 7:30. Temperatura sobe para 8.5°C. "
            "Compressor força recuperação gastando MAIS energia. "
            "Produtos ficam em risco sanitário (ANVISA)."
        ),
        "dados": (
            "• Temperatura: 2.0°C (alarme: -11°C)\n"
            "• Histórico: desligado 22:00→07:30 por 5 dias consecutivos\n"
            "• Temp. ao ligar: 8.5°C cada manhã\n"
            "• Compressor: overload 4.8A / 4.5A (102%)\n"
            "• Consumo: 18 kWh (média normal 14 kWh)\n"
            "• Carne bovina no estoque (~R$15.000)"
        ),
        "deve_fazer": (
            "✓ Detectar padrão de desligamento recorrente\n"
            "✓ Calcular que gasta MAIS energia (18 vs 14 kWh)\n"
            "✓ Identificar risco sanitário (carne acima de 4°C)\n"
            "✓ Recomendar NÃO desligar — educação do operador\n"
            "✓ Alertar sobre ANVISA"
        ),
        "resultado_llm": (
            "Padrão de abuso operacional: desligamento noturno gera risco sanitário e gasta mais energia. "
            "Orientar operador a manter equipamento ligado. Risco ANVISA para carnes."
        ),
        "decisao": "ALERT / SUGGEST",
        "score": "6/6",
        "status": "✅ PASS",
    },

    # Nível 1 — Sensor Defeituoso
    {
        "nivel": "Nível 1",
        "cenario": "Sensor Defeituoso (Falso Positivo — Armadilha)",
        "ativo": "FREEZER-03",
        "cliente": "Padaria São Jorge, Belo Horizonte",
        "descricao": (
            "Sensor principal (PT100) lê -2°C (alarme), mas TODOS os outros indicadores "
            "mostram que o freezer está perfeito. Sonda de produto: -19.2°C. "
            "Compressor relaxado (35%). Energia baixa (5.2 kWh). "
            "O LLM deve detectar a CONTRADIÇÃO e não despachar manutenção desnecessária."
        ),
        "dados": (
            "• Sensor principal (PT100): -2°C ← ALARME\n"
            "• Sonda produto (NTC): -19.2°C ← NORMAL\n"
            "• Evaporador: -20.5°C ← NORMAL\n"
            "• Compressor: 35% carga (relaxado)\n"
            "• Energia: 5.2 kWh (abaixo da média)\n"
            "• Vedação porta: 98%\n"
            "• Última calibração sensor: há 14 meses"
        ),
        "deve_fazer": (
            "✓ NÃO cair na armadilha do alarme\n"
            "✓ Notar contradição: sensores discrepantes\n"
            "✓ Comprovar: compressor relaxado + energia baixa\n"
            "✓ Concluir: sensor defeituoso\n"
            "✓ Recomendar calibração/substituição do sensor\n"
            "✗ NÃO enviar técnico para o compressor"
        ),
        "resultado_llm": (
            "Falso positivo — sensor PT100 com drift/descalibração. "
            "Todos os outros indicadores confirmam operação normal. "
            "Recomendar recalibração do sensor. Economia de R$350-500 em chamado desnecessário."
        ),
        "decisao": "SUGGEST",
        "score": "6/6",
        "status": "✅ PASS",
    },

    # Nível 2 — Múltiplas Falhas
    {
        "nivel": "Nível 2",
        "cenario": "Múltiplas Falhas Simultâneas",
        "ativo": "FREEZER-04",
        "cliente": "Distribuidora Gelatto, Ribeirão Preto",
        "descricao": (
            "Câmara fria industrial com TRÊS falhas simultâneas que juntas causam "
            "desvio de 12°C. Nenhum fator sozinho explica o desvio total. "
            "O LLM deve identificar os 3 fatores e sua contribuição combinada."
        ),
        "dados": (
            "• Temperatura: -8°C (ideal: -20°C, desvio: 12°C)\n"
            "• FATOR 1 — Compressor degradando:\n"
            "  - COP: 2.1 (ideal: 3.5)\n"
            "  - Vibração: 3.8 mm/s (limite: 2.5)\n"
            "  - Refrigerante: 78% (vazamento lento)\n"
            "• FATOR 2 — Vedação porta:\n"
            "  - Integridade: 72%\n"
            "  - Condensação detectada\n"
            "• FATOR 3 — Ambiente extremo:\n"
            "  - Temp. externa: 38°C\n"
            "  - Umidade: 75%"
        ),
        "deve_fazer": (
            "✓ Identificar TODOS os 3 fatores\n"
            "✓ Notar vibração elevada (3.8 mm/s)\n"
            "✓ Notar COP baixo (2.1)\n"
            "✓ Notar vedação comprometida (72%)\n"
            "✓ Notar temperatura ambiente extrema\n"
            "✓ Concluir: falha COMPOSTA\n"
            "✓ Recomendar ações para CADA fator"
        ),
        "resultado_llm": (
            "Falha composta: 3 fatores contribuem simultaneamente para o desvio de 12°C. "
            "Priorizar compressor (desgate + vazamento), trocar vedação, "
            "melhorar ventilação do ambiente."
        ),
        "decisao": "ALERT / SUGGEST",
        "score": "10/10",
        "status": "✅ PASS",
    },

    # Nível 3 — Cascata
    {
        "nivel": "Nível 3",
        "cenario": "Falha em Cascata — Root Cause Analysis",
        "ativo": "FREEZER-05",
        "cliente": "Sorveteria Glacial Premium, São Paulo",
        "descricao": (
            "Câmara fria de sorvetes com CADEIA CAUSAL:\n"
            "Vazamento de refrigerante → Compressor sobrecarregado → Motor aquecendo → "
            "Temperatura subindo. O compressor parece ser o problema, "
            "mas é SINTOMA. Manutenção agendada para amanhã, mas não pode esperar."
        ),
        "dados": (
            "• Temperatura: -4°C (era -18°C há 2 dias)\n"
            "• Refrigerante: 55% (CAUSA RAIZ)\n"
            "• Pressão sucção: 12 PSI (baixa)\n"
            "• Superheat: 15°C (alto)\n"
            "• Compressor: 98% carga, 5.3A (overload)\n"
            "• Motor: 92°C (aquecendo)\n"
            "• Vibração: 4.6 mm/s (alto)\n"
            "• COP: 1.4 (péssimo)\n"
            "• Estoque: R$120k em sorvetes premium\n"
            "• Histórico: micro-vazamento adiado na última visita"
        ),
        "deve_fazer": (
            "✓ Identificar vazamento como CAUSA RAIZ\n"
            "✓ Entender que compressor é CONSEQUÊNCIA\n"
            "✓ Decidir NÃO esperar manutenção de amanhã\n"
            "✓ Notar histórico de item adiado\n"
            "✓ Recomendar EMERGÊNCIA: recarga + reparo\n"
            "✓ Calcular risco financeiro (R$120k)"
        ),
        "resultado_llm": (
            "Root Cause: Vazamento de refrigerante (55%). Compressor é consequência, não causa. "
            "NÃO esperar manutenção de amanhã. Despacho de emergência para recarga de gás e "
            "reparo do vazamento. R$120k em risco."
        ),
        "decisao": "BLOCKED / ALERT",
        "score": "10/10",
        "status": "✅ PASS",
    },

    # Nível 4 — Red Herrings
    {
        "nivel": "Nível 4",
        "cenario": "Red Herrings + Sobrecarga de Informação",
        "ativo": "FREEZER-06",
        "cliente": "Supermercado Estrela, Belo Horizonte",
        "descricao": (
            "Temperatura a -6°C com MÚLTIPLAS pistas — a maioria FALSAS.\n"
            "CAUSA REAL: Queda de tensão na rede (198V vs 220V) → compressor sub-performa.\n"
            "4 RED HERRINGS: manutenção recente (bem sucedida), barulho (do freezer ao lado), "
            "funcionário novo (treinado, 3 aberturas = normal), Carnaval (contribui pouco)."
        ),
        "dados": (
            "• Temperatura: -6°C (alarme: -11°C)\n"
            "• CAUSA REAL:\n"
            "  - Tensão: 198V (nominal: 220V) ← CAUSA\n"
            "  - Corrente compressor: 3.8A (normal: ~4.5A)\n"
            "  - HVAC do prédio também afetado\n"
            "• RED HERRING 1: Manutenção há 2 dias (OK)\n"
            "• RED HERRING 2: Barulho (freezer adjacente)\n"
            "• RED HERRING 3: Pedro (novo, 3 aberturas = ok)\n"
            "• RED HERRING 4: Carnaval +45% clientes\n"
            "• Consumo: 7.5 kWh (normal: 9.5 kWh)"
        ),
        "deve_fazer": (
            "✓ Identificar queda de tensão como causa\n"
            "✓ Correlacionar com HVAC/luzes do prédio\n"
            "✓ Notar corrente BAIXA do compressor\n"
            "✓ NÃO culpar manutenção recente\n"
            "✓ NÃO culpar funcionário novo\n"
            "✓ Descartar/minimizar impacto Carnaval\n"
            "✓ Recomendar ação na rede elétrica"
        ),
        "resultado_llm": (
            "Causa: Queda de tensão (198V). Compressor sub-performando por subtensão. "
            "Red herrings descartados: manutenção OK, barulho é adjacente, funcionário treinado, "
            "Carnaval causa 1-3°C max. Acionar concessionária/estabilizador."
        ),
        "decisao": "SUGGEST",
        "score": "10/10",
        "status": "✅ PASS",
    },

    # Nível 5 — Paradoxo
    {
        "nivel": "Nível 5",
        "cenario": "Armadilha Dupla — Paradoxo (Degelo + Relé Travado)",
        "ativo": "FREEZER-07",
        "cliente": "Laticínios São Jorge, Curitiba",
        "descricao": (
            "+5°C num freezer de laticínios! Parece catastrófico.\n"
            "ARMADILHA 1: Panicar (+5°C! Produtos vão estragar!)\n"
            "→ Errado: é degelo programado, produtos já na backup.\n"
            "ARMADILHA 2: Relaxar ('é só um degelo')\n"
            "→ TAMBÉM errado: degelo de 30min já dura 75min!\n"
            "  O relé do aquecedor está TRAVADO em ON.\n"
            "  Risco de dano ao evaporador, não aos produtos."
        ),
        "dados": (
            "• Temperatura: +5°C (alarme: -11°C) ← CRÍTICO aparente\n"
            "• DEGELO PROGRAMADO:\n"
            "  - Início: 13:00, duração programada: 30 min\n"
            "  - Tempo decorrido: 75 min (OVERSHOOT!)\n"
            "  - Operador Marcos confirmou às 12:55\n"
            "  - Produtos realocados para BACKUP-UNIT-03\n"
            "• RELÉ TRAVADO:\n"
            "  - Aquecedor: 100% contínuo (deveria pulsar 50-70%)\n"
            "  - Temp. aquecedor: 45°C (limite: 35°C)\n"
            "  - Ciclos do relé hoje: 1 (normal: 5-8)\n"
            "  - Serpentina/coil: 38°C (dano >50°C)\n"
            "• Compressor: desligado (normal em degelo)\n"
            "• Backup: -19.5°C (produtos seguros)"
        ),
        "deve_fazer": (
            "✓ NÃO panicar: reconhecer degelo programado\n"
            "✓ Notar que produtos estão seguros (backup)\n"
            "✓ DETECTAR overshoot (75 vs 30 min)\n"
            "✓ Identificar relé do aquecedor TRAVADO\n"
            "✓ Entender: risco é no EQUIPAMENTO, não produtos\n"
            "✓ Recomendar desligar o AQUECEDOR (não o freezer)\n"
            "✓ Religar compressor depois"
        ),
        "resultado_llm": (
            "Degelo programado + relé travado. Produtos seguros (backup -19.5°C). "
            "Overshoot de 45 min — relé ON contínuo → aquecedor a 45°C → serpentina 38°C → "
            "risco de dano ao evaporador. Ação: desligar aquecedor, não o equipamento. "
            "Atualizar ticket existente para URGENTE."
        ),
        "decisao": "SUGGEST (HIGH)",
        "score": "9/10",
        "status": "✅ PASS",
    },
]

# Escrever dados
for row_idx, s in enumerate(scenarios, 2):
    nivel = s["nivel"]
    data = [
        nivel,
        s["cenario"],
        s["ativo"],
        s["cliente"],
        s["descricao"],
        s["dados"],
        s["deve_fazer"],
        s["resultado_llm"],
        s["decisao"],
        s["score"],
        s["status"],
    ]
    for col_idx, val in enumerate(data, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=val)
        cell.font = BODY_FONT
        cell.alignment = WRAP
        cell.border = THIN_BORDER
        if col_idx == 1:
            cell.fill = LEVEL_FILLS.get(nivel, PatternFill())
            cell.font = Font(name="Calibri", bold=True, size=10)
        if col_idx == 11 and "PASS" in val:
            cell.fill = PASS_FILL
            cell.font = PASS_FONT

# Altura das linhas (auto)
for row_idx in range(2, len(scenarios) + 2):
    ws.row_dimensions[row_idx].height = 120

# Congelar cabeçalho
ws.freeze_panes = "A2"

# ============================================================
# ABA 2: CAPACIDADES DEMONSTRADAS
# ============================================================
ws2 = wb.create_sheet("Capacidades CAOS")

capabilities = [
    ["Capacidade do CAOS", "Cenário que Demonstra", "Descrição para Vendas"],
    [
        "Diagnóstico Autônomo de Causa Operacional",
        "Nível 0 — FREEZER-01",
        "O CAOS identifica que excesso de aberturas de porta causou a elevação de temperatura, evitando chamado técnico desnecessário (economia ~R$350/evento).",
    ],
    [
        "Detecção de Abuso Operacional",
        "Nível 0.5 — FREEZER-02",
        "Detecta padrão de desligamento noturno pelo operador. Calcula que o 'economia' na verdade GASTA mais energia e coloca produtos em risco sanitário.",
    ],
    [
        "Detecção de Falso Positivo (Sensor Defeituoso)",
        "Nível 1 — FREEZER-03",
        "Cruza dados de múltiplos sensores e identifica que o alarme é falso positivo causado por sensor descalibrado. Evita chamado técnico e pânico desnecessário.",
    ],
    [
        "Diagnóstico Multi-Fator",
        "Nível 2 — FREEZER-04",
        "Analisa simultaneamente compressor, vedação e ambiente para explicar desvio de 12°C que nenhum fator isolado justifica. Recomenda ação para cada fator.",
    ],
    [
        "Root Cause Analysis (Cadeia Causal)",
        "Nível 3 — FREEZER-05",
        "Diferencia CAUSA de SINTOMA: o compressor sobrecarregado é consequência do vazamento de gás, não a causa. Recomenda ação emergencial na causa raiz mesmo com manutenção agendada.",
    ],
    [
        "Filtragem de Ruído (Red Herrings)",
        "Nível 4 — FREEZER-06",
        "Com 4 pistas falsas (manutenção OK, barulho adjacente, funcionário novo, Carnaval), identifica a causa real (queda de tensão) sem cair em armadilhas informacionais.",
    ],
    [
        "Raciocínio Paradoxal (Nuance Dupla)",
        "Nível 5 — FREEZER-07",
        "Navega uma armadilha dupla: NÃO panicar (é degelo programado) e NÃO descuidar (relé travado = risco no equipamento). Recomenda ação cirúrgica no aquecedor.",
    ],
    [
        "Guardrails Inteligentes (Context-Aware)",
        "Nível 5 — FREEZER-07",
        "O sistema de segurança reconhece contexto: durante degelo com produtos realocados, escalona ao invés de bloquear, permitindo ação proativa.",
    ],
    [
        "Fusão Híbrida (LLM + Código)",
        "Todos os Cenários",
        "O CAOS combina análise algorítmica (rápida, consistente) com raciocínio LLM (nuançado, contextual). Código garante piso de segurança, LLM agrega inteligência.",
    ],
    [
        "Auditoria Completa",
        "Todos os Cenários",
        "Cada decisão é rastreável: trace de raciocínio, scores de risco por eixo (físico, financeiro, contratual, comunicação), e justificativa da LLM.",
    ],
]

for row_idx, row_data in enumerate(capabilities, 1):
    for col_idx, val in enumerate(row_data, 1):
        cell = ws2.cell(row=row_idx, column=col_idx, value=val)
        cell.alignment = WRAP
        cell.border = THIN_BORDER
        if row_idx == 1:
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
        else:
            cell.font = BODY_FONT

ws2.column_dimensions["A"].width = 40
ws2.column_dimensions["B"].width = 25
ws2.column_dimensions["C"].width = 80
for r in range(2, len(capabilities) + 1):
    ws2.row_dimensions[r].height = 50
ws2.freeze_panes = "A2"

# ============================================================
# ABA 3: RESUMO VISUAL
# ============================================================
ws3 = wb.create_sheet("Resumo")

summary_headers = ["Nível", "Dificuldade", "Cenário", "Score", "Status"]
for col, h in enumerate(summary_headers, 1):
    cell = ws3.cell(row=1, column=col, value=h)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center")
    cell.border = THIN_BORDER

summary_data = [
    ["Nível 0", "🟢 Básico", "Abertura Excessiva de Portas", "6/6", "✅"],
    ["Nível 0.5", "🟢 Básico", "Desligamento Noturno", "6/6", "✅"],
    ["Nível 1", "🟡 Intermediário", "Sensor Defeituoso (Armadilha)", "6/6", "✅"],
    ["Nível 2", "🟡 Intermediário", "Múltiplas Falhas Simultâneas", "10/10", "✅"],
    ["Nível 3", "🟠 Avançado", "Falha em Cascata (Root Cause)", "10/10", "✅"],
    ["Nível 4", "🔴 Expert", "Red Herrings + Info Overload", "10/10", "✅"],
    ["Nível 5", "🟣 Paradoxo", "Armadilha Dupla (Degelo + Relé)", "9/10", "✅"],
]

for row_idx, row_data in enumerate(summary_data, 2):
    for col_idx, val in enumerate(row_data, 1):
        cell = ws3.cell(row=row_idx, column=col_idx, value=val)
        cell.font = Font(name="Calibri", size=11, bold=(col_idx in (1, 4, 5)))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
        if col_idx == 1:
            cell.fill = LEVEL_FILLS.get(val, PatternFill())

ws3.column_dimensions["A"].width = 12
ws3.column_dimensions["B"].width = 22
ws3.column_dimensions["C"].width = 42
ws3.column_dimensions["D"].width = 10
ws3.column_dimensions["E"].width = 10

# Total
total_row = len(summary_data) + 2
ws3.cell(row=total_row, column=1, value="TOTAL").font = Font(name="Calibri", bold=True, size=12)
ws3.cell(row=total_row, column=4, value="57/58").font = Font(name="Calibri", bold=True, size=12, color="006100")
ws3.cell(row=total_row, column=4).fill = PASS_FILL
ws3.cell(row=total_row, column=5, value="98.3%").font = Font(name="Calibri", bold=True, size=12, color="006100")
ws3.cell(row=total_row, column=5).fill = PASS_FILL
ws3.freeze_panes = "A2"

# ============================================================
# SALVAR
# ============================================================
output_path = "/Users/lucasseverino/Documents/VIVA/AGENTES/caos_agent/CAOS_Agent_Cenarios_de_Teste.xlsx"
wb.save(output_path)
print(f"✅ Excel salvo em: {output_path}")
