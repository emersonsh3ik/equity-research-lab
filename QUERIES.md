# Query Playbook — Equity Research Lab

Coleção de queries SQL úteis pra interrogar o banco de dados do projeto.

## Como rodar

### Modo simples — query pré-definida

```bash
python src/q.py today           # Top 20 sinais de hoje
python src/q.py insider-flags   # Tickers com insider selling pesado
python src/q.py growth          # Tickers com crescimento >20%
```

### Listar todas as queries disponíveis

```bash
python src/q.py --list
```

### SQL custom

```bash
python src/q.py --sql "SELECT ticker, close FROM signals WHERE rsi14 > 65 LIMIT 5"
```

### Histórico de um ticker específico

```bash
python src/q.py ticker-history --ticker EXTR
```

### Modo interativo (REPL SQL)

```bash
python src/q.py --interactive
```

Aí você digita SQL livremente. `quit` ou Ctrl+D pra sair.

---

## Queries pré-definidas (em ordem de utilidade diária)

### 📊 Daily review

**`today`** — Top 20 do dia (A e B juntos)
**`today-a`** — Só Universo A (mid/large)
**`today-b`** — Só Universo B (small caps)
**`sectors`** — Distribuição setorial do dia
**`valuations-summary`** — Mediana de P/E, EV/EBITDA, etc. dos sinais de hoje

### 🚨 Red flags

**`insider-flags`** — Tickers com insider selling pesado (>$1M vendido)
**`insider-buys`** — Tickers com insider BUYING (sinal positivo raro)

### 💎 Filtros qualitativos

**`cheap`** — Forward P/E < 15
**`growth`** — Revenue growth > 20% YoY
**`quality`** — FCF positivo + ROE > 15%
**`analyst-bullish`** — Upside > 20% pelo target mean

### 📅 Catalisadores

**`earnings-soon`** — Earnings nos próximos 30 dias

### 🔄 Histórico

**`history`** — Quantos sinais por dia
**`recent`** — Últimos 7 dias detalhados
**`repeats`** — Tickers que apareceram em múltiplos dias
**`ticker-history --ticker XXX`** — Histórico de um ticker específico

### 📈 Performance (precisa de outcomes coletados ao longo do tempo)

**`outcomes`** — Hit rate por janela (7d/30d/60d/90d)

### 🧪 Macro

**`macro-context`** — Tickers do dia com beta e perfil de risco

### 🔧 Sistema

**`db-stats`** — Estatísticas do banco

---

## Exemplos de queries SQL úteis (pra REPL)

### Tickers que ESTAVAM nos sinais de ontem mas NÃO estão hoje

```sql
SELECT ticker
FROM signals
WHERE signal_date = (SELECT MAX(signal_date) FROM signals WHERE signal_date < (SELECT MAX(signal_date) FROM signals))
  AND ticker NOT IN (
    SELECT ticker FROM signals WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
  );
```

### Sinais com TODAS as condições de "boa entrada"

```sql
SELECT ticker, sector, close, pe_forward, revenue_growth_yoy, free_cash_flow, insider_net_value_6m
FROM signals
WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
  AND pe_forward BETWEEN 10 AND 25       -- valuation razoável
  AND revenue_growth_yoy > 0.10           -- crescimento real
  AND free_cash_flow > 0                  -- gera caixa
  AND (insider_net_value_6m IS NULL OR insider_net_value_6m > -1000000)  -- sem insider selling pesado
  AND composite_score > 130
ORDER BY composite_score DESC;
```

### Setor com melhor score médio nos últimos 5 dias

```sql
SELECT
    sector,
    COUNT(*) AS n_aparicoes,
    ROUND(AVG(composite_score), 1) AS avg_score
FROM signals
WHERE signal_date >= (SELECT MAX(signal_date) FROM signals) - INTERVAL '5 days'
GROUP BY sector
ORDER BY avg_score DESC;
```

### Tickers com cobertura analítica forte (8+ analistas) E rating bullish (<2.0)

```sql
SELECT ticker, sector, analyst_count, ROUND(analyst_rating, 2) AS rating, target_upside_pct
FROM signals
WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
  AND analyst_count >= 8
  AND analyst_rating < 2.0
ORDER BY analyst_rating ASC;
```

### Tickers caros (P/E forward > 30) MAS com beats consistentes

Esse padrão é interessante porque o múltiplo alto se justifica se a empresa sempre supera:

```sql
SELECT ticker, sector, ROUND(pe_forward, 1) AS pe_fwd, earnings_beats_4q AS beats, ROUND(earnings_avg_surprise_pct, 1) AS surprise_pct
FROM signals
WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
  AND pe_forward > 30
  AND earnings_beats_4q >= 3
ORDER BY earnings_avg_surprise_pct DESC;
```

### Tickers com PIORES sinais combinados

```sql
SELECT
    ticker, sector,
    ROUND(pe_forward, 1) AS pe_fwd,
    insider_n_sells_6m AS sells,
    ROUND(insider_net_value_6m / 1e6, 1) AS net_value_mm,
    analyst_downgrades_90d AS down_90d
FROM signals
WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
  AND insider_net_value_6m < -5000000   -- mais de $5M vendido
  AND (analyst_downgrades_90d > 0 OR pe_forward > 50)
ORDER BY insider_net_value_6m ASC;
```

---

## Schema de referência

Pra ver todas as colunas disponíveis na tabela `signals`:

```bash
python src/q.py --sql "PRAGMA table_info(signals)"
```

Tabelas existentes:
- `signals` — sinais do screener (com fundamentals + insider depois do enrich)
- `analyses_llm` — análises geradas pelo Cowork (preenchido via `process_brief.py`)
- `signal_outcomes` — performance dos sinais 7d/30d/60d/90d depois
- `universe` — todos os tickers NYSE+NASDAQ + setor
- `prices_daily` — OHLCV histórico (vazio por enquanto, o screener não popula)
- `fundamentals_quarterly` — earnings históricos (vazio)
- `events`, `insider_transactions`, `short_interest` — outros (vazios)
- `runs` — log de execuções

Views:
- `v_hit_rate_by_window` — hit rate por janela (consolidado dos outcomes)
- `v_performance_by_sector` — performance por setor
- `v_open_signals` — sinais ainda dentro da janela de 90d
