# Equity Research Lab — Arquitetura Completa

Sistema de descoberta sistemática de oportunidades em ações americanas (NYSE + NASDAQ, ~6.000 ativos), com aprendizado composto ao longo do tempo via banco de dados próprio.

**Objetivo:** identificar empresas com alta probabilidade de valorização, registrar a tese e o setup, acompanhar o que aconteceu, e iterar sobre o processo medindo edge real.

**NÃO é:** bot de trading. Você não está operando dinheiro nesta fase (e talvez nunca, ou só quando o sistema provar edge consistente).

---

## Princípios de design

1. **Cobertura total não-negociável.** Filtros quantitativos cobrem 6.000 ações por <$0,01/dia. LLM cobre apenas top 10-20 que passaram dos filtros.
2. **Memória persistente é o ativo.** Cada sinal, cada análise, cada outcome vai pro banco. Em 6 meses você terá dados que ninguém mais tem do SEU processo.
3. **Determinístico onde der, qualitativo onde precisar.** Quant: filtros, scores, outcomes. LLM: tese narrativa, bear case, contexto qualitativo.
4. **Verificação cruzada obrigatória.** Sempre 2+ fontes para dados quantitativos. LLM com retrieval estruturado, não scraping de notícias.
5. **Falha barata.** Sinais errados custam zero (sem dinheiro). Aprendizado vem do volume + tracking.
6. **Reversibilidade.** Cada componente roda standalone. Trocar yfinance por Polygon ou Claude por GPT é mudança de arquivo, não de arquitetura.

---

## Arquitetura em 5 camadas

