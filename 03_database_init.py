"""
Equity Research Lab — Database Initialization
=============================================

Cria o schema completo do DuckDB para o research lab.
Roda uma vez na primeira instalação. Idempotente: re-rodar é seguro.

USO:
    python database_init.py --db-path ./data/research_lab.duckdb
    python database_init.py --reset   # DROPA tudo e recria (cuidado!)

TABELAS:
    universe                  — Todos os tickers NYSE+NASDAQ + setor/indústria
    prices_daily              — OHLCV diário histórico
    fundamentals_quarterly    — Margens, ROIC, balanço (Q a Q)
    signals                   — Cada sinal gerado pelo screener
    analyses_llm              — Outputs estruturados das análises do Claude
    signal_outcomes           — Preço 7d/30d/60d/90d após sinal + R-multiple
    events                    — Earnings, M&A, splits, dividendos
    insider_transactions      — SEC Form 4 (compras/vendas executivos)
    short_interest            — FINRA bi-monthly
    runs                      — Log de execuções do screener (auditoria)

DESIGN PRINCIPLES:
    1. Cada tabela tem created_at e updated_at
    2. Sinais são imutáveis (não atualizamos um sinal — criamos novo)
    3. Outcomes são append-only (cada checagem de 7d/30d/60d/90d cria linha)
    4. Foreign keys soft (não enforced — DuckDB OLAP, queries são flexíveis)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


SCHEMA_SQL = """
-- =====================================================================
-- UNIVERSE
-- =====================================================================
CREATE TABLE IF NOT EXISTS universe (
    ticker VARCHAR PRIMARY KEY,
    company_name VARCHAR,
    exchange VARCHAR,
    sector VARCHAR,
    industry VARCHAR,
    market_cap_usd DOUBLE,
    is_active BOOLEAN DEFAULT TRUE,
    delisted_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_universe_sector ON universe(sector);
CREATE INDEX IF NOT EXISTS idx_universe_active ON universe(is_active);

-- =====================================================================
-- PRICES_DAILY (OHLCV)
-- =====================================================================
CREATE TABLE IF NOT EXISTS prices_daily (
    ticker VARCHAR,
    date DATE,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume BIGINT,
    adj_close DOUBLE,
    -- Indicadores pré-calculados (otimização)
    mm5 DOUBLE,
    mm21 DOUBLE,
    mm50 DOUBLE,
    mm100 DOUBLE,
    mm200 DOUBLE,
    rsi14 DOUBLE,
    atr14 DOUBLE,
    vol_avg_20 DOUBLE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_prices_date ON prices_daily(date);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices_daily(ticker);

-- =====================================================================
-- FUNDAMENTALS_QUARTERLY
-- =====================================================================
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    ticker VARCHAR,
    fiscal_period_end DATE,
    report_date DATE,
    -- Income statement
    revenue DOUBLE,
    gross_profit DOUBLE,
    operating_income DOUBLE,
    net_income DOUBLE,
    eps_diluted DOUBLE,
    -- Margins
    gross_margin DOUBLE,
    operating_margin DOUBLE,
    net_margin DOUBLE,
    -- Balance sheet
    total_assets DOUBLE,
    total_debt DOUBLE,
    cash_and_equivalents DOUBLE,
    shareholders_equity DOUBLE,
    -- Returns
    roe DOUBLE,
    roic DOUBLE,
    roa DOUBLE,
    -- Cash flow
    operating_cash_flow DOUBLE,
    free_cash_flow DOUBLE,
    capex DOUBLE,
    -- Source
    source VARCHAR DEFAULT 'yfinance',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_period_end)
);

