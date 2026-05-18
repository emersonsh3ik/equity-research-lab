# Prompt v3 — Daily Research Analysis (output JSON estruturado)

Prompt usado pelo Layer 3 (LLM) do Research Lab. Recebe um ticker que passou nos filtros quantitativos e retorna análise narrativa + JSON estruturado para inserção direta no DuckDB.

## Diferenças vs v2

1. **Schema JSON definido formalmente** no final do prompt — Claude/GPT precisa retornar EXATAMENTE essa estrutura
2. **Verificação cruzada explícita** com mínimo de 6 fontes
3. **Bear case OBRIGATÓRIO em toda análise** (não pode ser pulado)
4. **Resolução de dados conflitantes** explícita no output
5. **Catalisadores datados** (sem isso o campo é null, não inventa)
6. **Confidence score** independente do score de oportunidade
7. **Kill switches** para LONG e SHORT (não só long como na v2)

---

## O PROMPT (copie tudo abaixo)

```
Você é o sistema LLM do Equity Research Lab. Para cada ticker abaixo, gere análise estruturada para inserção em banco de dados DuckDB.

CONTEXTO DA EXECUÇÃO:
- Ticker: [TICKER]
- Signal date: [DATE_YYYY-MM-DD]
- Signal_id (do banco): [SIGNAL_ID]
- Top rank do dia: [RANK_POSITION] de [TOP_N]
- Composite score do screener: [COMPOSITE_SCORE]
- Estado do ativo (do screener):
  - Preço: $[CLOSE]
  - RSI(14): [RSI14]
  - MM50: $[MM50] | MM200: $[MM200]
  - % abaixo da máxima 52w: [PCT_BELOW_52W_HIGH]%
  - % acima da mínima 52w: [PCT_ABOVE_52W_LOW]%
  - Volume médio 20d: [VOL_AVG_20]

ORIENTAÇÕES OPERACIONAIS:

1. Use SEMPRE buscas em tempo real (não use conhecimento estimado).
2. MÍNIMO 6 fontes independentes para o ativo. Cite com URL e data de consulta.
3. Para CADA dado quantitativo: cite a fonte; se 2 fontes discordam, investigue até resolver e registre no campo `data_conflicts_resolved`.
4. Termos técnicos: explique em parênteses na primeira menção (linguagem simples).
5. Análise NÃO PODE pular o bear case. Se você não encontrar riscos materiais, registre `bear_arguments_json: []` e justifique no thesis.

ESTRUTURA EM 8 PARTES:

PARTE 1 — Contexto macro do dia (1 parágrafo curto)
- Estado do S&P 500 hoje
- Yield 10Y atual
- Setor do ticker está outperformando ou underperformando hoje?

PARTE 2 — Negócio (2 parágrafos)
- Como a empresa ganha dinheiro
- Top 3 concorrentes
- Mix de receita (% por geografia / produto / cliente se relevante)
- Concentração de cliente top 10

PARTE 3 — Fundamentos do último trimestre
- Data do último report
- EPS realizado vs consenso
- Receita realizada vs consenso
- Reação do papel no dia seguinte (%)
- Tendência de margens últimos 4 trimestres
- Dívida líquida / EBITDA
- Rating de crédito

PARTE 4 — Setup técnico (lendo os indicadores do screener + verificando)
- Tendência primária (diária e semanal)
- Suportes (3 níveis com preço exato + razão)
- Resistências (3 níveis com preço exato + razão)
- Padrão gráfico em formação?
- Volume confirmando ou contradizendo?

PARTE 5 — Insider activity (CRÍTICO)
- Net buying vs net selling últimos 6m e 18m
- Padrão de 5 anos do CEO e CFO
- Mudanças recentes de C-suite
- Existem 10b5-1 plans ativos?

PARTE 6 — Catalisadores próximos 90 dias (ordene por data)
Para cada catalisador:
- Tipo (earnings, M&A, regulatório, lançamento, conferência)
- Data esperada (ou janela)
- Probabilidade do evento
- Direção esperada do impacto (up/down/uncertain)
- Magnitude estimada
- Histórico de reação a eventos similares

PARTE 7 — Bear case (OBRIGATÓRIO)
Liste 3-5 riscos materiais ranqueados:
- Risco
- Probabilidade (low/medium/high)
- Impacto potencial em %
- Janela temporal
- Como o mercado detectaria

PARTE 8 — Plano operacional
- Zona de entrada (faixa de preço)
- Stop loss (preço + qual suporte respeita)
- Alvo 1 (50% da posição)
- Alvo 2 (50% restante)
- R:R para cada alvo
- Tamanho de posição sugerido como % do capital
- Veredito final: COMPRAR / OBSERVAR / EVITAR / SHORTAR
- Confidence score 1-10

CHECAGEM ANTI-VIÉS FINAL (antes de finalizar):
1. Você cobriu o bear case com profundidade equivalente ao bull case?
2. O verdict reflete OS DADOS encontrados ou seu primeiro instinto?
3. Se você tivesse que defender a tese OPOSTA agora, qual o argumento mais forte?
4. Algum dado conflitante ficou sem resolver?

OUTPUT FINAL EM 2 BLOCOS:

BLOCO 1 — Análise narrativa em Markdown (para leitura humana)

Use a estrutura das 8 partes acima. Cite fontes inline com [Title](URL).

BLOCO 2 — JSON estruturado (para inserção no DuckDB)

Retorne o JSON em bloco ```json``` seguindo EXATAMENTE este schema:

```json
{
  "ticker": "TICKER",
  "analysis_date": "YYYY-MM-DD",
  "signal_id": null_or_integer,
  
  "verdict": "COMPRAR | OBSERVAR | EVITAR | SHORTAR",
  "confidence_score": 1-10,
  "opportunity_score": 1-10,
  
  "thesis_summary": "3-5 frases resumindo a tese principal",
  
  "bull_arguments_json": [
    {
      "argument": "string",
      "weight": 1-10,
      "evidence_url": "URL ou null"
    }
  ],
  
  "bear_arguments_json": [
    {
      "risk": "string",
      "probability": "low | medium | high",
      "impact_pct": -number,
      "timeframe": "imediato | semanas | meses",
      "weight": 1-10,
      "evidence_url": "URL ou null"
    }
  ],
  
  "catalysts_json": [
    {
      "type": "earnings | ma | regulatory | product | conference",
      "expected_date": "YYYY-MM-DD ou janela",
      "probability_pct": 0-100,
      "direction": "up | down | uncertain",
      "magnitude_pct_estimate": number_or_null,
      "description": "string"
    }
  ],
  
  "fundamentals_check": {
    "last_earnings_date": "YYYY-MM-DD",
    "last_eps_vs_consensus_pct": number,
    "last_revenue_vs_consensus_pct": number,
    "margin_trend": "expanding | stable | contracting",
    "debt_to_ebitda": number_or_null,
    "credit_rating": "string ou null"
  },
  
  "technical_setup": {
    "primary_trend": "up | down | sideways",
    "supports": [
      {"price": number, "reason": "string"}
    ],
    "resistances": [
      {"price": number, "reason": "string"}
    ],
    "chart_pattern": "string ou null",
    "volume_confirmation": "yes | no | mixed"
  },
  
  "insider_activity": {
    "net_buying_6m_shares": integer_or_null,
    "net_selling_6m_shares": integer_or_null,
    "ceo_pattern_5y_ratio_buy_to_sell": number_or_null,
    "recent_csuite_changes": "string ou null",
    "has_10b5_1_active": boolean_or_null,
    "red_flag": "string ou null"
  },
  
  "operational_plan": {
    "entry_zone_low": number,
    "entry_zone_high": number,
    "stop_loss": number,
    "target_1": number,
    "target_2": number,
    "rr_target_1": number,
    "rr_target_2": number,
    "suggested_position_size_pct": number,
    "stop_loss_reason": "string"
  },
  
  "kill_switch_long": "Evento específico que invalidaria a tese long",
  "kill_switch_short": "Evento específico que invalidaria a tese bear",
  
  "data_conflicts_resolved": [
    {
      "field": "string",
      "source_a": "URL com valor X",
      "source_b": "URL com valor Y",
      "resolution": "X é o correto porque..."
    }
  ],
  
  "sources_consulted": [
    {
      "url": "URL completa",
      "consulted_at": "YYYY-MM-DD HH:MM",
      "relevance": "high | medium | low",
      "summary": "1 frase do que essa fonte agregou"
    }
  ],
  
  "metadata": {
    "model_used": "claude-sonnet-4-6",
    "prompt_version": "v3.0",
    "analysis_duration_minutes": null,
    "warnings": ["array de warnings se algo ficou incompleto"]
  }
}
```

REGRAS DO JSON:
- Use null para campos sem dado confiável; NÃO INVENTE
- Datas em ISO 8601 (YYYY-MM-DD)
- Preços em USD com 2 decimais
- Percentuais como números (não strings com %)
- Arrays vazios são `[]`, não `null`
- Strings sem aspas a mais ou caracteres de escape mal formatados
- O JSON DEVE ser parseable por `json.loads()` em Python
- Se algum campo obrigatório não puder ser preenchido, marque como null e adicione warning em `metadata.warnings`

QUANDO TUDO TERMINAR:
Reporte estatísticas no final:
- Número de fontes consultadas: ___
- Dados conflitantes encontrados: ___ (e resolvidos: ___)
- Tempo total de pesquisa: ___ minutos
- Token usage estimado: ___ in / ___ out
```