```
┌──────────────────────────────────────────────────────────────────┐
│                      LAYER 1 — DATA INGESTION                    │
│  Diário, 5h-7h da manhã (antes do mercado abrir)                 │
│                                                                  │
│  • Universe: NYSE + NASDAQ tickers (~6.000)                      │
│  • Daily OHLCV: yfinance bulk download (~30 min)                 │
│  • Fundamentos básicos: market cap, sector, industry             │
│  • Calendário: earnings, dividends, splits                       │
│  • Notícias/catalisadores: NewsAPI ou agregador                  │
│                                                                  │
│  → Salva em Parquet + atualiza DuckDB                            │
│  Custo: $0 (yfinance grátis) ou ~$30/mês (Polygon flat files)    │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              LAYER 2 — QUANT SCREENER + RANKING                  │
│  Roda imediatamente após Layer 1, ~5-10 min                      │
│                                                                  │
│  FILTROS BÁSICOS (reduz 6.000 → ~2.500):                         │
│  • Market cap >= $200M                                           │
│  • Volume médio 20d >= 200k shares                               │
│  • Preço >= $5 (sem penny stocks)                                │
│                                                                  │
│  FILTROS TÉCNICOS (reduz ~2.500 → ~200-500):                     │
│  • RSI(14) entre 35-70 (zona operável)                           │
│  • Preço acima da MM50 OU teste de MM200 ascendente              │
│  • Não dentro de 5% da máxima de 52 semanas (evita topo)         │
│  • Não em queda livre (preço > 110% da mínima 52w)               │
│                                                                  │
│  RANKING MULTIFATORIAL (top 200 → top 20):                       │
│  • Momentum score 30% (preço vs MAs, RSI, força relativa SPY)    │
│  • Quality score 25% (margens, ROIC, no recent miss)             │
│  • Catalyst proximity 20% (earnings 7-30d, insider buying)       │
│  • Value score 15% (P/E vs setor, P/B razoável)                  │
│  • Setup score 10% (padrões gráficos, volume confirmando)        │
│                                                                  │
│  → Top 20 candidatos do dia salvos em DuckDB                     │
│  Custo: $0 (Python local roda em segundos)                       │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  LAYER 3 — LLM DEEP ANALYSIS                     │
│  Roda nos top 10 do ranking diário, ~10-15 min                   │
│                                                                  │
│  Para cada ticker do top 10:                                     │
│  • Aplicar Prompt v3 (versão estruturada com saída JSON)         │
│  • Bear case OBRIGATÓRIO em cada análise                         │
│  • Verificação cruzada de dados conflitantes                     │
│  • Score final 1-10 + confidence level                           │
│                                                                  │
│  Output: JSON estruturado por análise + markdown narrativo       │
│                                                                  │
│  Custo: ~$5-15/dia ($150-450/mês) com Claude Sonnet via API      │
│  Alternativa: scheduled tasks do Cowork (sem custo adicional     │
│  se já tem Claude Enterprise) com limitações de throughput       │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  LAYER 4 — DATABASE + TRACKING                   │
│  DuckDB local, ~7-15 GB após 12 meses de cobertura completa      │
│                                                                  │
│  Tabelas principais:                                             │
│  • universe — todos os tickers NYSE+NASDAQ                       │
│  • prices_daily — OHLCV histórico                                │
│  • fundamentals_quarterly — earnings, margens, balanço           │
│  • signals — cada sinal gerado pelo screener                     │
│  • analyses_llm — outputs estruturados do LLM                    │
│  • signal_outcomes — preço 7d, 30d, 60d, 90d após sinal          │
│  • events — earnings, M&A, dividendos, splits, IPO               │
│                                                                  │
│  Job de outcome tracking roda diariamente:                       │
│  → Para cada sinal de 7/30/60/90 dias atrás, mede preço atual    │
│  → Calcula R-multiple, hit rate, edge cumulativa                 │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    LAYER 5 — DELIVERY                            │
│                                                                  │
│  • Relatório diário em Markdown (top 5 + watchlist mudanças)     │
│  • Dashboard Streamlit (opcional, semana 6+):                    │
│    - Hit rate por setor                                          │
│    - R-multiple histórico                                        │
│    - Setores com mais signals                                    │
│    - Comparação vs SPY                                           │
│  • Telegram bot (opcional, semana 8+) com /top5 e /track         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Stack tecnológico — decisões e justificativas

| Componente | Escolha | Por quê | Alternativa | Quando trocar |
|---|---|---|---|---|
| **Linguagem** | Python 3.11+ | Padrão de quant finance, libs maduras | R, Julia | Provavelmente nunca |
| **Data analysis** | **Polars** + DuckDB | Polars 5-10x mais rápido que pandas em DataFrames; DuckDB é SQL analítico de elite | pandas, PostgreSQL | Pandas se você já domina; PG se precisar de concorrência multi-usuário |
| **Database** | **DuckDB** | OLAP local, arquivo único, SQL completo, sem servidor | PostgreSQL, SQLite | SQLite para mobile-first; PG para multi-user |
| **Daily prices** | yfinance (grátis) → Polygon ($30/mês quando crescer) | yfinance funciona; Polygon é mais robusto | Tiingo, Alpha Vantage, IEX Cloud | Quando hit rate limit do yfinance ou precisar de dados intraday |
| **Fundamentos** | yfinance + SimFin (free tier) | Cobertura básica suficiente | Financial Modeling Prep ($14/mês), Polygon | Quando precisar de filings históricos completos |
| **Notícias** | NewsAPI ($0-449/mês), Benzinga ($60/mês) | Coverage US sólido | Reuters/Bloomberg (caro), scraping (frágil) | Quando precisar de news com latência <5 min |
| **Earnings calendar** | yfinance + Earnings Whispers (scraping) | Grátis | Estimize ($50/mês) | Se precisar de whisper numbers consistentes |
| **Insider transactions** | OpenInsider (scraping grátis) | SEC Form 4 agregada | Whale Wisdom ($20/mês) | Quando precisar de 13F holdings também |
| **Short interest** | FINRA bi-monthly (grátis) | Atrasado mas oficial | Ortex ($300/mês) | Quando trade ativamente |
| **LLM** | **Claude Sonnet via API** | Melhor para análise estruturada; saída JSON confiável | GPT-4o, Gemini Pro | Diversificar provedores para resiliência (recomendado a partir de mês 6) |
| **Scheduler** | cron (Linux) ou Task Scheduler (Mac) | Padrão, free | Airflow (overkill agora), Cowork scheduled tasks | Se precisar de orquestração multi-step |
| **Storage** | Parquet files + DuckDB | Compressão 5-10x, query rápida | CSV (não escalável), Arrow IPC | Provavelmente nunca |
| **Dashboard** | Streamlit (opcional, fase 2) | Python puro, deploy fácil | Plotly Dash, Grafana | Se quiser produção corporate |
| **Versionamento** | Git + GitHub privado | Padrão | GitLab, Bitbucket | Nunca |
| **Notebook exploratório** | Jupyter / VS Code | Padrão dev quant | Hex.tech, Deepnote | Se for colaborar |
| **Secret management** | .env + python-dotenv | Simples | AWS Secrets Manager, 1Password CLI | Se for produção compartilhada |

---

## Duas opções de deployment (escolha depois)

### Opção A — 100% local (recomendada se você tem laptop bom)

**Stack físico:**
- Seu laptop ou mini-PC sempre ligado, ou
- VPS Linux $5-20/mês (DigitalOcean, Hetzner, Linode)

**Fluxo diário:**
- Cron às 5h: Layer 1 (data ingestion)
- Cron às 6h: Layer 2 (screener + ranking)
- Cron às 6h30: Layer 3 (LLM via Anthropic API)
- Cron às 7h: Layer 4 (outcome tracking) + Layer 5 (relatório)

**Custos:**
- VPS: $5-20/mês (opcional)
- Anthropic API: $150-450/mês uso pesado, ou $30-60/mês com Haiku para batch + Sonnet só nos finalistas
- Polygon (eventualmente): $30/mês
- **Total: $30-500/mês conforme uso**

**Vantagens:**
- Controle total
- Backtest histórico fácil
- Sem limitações de plataforma

**Desvantagens:**
- Setup técnico inicial (Python, libs, cron)
- Você é responsável pela disponibilidade
- Debugging local

### Opção B — Híbrida: Python local + Cowork scheduled tasks

**Stack físico:**
- Layer 1, 2, 4: rodam no seu laptop via cron
- Layer 3 (LLM): rodam como scheduled task do Cowork lendo arquivos da pasta mounted
- Layer 5: relatórios montados pelo Cowork na pasta

**Custos:**
- Anthropic API: $0 (uso seu Claude Enterprise via Cowork)
- VPS: $0 (roda no laptop)
- **Total: $0-50/mês**

**Vantagens:**
- Sem custo adicional de API (Cowork já incluído)
- Setup mais simples (não precisa gerenciar chaves API)
- Reports aparecem na sua pasta selecionada automaticamente

**Desvantagens:**
- Throughput limitado (não dá pra rodar análise de 100 ações por minuto)
- Menos flexível pra rodar análises sob demanda fora da rotina
- Dependência da janela horária do scheduled task

---

## Cost-benefit honesto

| Item | Tempo (semana) | Custo (mês) | Valor entregue |
|---|---|---|---|
| Layer 1 + 2 funcionando | ~8-12 horas para setup | $0-30 | Cobertura sistemática diária 6.000 ações — algo que retail RARAMENTE tem |
| + Layer 3 (LLM) | +6-10 horas | +$50-450 | Análise narrativa estruturada nos top candidates — multiplica capacidade analítica em 10x |
| + Layer 4 (database + tracking) | +6-8 horas | +$0 | Memória composta — o ativo de longo prazo do projeto |
| + Layer 5 (delivery) | +3-5 horas | +$0 | Conveniência operacional |
| Backtest framework | +20-30 horas | $0 | Capacidade de validar mudanças sem operar real |

**Total setup inicial: ~50-65 horas em 6-8 semanas.**

**Custo operacional contínuo: $0-500/mês.**

**Valor após 12 meses de operação:**
- Base de dados de 250+ dias de cobertura completa NYSE+NASDAQ
- ~5.000 sinais gerados com outcomes medidos
- Hit rate, R-multiple, e setup-effectiveness conhecidos por setor
- Conhecimento de mercado que NENHUM curso ou newsletter entrega

---

## O que CADA layer endereça dos 7 furos restantes

| Furo original | Como o layer endereça |
|---|---|
| 1. Edge problem | Mitigado: você não está disputando alpha financeiro, está construindo edge de PESQUISA via cobertura + memória |
| 2. Look-ahead bias | Layer 4: tradelog registra sinais NO MOMENTO em que foram gerados; outcomes medidos depois sem alterar a entrada |
| 3. Sem backtest | Layer 5/Fase 2: backtest framework permite simular o sistema em qualquer janela histórica |
| 4. Cost vs scale | Resolvido: Layer 1 grátis cobre todos; Layer 3 só nos finalistas |
| 5. Hallucination | Layer 1 dados estruturados via API; LLM em Layer 3 usa retrieval, não scraping; Layer 4 valida outputs antes de inserir |
| 6. Feedback loop | Layer 4: outcome tracking automático 7/30/60/90d; relatórios mostram hit rate evoluindo |
| 7. Portfolio thinking | Layer 5: relatórios de exposição setorial, correlação dos signals, concentração |
| 8. Behavioral risk | Sem dinheiro real = sem risco emocional; relatório diário é leitura, não comando de operação |
| 9. Legal/regulatório | Uso próprio (não compartilhado) = sem necessidade CNPI/CVM |
| 10. Reinventar a roda | Não estamos competindo com TradingView; estamos construindo algo que TradingView NÃO faz: memória persistente de análises narrativas com tracking de outcomes ao longo do tempo |

---

## Métricas de sucesso (o que medir e quando)

### Semana 1-2 (após Layer 1+2 funcionar)
- ✓ Dados de 5+ dias consecutivos para 6.000 ações
- ✓ Top 20 ranking gerado todo dia sem erros
- ✓ Tempo de execução < 1 hora

### Mês 1 (após Layer 3 funcionar)
- ✓ 30 dias de análises LLM nos top 10 diários
- ✓ ~300 sinais registrados no banco
- ✓ Análise qualitativa: as recomendações fazem sentido?

### Mês 3
- ✓ Outcomes de 60-90 dias para os primeiros sinais
- ✓ Hit rate medido pela primeira vez (esperado: 30-50%)
- ✓ R-multiple médio medido
- ✓ Identificação dos TIPOS de setup que funcionam vs não

### Mês 6
- ✓ 500+ sinais com outcomes completos
- ✓ Edge estatisticamente significativo medido
- ✓ Backtest framework rodando
- ✓ Decisão informada: o sistema tem edge real? Iterar ou pivotar?

### Mês 12
- ✓ Base de dados de 5.000+ sinais
- ✓ Modelo de predição treinado em dados reais (opcional, fase avançada)
- ✓ Decisão: começar a operar com tamanho pequeno? Continuar paper? Pivotar?

---

## O que NÃO entra no escopo (deliberadamente)

- **Execução de ordens.** Sem trading real nesta fase. Se mais tarde quiser, faz integração broker (Interactive Brokers tem API).
- **Real-time data.** Sistema é EOD (end-of-day). Não tenta operar intraday.
- **Day trading patterns.** Setups são swing (3 dias a 3 meses), não scalping.
- **Opções e derivativos.** Foco em ações. Opções podem entrar depois como source de sinais (unusual activity).
- **Mercado brasileiro nesta fase.** Foco NYSE+NASDAQ. B3 fica para depois (libs e fontes são diferentes).
- **Newsletter / produto comercial.** Uso próprio. Compartilhamento requer adequação regulatória.

---

## Próximos arquivos no projeto

1. `02_screener.py` — Layer 1+2 funcionando (você vai poder rodar essa semana)
2. `03_database_init.py` — Schema DuckDB completo
3. `04_prompt_v3_daily.md` — Prompt LLM com output JSON estruturado
4. `05_README_setup.md` — Step-by-step de instalação
5. `06_roadmap_8_semanas.md` — Cronograma de implementação

Depois de tudo isso pronto, decidimos juntos: opção A (100% local) ou opção B (híbrido com Cowork).