-- =====================================================================
-- SIGNALS (gerados pelo screener)
-- =====================================================================
CREATE TABLE IF NOT EXISTS signals (
    signal_id BIGINT PRIMARY KEY,
    signal_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    -- Estado do ativo no momento do sinal
    close DOUBLE,
    volume BIGINT,
    vol_avg_20 DOUBLE,
    rsi14 DOUBLE,
    mm50 DOUBLE,
    mm200 DOUBLE,
    high_52w DOUBLE,
    low_52w DOUBLE,
    pct_below_52w_high DOUBLE,
    pct_above_52w_low DOUBLE,
    -- Scores do screener
    momentum_score DOUBLE,
    quality_score DOUBLE,
    catalyst_score DOUBLE,
    value_score DOUBLE,
    setup_score DOUBLE,
    composite_score DOUBLE,
    rank_position INTEGER,
    -- Stop/alvos sugeridos pelo screener (técnicos puros)
    suggested_stop DOUBLE,
    suggested_target_1 DOUBLE,
    suggested_target_2 DOUBLE,
    suggested_rr_ratio DOUBLE,
    -- Metadata
    screener_version VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (signal_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(composite_score);

CREATE SEQUENCE IF NOT EXISTS signal_id_seq START 1;

-- =====================================================================
-- ANALYSES_LLM (outputs do Claude/GPT em JSON estruturado)
-- =====================================================================
CREATE TABLE IF NOT EXISTS analyses_llm (
    analysis_id BIGINT PRIMARY KEY,
    signal_id BIGINT,
    analysis_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    -- Output estruturado (JSON validado)
    verdict VARCHAR,              -- COMPRAR / OBSERVAR / EVITAR / SHORTAR
    confidence_score INTEGER,     -- 1-10
    -- Tese e fundamentos
    thesis_summary TEXT,
    bull_arguments_json JSON,
    bear_arguments_json JSON,
    -- Catalisadores
    catalysts_json JSON,
    -- Plano operacional
    entry_zone_low DOUBLE,
    entry_zone_high DOUBLE,
    stop_loss DOUBLE,
    target_1 DOUBLE,
    target_2 DOUBLE,
    rr_target_1 DOUBLE,
    rr_target_2 DOUBLE,
    suggested_position_size_pct DOUBLE,
    -- Kill switches
    kill_switch_long TEXT,
    kill_switch_short TEXT,
    -- Raw outputs
    full_analysis_markdown TEXT,
    raw_json TEXT,
    -- Metadata
    model_used VARCHAR,            -- claude-sonnet-4-6, gpt-5, etc.
    prompt_version VARCHAR,        -- v3, v3.1, etc.
    token_cost_input INTEGER,
    token_cost_output INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (analysis_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_analyses_date ON analyses_llm(analysis_date);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON analyses_llm(ticker);
CREATE INDEX IF NOT EXISTS idx_analyses_verdict ON analyses_llm(verdict);

CREATE SEQUENCE IF NOT EXISTS analysis_id_seq START 1;

-- =====================================================================
-- SIGNAL_OUTCOMES (tracking 7d/30d/60d/90d após sinal)
-- =====================================================================
CREATE TABLE IF NOT EXISTS signal_outcomes (
    outcome_id BIGINT PRIMARY KEY,
    signal_id BIGINT NOT NULL,
    analysis_id BIGINT,
    ticker VARCHAR NOT NULL,
    signal_date DATE NOT NULL,
    measurement_date DATE NOT NULL,
    days_elapsed INTEGER,
    -- Preço no momento da medição
    price_at_measurement DOUBLE,
    high_since_signal DOUBLE,
    low_since_signal DOUBLE,
    -- Comparação com entry
    entry_price DOUBLE,
    pct_change DOUBLE,
    -- Comparação com stop e alvos da análise LLM
    hit_stop BOOLEAN,
    hit_target_1 BOOLEAN,
    hit_target_2 BOOLEAN,
    -- R-multiple realizado
    r_multiple DOUBLE,            -- (exit - entry) / (entry - stop)
    -- Comparação com benchmark (SPY)
    spy_pct_change DOUBLE,
    excess_return DOUBLE,         -- ticker_pct - spy_pct
    -- Status
    is_open BOOLEAN,              -- ainda dentro da janela 90d
    is_closed_winner BOOLEAN,
    is_closed_loser BOOLEAN,
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (signal_id, days_elapsed)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_signal ON signal_outcomes(signal_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_ticker ON signal_outcomes(ticker);

CREATE SEQUENCE IF NOT EXISTS outcome_id_seq START 1;

-- =====================================================================
-- EVENTS (earnings, M&A, splits, dividends)
-- =====================================================================
CREATE TABLE IF NOT EXISTS events (
    event_id BIGINT PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    event_date DATE NOT NULL,
    event_type VARCHAR,            -- earnings, ma_announcement, split, dividend, ipo, delisting
    event_subtype VARCHAR,         -- pre-market, after-hours, completed, rumored
    description TEXT,
    impact_pct DOUBLE,             -- variação % no dia do evento (calculado depois)
    metadata_json JSON,
    source VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, event_date, event_type)
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

CREATE SEQUENCE IF NOT EXISTS event_id_seq START 1;

-- =====================================================================
-- INSIDER_TRANSACTIONS (SEC Form 4)
-- =====================================================================
CREATE TABLE IF NOT EXISTS insider_transactions (
    transaction_id BIGINT PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    insider_name VARCHAR,
    insider_title VARCHAR,
    transaction_date DATE,
    transaction_type VARCHAR,      -- buy, sell, gift, option_exercise
    shares INTEGER,
    price_per_share DOUBLE,
    total_value DOUBLE,
    shares_owned_after INTEGER,
    is_10b5_1 BOOLEAN,
    filing_date DATE,
    source VARCHAR DEFAULT 'openinsider',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, insider_name, transaction_date, shares)
);

CREATE INDEX IF NOT EXISTS idx_insider_ticker ON insider_transactions(ticker);
CREATE INDEX IF NOT EXISTS idx_insider_date ON insider_transactions(transaction_date);

CREATE SEQUENCE IF NOT EXISTS transaction_id_seq START 1;

-- =====================================================================
-- SHORT_INTEREST (FINRA)
-- =====================================================================
CREATE TABLE IF NOT EXISTS short_interest (
    ticker VARCHAR,
    settlement_date DATE,
    short_shares BIGINT,
    avg_daily_volume BIGINT,
    days_to_cover DOUBLE,
    short_pct_float DOUBLE,
    source VARCHAR DEFAULT 'finra',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, settlement_date)
);

CREATE INDEX IF NOT EXISTS idx_short_ticker ON short_interest(ticker);

-- =====================================================================
-- RUNS (log de execuções para auditoria)
-- =====================================================================
CREATE TABLE IF NOT EXISTS runs (
    run_id BIGINT PRIMARY KEY,
    run_type VARCHAR,              -- screener, llm_analysis, outcome_tracking
    run_date DATE NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    elapsed_seconds DOUBLE,
    tickers_processed INTEGER,
    signals_generated INTEGER,
    errors_count INTEGER,
    status VARCHAR,                -- success, partial, failed
    notes TEXT,
    config_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
CREATE INDEX IF NOT EXISTS idx_runs_type ON runs(run_type);

CREATE SEQUENCE IF NOT EXISTS run_id_seq START 1;

-- =====================================================================
-- VIEWS úteis (calculadas dinamicamente)
-- =====================================================================

-- Hit rate global ao longo do tempo
CREATE OR REPLACE VIEW v_hit_rate_by_window AS
SELECT
    days_elapsed,
    COUNT(*) AS total_signals,
    SUM(CASE WHEN hit_target_1 THEN 1 ELSE 0 END) AS hit_target_1_count,
    SUM(CASE WHEN hit_target_2 THEN 1 ELSE 0 END) AS hit_target_2_count,
    SUM(CASE WHEN hit_stop THEN 1 ELSE 0 END) AS hit_stop_count,
    AVG(r_multiple) AS avg_r_multiple,
    AVG(excess_return) AS avg_excess_return,
    AVG(CASE WHEN hit_target_1 THEN 1.0 ELSE 0.0 END) * 100 AS hit_rate_target_1_pct,
    AVG(CASE WHEN hit_target_2 THEN 1.0 ELSE 0.0 END) * 100 AS hit_rate_target_2_pct
FROM signal_outcomes
WHERE NOT is_open
GROUP BY days_elapsed
ORDER BY days_elapsed;

-- Performance por setor
CREATE OR REPLACE VIEW v_performance_by_sector AS
SELECT
    u.sector,
    COUNT(*) AS total_signals,
    AVG(o.r_multiple) AS avg_r_multiple,
    AVG(o.excess_return) AS avg_excess_return,
    AVG(CASE WHEN o.hit_target_1 THEN 1.0 ELSE 0.0 END) * 100 AS hit_rate_pct
FROM signal_outcomes o
JOIN universe u ON u.ticker = o.ticker
WHERE NOT o.is_open AND o.days_elapsed = 30
GROUP BY u.sector
ORDER BY avg_r_multiple DESC;

-- Sinais ainda abertos (precisam de tracking)
CREATE OR REPLACE VIEW v_open_signals AS
SELECT
    s.signal_id,
    s.signal_date,
    s.ticker,
    s.composite_score,
    DATE_DIFF('day', s.signal_date, CURRENT_DATE) AS days_since_signal,
    a.verdict AS llm_verdict,
    a.confidence_score AS llm_confidence
FROM signals s
LEFT JOIN analyses_llm a ON a.signal_id = s.signal_id
WHERE DATE_DIFF('day', s.signal_date, CURRENT_DATE) <= 90
ORDER BY s.signal_date DESC, s.composite_score DESC;
"""


RESET_SQL = """
DROP VIEW IF EXISTS v_open_signals;
DROP VIEW IF EXISTS v_performance_by_sector;
DROP VIEW IF EXISTS v_hit_rate_by_window;
DROP SEQUENCE IF EXISTS run_id_seq;
DROP SEQUENCE IF EXISTS transaction_id_seq;
DROP SEQUENCE IF EXISTS event_id_seq;
DROP SEQUENCE IF EXISTS outcome_id_seq;
DROP SEQUENCE IF EXISTS analysis_id_seq;
DROP SEQUENCE IF EXISTS signal_id_seq;
DROP TABLE IF EXISTS runs;
DROP TABLE IF EXISTS short_interest;
DROP TABLE IF EXISTS insider_transactions;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS signal_outcomes;
DROP TABLE IF EXISTS analyses_llm;
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS fundamentals_quarterly;
DROP TABLE IF EXISTS prices_daily;
DROP TABLE IF EXISTS universe;
"""


def init_database(db_path: Path, reset: bool = False):
    """Cria o schema. Se reset=True, dropa tudo antes."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path))
    try:
        if reset:
            logger.warning(f"RESET: dropando todas as tabelas em {db_path}")
            conn.execute(RESET_SQL)

        logger.info(f"Criando schema em {db_path}")
        conn.execute(SCHEMA_SQL)

        # Lista tabelas criadas
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        logger.info(f"Tabelas criadas: {[t[0] for t in tables]}")

        # Lista views
        views = conn.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
        ).fetchall()
        logger.info(f"Views criadas: {[v[0] for v in views]}")

    finally:
        conn.close()

    logger.info(f"Database inicializado: {db_path}")


def show_stats(db_path: Path):
    """Mostra estatísticas do estado atual do banco."""
    if not db_path.exists():
        logger.error(f"Database não existe: {db_path}")
        return

    conn = duckdb.connect(str(db_path))
    try:
        tables = [
            "universe",
            "prices_daily",
            "signals",
            "analyses_llm",
            "signal_outcomes",
            "events",
            "insider_transactions",
            "short_interest",
            "runs",
        ]

        print("\n" + "=" * 60)
        print(f"DATABASE STATS: {db_path}")
        print("=" * 60)
        for t in tables:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t:30s} {count:>10,} linhas")
            except Exception as e:
                print(f"  {t:30s} ERRO: {e}")
        print()

        # Tamanho do arquivo
        size_mb = db_path.stat().st_size / 1024 / 1024
        print(f"Tamanho do arquivo: {size_mb:.1f} MB")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Init DuckDB schema for Research Lab")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("./data/research_lab.duckdb"),
        help="Caminho do arquivo .duckdb",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="ATENÇÃO: dropa todas as tabelas antes de criar (perde dados)",
    )
    parser.add_argument("--stats", action="store_true", help="Apenas mostra stats")
    args = parser.parse_args()

    if args.stats:
        show_stats(args.db_path)
    else:
        if args.reset:
            confirm = input(
                f"\n⚠️  RESET vai DROPAR TUDO em {args.db_path}\nDigite 'YES' para confirmar: "
            )
            if confirm != "YES":
                print("Cancelado.")
                return
        init_database(args.db_path, reset=args.reset)
        show_stats(args.db_path)


if __name__ == "__main__":
    main()