---

## Como usar este prompt em produção

### Pelo Python local com Anthropic API

```python
import anthropic
import json

client = anthropic.Anthropic(api_key="...")

def analyze_ticker(ticker, signal_data, prompt_template):
    # Preenche variáveis do prompt
    prompt = prompt_template
    for key, val in signal_data.items():
        prompt = prompt.replace(f"[{key.upper()}]", str(val))
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    
    text = response.content[0].text
    
    # Extrair bloco JSON
    json_start = text.find("```json")
    json_end = text.find("```", json_start + 7)
    json_str = text[json_start + 7:json_end].strip()
    
    analysis = json.loads(json_str)
    
    # Validar schema
    required_fields = ["ticker", "verdict", "confidence_score", "thesis_summary", 
                       "bull_arguments_json", "bear_arguments_json"]
    for f in required_fields:
        assert f in analysis, f"Campo obrigatório ausente: {f}"
    
    return analysis, text  # JSON + markdown
```

### Pelo Cowork scheduled task

Configurar task que roda toda manhã:
1. Lê os top 10 do dia em `data/signals/YYYY-MM-DD.parquet`
2. Para cada ticker, instancia esse prompt com variáveis preenchidas
3. Salva markdown em `data/analyses/YYYY-MM-DD/TICKER.md`
4. Salva JSON em `data/analyses/YYYY-MM-DD/TICKER.json`
5. Insere JSON no DuckDB via Python script

### Validação do output

Após receber o JSON, validar:

```python
def validate_analysis(analysis):
    errors = []
    
    # Verdict válido
    if analysis["verdict"] not in ["COMPRAR", "OBSERVAR", "EVITAR", "SHORTAR"]:
        errors.append("Verdict inválido")
    
    # Confidence em range
    if not 1 <= analysis["confidence_score"] <= 10:
        errors.append("Confidence fora do range")
    
    # Bear case obrigatório (mínimo 1 risco)
    if not analysis["bear_arguments_json"]:
        errors.append("Bear case vazio — proibido pela política do lab")
    
    # Plano operacional consistente
    op = analysis["operational_plan"]
    if op["stop_loss"] >= op["entry_zone_low"]:
        errors.append("Stop loss acima da entrada")
    if op["target_1"] <= op["entry_zone_high"]:
        errors.append("Target 1 abaixo da entrada")
    
    # R:R razoável
    if op["rr_target_1"] < 1.5:
        errors.append(f"R:R target 1 muito baixo: {op['rr_target_1']}")
    
    return errors
