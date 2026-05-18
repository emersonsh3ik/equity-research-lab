# Equity Research Lab — Setup e Operação

Guia step-by-step para colocar o sistema rodando do zero até a primeira análise.

---

## Pré-requisitos

### Hardware
- **Laptop ou PC** com pelo menos 8GB RAM, 50GB livres de SSD
- OU **VPS Linux** ($5-20/mês — DigitalOcean, Hetzner, Linode)

### Software base
- Python 3.11 ou superior
- Git
- Editor de código (VS Code recomendado)

### Contas necessárias (free tiers no início)
- [ ] Anthropic API account com chave criada (https://console.anthropic.com) — para Layer 3
- [ ] yfinance funciona sem conta (free)
- [ ] OPCIONAL: Polygon.io free tier (https://polygon.io)
- [ ] OPCIONAL: NewsAPI free tier (https://newsapi.org)

---

## Instalação inicial (uma vez, ~30 minutos)

### Passo 1 — Criar estrutura de pastas

```bash
# Onde você quiser. Sugestão: ~/equity-research-lab/
mkdir -p ~/equity-research-lab/{src,data,prompts,reports,logs}
cd ~/equity-research-lab

# Inicializa git (vai ser útil pra versionar prompts)
git init
echo "data/" >> .gitignore
echo ".env" >> .gitignore
echo "__pycache__/" >> .gitignore
```

### Passo 2 — Ambiente virtual e dependências

```bash
# Cria virtualenv
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate    # Windows

# Atualiza pip
pip install --upgrade pip

# Dependências core
pip install \
    polars==1.* \
    duckdb==1.* \
    yfinance==0.2.* \
    requests \
    tqdm \
    python-dotenv \
    anthropic \
    pyarrow

# OPCIONAL: ta-lib (mais rápido para indicadores técnicos)
# Mac:
#   brew install ta-lib
#   pip install ta-lib-binary
# Linux:
#   sudo apt-get install -y libta-lib-dev
#   pip install ta-lib-binary
# Windows: baixar TA-Lib binary do site oficial
```

### Passo 3 — Variáveis de ambiente

```bash
# Crie .env na raiz do projeto
cat > .env << EOF
ANTHROPIC_API_KEY=sua-key-aqui
DATA_DIR=./data
LOG_LEVEL=INFO
EOF
```

### Passo 4 — Copiar os arquivos do lab

Coloque na pasta `src/` os arquivos que entreguei:
- `02_screener.py` → `src/screener.py`
- `03_database_init.py` → `src/database_init.py`
- `04_prompt_v3_daily.md` → `prompts/v3.0.md`

### Passo 5 — Inicializar o database

```bash
python src/database_init.py --db-path ./data/research_lab.duckdb
```

Saída esperada:
```
Tabelas criadas: ['analyses_llm', 'events', 'fundamentals_quarterly', 'insider_transactions', 'prices_daily', 'runs', 'short_interest', 'signal_outcomes', 'signals', 'universe']
Views criadas: ['v_hit_rate_by_window', 'v_open_signals', 'v_performance_by_sector']
```

### Passo 6 — Primeira execução do screener (modo SAMPLE para testar)

```bash
# Roda em 100 tickers só pra testar setup
python src/screener.py --sample 100 --top-n 5 --output-dir ./data
```

Tempo esperado: ~2-3 minutos.

Saída esperada:
```
======================================================================
TOP 5 CANDIDATOS DO DIA
======================================================================
ticker   close   rsi14   pct_below_52w_high   momentum_score   setup_score   composite_score
AAPL    180.50   65.2                  3.2               82.1          76.4              79.5
NVDA    420.10   62.8                  4.8               79.5          73.2              76.7
...
```

Se isso funcionou, está pronto para passo 7.

### Passo 7 — Execução completa NYSE+NASDAQ

```bash
# Rebaixa universo + roda em todos os ~6.000 tickers
python src/screener.py --refresh-universe --top-n 20 --output-dir ./data
```

Tempo esperado: **30-60 minutos** na primeira execução (yfinance rate limits).

Próximas execuções (cache de universo): **20-40 minutos.**

---

## Operação diária

### Modo manual (você roda quando quiser)

```bash
cd ~/equity-research-lab
source venv/bin/activate
python src/screener.py --top-n 20 --output-dir ./data
```

Output sempre em:
- `data/prices/YYYY-MM-DD.parquet` (raw prices do dia)
- `data/signals/YYYY-MM-DD.parquet` (top 20 ranqueado)
- `data/research_lab.duckdb` (DB com tudo acumulando)

### Modo automatizado (cron — Linux/Mac)

Edite seu crontab:
```bash
crontab -e
```

Adicione (roda toda manhã às 6h):
```
0 6 * * 1-5 cd ~/equity-research-lab && source venv/bin/activate && python src/screener.py --top-n 20 >> logs/screener_$(date +\%Y\%m\%d).log 2>&1
```

Nota: roda só dias úteis (segunda a sexta) e em horário pre-market.

### Modo automatizado (VPS)

```bash
# No VPS, depois de instalar tudo, mesma config:
crontab -e
# Adicionar mesma linha acima
```

---

## Layer 3 — análise LLM (a partir da semana 2)

Crie `src/llm_analyzer.py`:

```python
import os
import json
import polars as pl
import duckdb
import anthropic
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
DB_PATH = Path(os.getenv("DATA_DIR", "./data")) / "research_lab.duckdb"
PROMPT_PATH = Path("./prompts/v3.0.md")


def load_prompt_template():
    """Carrega o prompt v3 do arquivo."""
    text = PROMPT_PATH.read_text()
    # Extrai apenas o bloco do prompt (entre ``` markers)
    start = text.find("```\nVocê é o sistema LLM")
    if start == -1:
        raise ValueError("Não encontrei o início do prompt")
    end = text.find("```", start + 3)
    return text[start + 3:end]


def analyze_one(ticker, signal_data):
    template = load_prompt_template()
    
    # Preenche variáveis
    prompt = template
    for k, v in signal_data.items():
        prompt = prompt.replace(f"[{k.upper()}]", str(v))
    
    # Chama Claude com web search habilitado
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
        # Anthropic tem tool use para web search; use se disponível
    )
    
    text = response.content[0].text
    
    # Extrai JSON
    json_start = text.find("```json")
    if json_start == -1:
        return None, text, "JSON block não encontrado"
    json_end = text.find("```", json_start + 7)
    json_str = text[json_start + 7:json_end].strip()
    
    try:
        analysis = json.loads(json_str)
        return analysis, text, None
    except json.JSONDecodeError as e:
        return None, text, f"JSON parse error: {e}"


def analyze_today():
    today = datetime.now().strftime("%Y-%m-%d")
    signals_path = Path(f"./data/signals/{today}.parquet")
    
    if not signals_path.exists():
        print(f"Sem sinais para {today}. Roda o screener primeiro.")
        return
    
    signals = pl.read_parquet(signals_path).head(10)  # top 10
    print(f"Analisando top 10 de {today}...")
    
    for row in signals.iter_rows(named=True):
        ticker = row["ticker"]
        print(f"  → {ticker}")
        
        analysis, markdown, error = analyze_one(ticker, row)
        
        # Salva
        analyses_dir = Path(f"./data/analyses/{today}")
        analyses_dir.mkdir(parents=True, exist_ok=True)
        
        if markdown:
            (analyses_dir / f"{ticker}.md").write_text(markdown)
        if analysis:
            (analyses_dir / f"{ticker}.json").write_text(
                json.dumps(analysis, indent=2)
            )
            # Insere no DB
            _insert_analysis(analysis)
        else:
            print(f"    ⚠️ {ticker}: {error}")


def _insert_analysis(analysis):
    conn = duckdb.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            INSERT INTO analyses_llm (
                analysis_id, analysis_date, ticker, verdict, confidence_score,
                thesis_summary, bull_arguments_json, bear_arguments_json,
                catalysts_json, entry_zone_low, entry_zone_high, stop_loss,
                target_1, target_2, rr_target_1, rr_target_2,
                suggested_position_size_pct, kill_switch_long, kill_switch_short,
                raw_json, model_used, prompt_version
            )
            VALUES (
                nextval('analysis_id_seq'), ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                analysis["analysis_date"],
                analysis["ticker"],
                analysis["verdict"],
                analysis["confidence_score"],
                analysis["thesis_summary"],
                json.dumps(analysis["bull_arguments_json"]),
                json.dumps(analysis["bear_arguments_json"]),
                json.dumps(analysis["catalysts_json"]),
                analysis["operational_plan"]["entry_zone_low"],
                analysis["operational_plan"]["entry_zone_high"],
                analysis["operational_plan"]["stop_loss"],
                analysis["operational_plan"]["target_1"],
                analysis["operational_plan"]["target_2"],
                analysis["operational_plan"]["rr_target_1"],
                analysis["operational_plan"]["rr_target_2"],
                analysis["operational_plan"]["suggested_position_size_pct"],
                analysis["kill_switch_long"],
                analysis["kill_switch_short"],
                json.dumps(analysis),
                analysis["metadata"]["model_used"],
                analysis["metadata"]["prompt_version"],
            ],
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    analyze_today()
```

Cron entry para rodar após o screener:
```
30 6 * * 1-5 cd ~/equity-research-lab && source venv/bin/activate && python src/llm_analyzer.py >> logs/llm_$(date +\%Y\%m\%d).log 2>&1
```

---

## Layer 4 — Outcome tracking (a partir da semana 3)

Crie `src/outcome_tracker.py`:

```python
import duckdb
from pathlib import Path
from datetime import datetime, timedelta
import yfinance as yf

DB_PATH = Path("./data/research_lab.duckdb")


def track_outcomes():
    """
    Para cada sinal de 7, 30, 60 ou 90 dias atrás, mede o preço atual
    e calcula o R-multiple.
    """
    conn = duckdb.connect(str(DB_PATH))
    
    # Para cada janela
    for days in [7, 30, 60, 90]:
        target_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        # Sinais daquela data que ainda não têm outcome para esse window
        signals = conn.execute(
            """
            SELECT s.signal_id, s.ticker, s.close as entry_price, s.signal_date,
                   a.stop_loss, a.target_1, a.target_2
            FROM signals s
            LEFT JOIN analyses_llm a ON a.ticker = s.ticker 
                                       AND a.analysis_date = s.signal_date
            WHERE s.signal_date = ?
              AND NOT EXISTS (
                  SELECT 1 FROM signal_outcomes o
                  WHERE o.signal_id = s.signal_id AND o.days_elapsed = ?
              )
            """,
            [target_date, days]
        ).fetchall()
        
        for sig in signals:
            signal_id, ticker, entry_price, signal_date, stop, t1, t2 = sig
            
            # Busca preço atual
            try:
                current = yf.Ticker(ticker).history(period="1d")["Close"].iloc[-1]
            except Exception:
                continue
            
            # Busca high/low desde o sinal
            hist = yf.Ticker(ticker).history(start=signal_date)
            high_since = hist["High"].max() if not hist.empty else current
            low_since = hist["Low"].min() if not hist.empty else current
            
            pct_change = ((current - entry_price) / entry_price) * 100
            
            # Hit checks
            hit_stop = low_since <= stop if stop else False
            hit_t1 = high_since >= t1 if t1 else False
            hit_t2 = high_since >= t2 if t2 else False
            
            # R-multiple (se stop disponível)
            if stop and stop < entry_price:
                r_per_dollar = entry_price - stop
                profit = current - entry_price
                r_multiple = profit / r_per_dollar if r_per_dollar > 0 else None
            else:
                r_multiple = None
            
            # SPY benchmark
            try:
                spy_then = yf.Ticker("SPY").history(
                    start=signal_date, end=signal_date
                )["Close"].iloc[0]
                spy_now = yf.Ticker("SPY").history(period="1d")["Close"].iloc[-1]
                spy_pct = ((spy_now - spy_then) / spy_then) * 100
                excess = pct_change - spy_pct
            except Exception:
                spy_pct = None
                excess = None
            
            conn.execute(
                """
                INSERT INTO signal_outcomes (
                    outcome_id, signal_id, ticker, signal_date, measurement_date,
                    days_elapsed, price_at_measurement, high_since_signal,
                    low_since_signal, entry_price, pct_change, hit_stop,
                    hit_target_1, hit_target_2, r_multiple, spy_pct_change,
                    excess_return, is_open
                )
                VALUES (nextval('outcome_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [signal_id, ticker, signal_date, datetime.now().date(), days,
                 current, high_since, low_since, entry_price, pct_change,
                 hit_stop, hit_t1, hit_t2, r_multiple, spy_pct, excess,
                 days < 90]
            )
        
        conn.commit()
    
    conn.close()


if __name__ == "__main__":
    track_outcomes()
```

Cron para rodar diariamente:
```
0 20 * * 1-5 cd ~/equity-research-lab && source venv/bin/activate && python src/outcome_tracker.py
```

---

## Queries úteis no DuckDB

Abra o DB:
```bash
duckdb ./data/research_lab.duckdb
```

### Top 10 sinais de hoje
```sql
SELECT ticker, close, composite_score, rank_position
FROM signals
WHERE signal_date = CURRENT_DATE
ORDER BY rank_position
LIMIT 10;
```

### Hit rate por janela temporal
```sql
SELECT * FROM v_hit_rate_by_window;
```

### Performance por setor (após 30+ dias de dados)
```sql
SELECT * FROM v_performance_by_sector;
```

### Sinais ainda abertos
```sql
SELECT * FROM v_open_signals;
```

### Análises com verdict COMPRAR e maior confidence
```sql
SELECT analysis_date, ticker, verdict, confidence_score, thesis_summary
FROM analyses_llm
WHERE verdict = 'COMPRAR' AND confidence_score >= 8
ORDER BY analysis_date DESC, confidence_score DESC
LIMIT 20;
```

### Sinais que repetiram entre múltiplas datas (insistência)
```sql
SELECT ticker, COUNT(*) as appearances,
       MIN(signal_date) as first_signal,
       MAX(signal_date) as last_signal,
       AVG(composite_score) as avg_score
FROM signals
GROUP BY ticker
HAVING COUNT(*) >= 5
ORDER BY appearances DESC;
```

### Tickers que tiveram bear case forte mas subiram mesmo assim
```sql
SELECT a.ticker, a.analysis_date, a.verdict,
       o.pct_change, o.r_multiple
FROM analyses_llm a
JOIN signal_outcomes o ON o.ticker = a.ticker
WHERE a.verdict = 'EVITAR' AND o.days_elapsed = 30 AND o.pct_change > 10
ORDER BY o.pct_change DESC;
```

---

## Pitfalls comuns e como resolver

### "yfinance rate limit"
**Sintoma:** muitos `None` no download.

**Resolução:** reduzir `max_workers` de 10 para 5 ou 3. Adicionar `time.sleep(0.1)` entre chamadas. Ou trocar para Polygon ($30/mês).

### "DuckDB locked"
**Sintoma:** erro ao abrir DB em duas conexões.

**Resolução:** DuckDB é single-writer. Garanta que só um processo escreve por vez. Para leitura concorrente, use modo read-only.

### "Token cost explodiu"
**Sintoma:** custo Anthropic muito acima de $50/mês.

**Resolução:**
- Use Haiku para coleta de dados, Sonnet só pra síntese final
- Reduza max_tokens do output
- Limite top 5 em vez de top 10
- Cache análises recentes (mesmo ticker analisado nos últimos 3 dias = skip)

### "Sinais não fazem sentido"
**Sintoma:** screener retorna lixo (penny stocks, ações ilíquidas).

**Resolução:**
- Aumentar `min_market_cap` para $500M
- Aumentar `min_avg_volume` para 500k
- Aumentar `min_price` para $10

### "Backtest mostra resultado lindo, ao vivo fica medíocre"
**Sintoma:** clássico look-ahead bias.

**Resolução:** revisar como você cortou os dados no backtest. As médias móveis usaram só info DISPONÍVEL no dia X, ou olharam pro futuro?

---

## Próximos passos depois do MVP

Depois de tudo isto rodando por 4 semanas:

1. **Adicionar fundamentos via yfinance/SimFin** (margem, ROIC, P/E) — habilita quality_score e value_score
2. **Adicionar insider transactions via OpenInsider scraping** — habilita análise de C-suite buying
3. **Adicionar earnings calendar via Finnhub/Yahoo** — habilita catalyst_score
4. **Dashboard Streamlit** — visualização do que o banco acumula
5. **Backtest framework** — replay histórico de 12 meses para validar mudanças
6. **Telegram bot** — entregar relatório diário no celular
7. **A/B testing de prompts** — comparar v3.0 vs v3.1 com significância estatística

Cada um desses adiciona ~10-20 horas de dev e aumenta valor do sistema.

---

## Resumo de comandos para o dia a dia

```bash
# Ativar env
source venv/bin/activate

# Rodar screener
python src/screener.py --top-n 20

# Rodar análise LLM (após screener)
python src/llm_analyzer.py

# Rodar outcome tracking
python src/outcome_tracker.py

# Ver stats do DB
python src/database_init.py --stats

# Abrir DB pra queries
duckdb ./data/research_lab.duckdb

# Backup do DB (diário recomendado)
cp ./data/research_lab.duckdb ./data/backups/research_lab_$(date +%Y%m%d).duckdb
```