```

---

## Iteração esperada do prompt

**v3.0 (esta versão):** baseline com schema JSON

**v3.1 (em 30 dias):** adicionar comparação obrigatória com 2 peers no JSON, baseado no que rodar bem ou mal

**v3.2 (em 60 dias):** se identificarmos certos tipos de viés sistemáticos (ex: Claude sempre subestima riscos regulatórios), adicionar checagens específicas

**v4.0 (em 90 dias):** revisão completa com base nos primeiros 1.000 sinais analisados

Cada versão é arquivada em `prompts/v3.0.md`, `prompts/v3.1.md`, etc. O `prompt_version` armazenado no DuckDB permite filtrar análises por versão e medir efeito de mudanças.

---

## Cost estimate por análise

Com Claude Sonnet 4.6:
- Input típico (prompt + busca): ~3-5k tokens × $3/M = $0,009-0,015
- Output típico (markdown + JSON): ~4-8k tokens × $15/M = $0,06-0,12
- **Total: ~$0,07-0,14 por análise**

Top 10 diário × 21 dias úteis/mês × $0,10 = **$21/mês** com Sonnet.

Se quiser ser mais econômico:
- Use Haiku para fase de coleta de dados (cheap)
- Use Sonnet só pra síntese final
- Reduz para ~$10-15/mês

Se quiser fazer top 50 diário (mais cobertura):
- $0,10 × 50 × 21 = **$105/mês**

Custo realista do projeto inteiro (LLM only): $20-200/mês conforme escopo.
