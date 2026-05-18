"""
Equity Research Lab — Query CLI
================================

Helper pra rodar queries SQL no DuckDB de forma rápida.

USO:

    # Roda uma query pré-definida
    python src/q.py today
    python src/q.py insider-flags
    python src/q.py recent

    # Roda SQL custom
    python src/q.py --sql "SELECT ticker, close FROM signals LIMIT 5"

    # Lista todas as queries disponíveis
    python src/q.py --list

    # Modo interativo (REPL SQL no DuckDB)
    python src/q.py --interactive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import polars as pl

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"

# ============================================================
# QUERIES PRÉ-DEFINIDAS
# ============================================================

QUERIES = {
    "today": {
        "desc": "Top 20 sinais do dia atual (A e B)",
        "sql": """
            SELECT
                rank_position AS rank,
                universe,
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(rsi14, 1) AS rsi,
                ROUND(composite_score, 1) AS score,
                ROUND(pct_below_52w_high, 2) AS pct_high
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
            ORDER BY universe, rank_position
        """,
    },
    "today-a": {
        "desc": "Top 10 do Universo A (mid/large)",
        "sql": """
            SELECT
                rank_position AS rank,
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(rsi14, 1) AS rsi,
                ROUND(composite_score, 1) AS score,
                ROUND(market_cap_usd / 1e9, 2) AS market_cap_bn
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND universe = 'A'
            ORDER BY rank_position
        """,
    },
    "today-b": {
        "desc": "Top 10 do Universo B (small caps)",
        "sql": """
            SELECT
                rank_position AS rank,
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(rsi14, 1) AS rsi,
                ROUND(composite_score, 1) AS score,
                ROUND(market_cap_usd / 1e6, 2) AS market_cap_mm
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND universe = 'B'
            ORDER BY rank_position
        """,
    },
    "recent": {
        "desc": "Sinais dos últimos 7 dias com sinais (qualquer dia útil)",
        "sql": """
            SELECT
                signal_date,
                universe,
                COUNT(*) AS n_sinais,
                STRING_AGG(ticker, ', ' ORDER BY rank_position) AS tickers
            FROM signals
            WHERE signal_date >= (SELECT MAX(signal_date) FROM signals) - INTERVAL '7 days'
            GROUP BY signal_date, universe
            ORDER BY signal_date DESC, universe
        """,
    },
    "repeats": {
        "desc": "Tickers que apareceram em múltiplos dias (alta convicção do screener)",
        "sql": """
            SELECT
                ticker,
                sector,
                COUNT(DISTINCT signal_date) AS dias_aparecendo,
                MIN(signal_date) AS primeira_vez,
                MAX(signal_date) AS ultima_vez,
                ROUND(AVG(composite_score), 1) AS avg_score
            FROM signals
            GROUP BY ticker, sector
            HAVING COUNT(DISTINCT signal_date) >= 2
            ORDER BY dias_aparecendo DESC, avg_score DESC
        """,
    },
    "insider-flags": {
        "desc": "Tickers do dia com insider selling pesado (RED FLAG)",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                insider_n_buys_6m AS buys,
                insider_n_sells_6m AS sells,
                ROUND(insider_net_value_6m / 1e6, 2) AS net_value_mm,
                insider_top_sellers AS top_vendedores
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND insider_net_value_6m IS NOT NULL
              AND insider_net_value_6m < -1000000  -- mais de $1M vendido
            ORDER BY insider_net_value_6m ASC
        """,
    },
    "insider-buys": {
        "desc": "Tickers do dia com insider BUYING (sinal positivo raro)",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                insider_n_buys_6m AS buys,
                insider_n_sells_6m AS sells,
                ROUND(insider_net_value_6m / 1e6, 2) AS net_value_mm
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND insider_n_buys_6m > 0
            ORDER BY insider_net_value_6m DESC
        """,
    },
    "cheap": {
        "desc": "Tickers do dia com Forward P/E < 15 (potencialmente baratos)",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(pe_ttm, 1) AS pe_ttm,
                ROUND(pe_forward, 1) AS pe_fwd,
                ROUND(ev_to_ebitda, 1) AS ev_ebitda,
                ROUND(dividend_yield * 100, 2) AS div_yield_pct,
                ROUND(revenue_growth_yoy * 100, 1) AS rev_growth_pct
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND pe_forward IS NOT NULL
              AND pe_forward < 15
              AND pe_forward > 0
            ORDER BY pe_forward ASC
        """,
    },
    "growth": {
        "desc": "Tickers do dia com revenue growth > 20% YoY",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(revenue_growth_yoy * 100, 1) AS rev_growth_pct,
                ROUND(earnings_growth_yoy * 100, 1) AS earn_growth_pct,
                ROUND(pe_forward, 1) AS pe_fwd,
                ROUND(composite_score, 1) AS score
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND revenue_growth_yoy IS NOT NULL
              AND revenue_growth_yoy > 0.20
            ORDER BY revenue_growth_yoy DESC
        """,
    },
    "quality": {
        "desc": "Tickers do dia com FCF positivo + ROE > 15% + net debt manejável",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(free_cash_flow / 1e6, 1) AS fcf_mm,
                ROUND(return_on_equity * 100, 1) AS roe_pct,
                ROUND(net_debt / 1e6, 1) AS net_debt_mm,
                ROUND(gross_margin * 100, 1) AS gross_pct,
                ROUND(operating_margin * 100, 1) AS op_pct
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND free_cash_flow > 0
              AND return_on_equity > 0.15
            ORDER BY return_on_equity DESC
        """,
    },
    "analyst-bullish": {
        "desc": "Tickers do dia com upside > 20% pelo target mean dos analistas",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                ROUND(target_mean_price, 2) AS target_mean,
                ROUND(target_upside_pct, 1) AS upside_pct,
                ROUND(analyst_rating, 2) AS rating,
                analyst_count AS n_analysts,
                analyst_upgrades_90d AS up_90d,
                analyst_downgrades_90d AS down_90d
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND target_upside_pct IS NOT NULL
              AND target_upside_pct > 20
            ORDER BY target_upside_pct DESC
        """,
    },
    "earnings-soon": {
        "desc": "Tickers do dia com earnings nos próximos 30 dias",
        "sql": """
            SELECT
                ticker,
                sector,
                ROUND(close, 2) AS preco,
                next_earnings_date,
                earnings_beats_4q AS beats_4q,
                ROUND(earnings_avg_surprise_pct, 1) AS surprise_avg_pct,
                ROUND(composite_score, 1) AS score
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
              AND next_earnings_date IS NOT NULL
              AND CAST(next_earnings_date AS DATE) BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'
            ORDER BY next_earnings_date ASC
        """,
    },
    "sectors": {
        "desc": "Distribuição setorial dos sinais do dia",
        "sql": """
            SELECT
                universe,
                sector,
                COUNT(*) AS n,
                ROUND(AVG(composite_score), 1) AS avg_score,
                STRING_AGG(ticker, ', ' ORDER BY composite_score DESC) AS tickers
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
            GROUP BY universe, sector
            ORDER BY universe, n DESC, avg_score DESC
        """,
    },
    "history": {
        "desc": "Histórico completo de quantos sinais foram gerados por dia",
        "sql": """
            SELECT
                signal_date,
                universe,
                COUNT(*) AS n_sinais,
                ROUND(AVG(composite_score), 1) AS avg_score,
                ROUND(MIN(composite_score), 1) AS min_score,
                ROUND(MAX(composite_score), 1) AS max_score
            FROM signals
            GROUP BY signal_date, universe
            ORDER BY signal_date DESC, universe
        """,
    },
    "ticker-history": {
        "desc": "Histórico de um ticker específico (passe via --ticker AAPL)",
        "sql": None,  # Não tem SQL fixo, gerado dinamicamente
        "dynamic": True,
    },
    "valuations-summary": {
        "desc": "Estatísticas de valuation dos sinais de hoje (mediana e quartis)",
        "sql": """
            SELECT
                universe,
                COUNT(*) AS n,
                ROUND(MEDIAN(pe_forward), 1) AS pe_fwd_median,
                ROUND(MEDIAN(ev_to_ebitda), 1) AS ev_ebitda_median,
                ROUND(MEDIAN(price_to_book), 1) AS pb_median,
                ROUND(MEDIAN(revenue_growth_yoy) * 100, 1) AS rev_growth_median_pct,
                ROUND(MEDIAN(beta), 2) AS beta_median
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
            GROUP BY universe
        """,
    },
    "outcomes": {
        "desc": "Hit rate por janela temporal (precisa de outcomes coletados)",
        "sql": """
            SELECT
                days_elapsed AS dias,
                COUNT(*) AS n_outcomes,
                ROUND(AVG(CASE WHEN hit_target_1 THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate_t1_pct,
                ROUND(AVG(CASE WHEN hit_target_2 THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate_t2_pct,
                ROUND(AVG(CASE WHEN hit_stop THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_stop_pct,
                ROUND(AVG(r_multiple), 2) AS avg_r,
                ROUND(AVG(excess_return), 2) AS avg_excess_vs_spy
            FROM signal_outcomes
            GROUP BY days_elapsed
            ORDER BY days_elapsed
        """,
    },
    "db-stats": {
        "desc": "Estatísticas gerais do banco",
        "sql": """
            SELECT
                'signals' AS tabela,
                COUNT(*) AS linhas,
                COUNT(DISTINCT signal_date) AS dias_unicos,
                COUNT(DISTINCT ticker) AS tickers_unicos
            FROM signals
            UNION ALL
            SELECT
                'analyses_llm',
                COUNT(*),
                COUNT(DISTINCT analysis_date),
                COUNT(DISTINCT ticker)
            FROM analyses_llm
            UNION ALL
            SELECT
                'signal_outcomes',
                COUNT(*),
                COUNT(DISTINCT signal_date),
                COUNT(DISTINCT ticker)
            FROM signal_outcomes
        """,
    },
    "macro-context": {
        "desc": "Combinação técnica + macro: cada ticker com beta e setor (pra contexto macro)",
        "sql": """
            SELECT
                ticker,
                sector,
                universe,
                ROUND(close, 2) AS preco,
                ROUND(beta, 2) AS beta,
                ROUND(change_52w_pct * 100, 1) AS chg_52w_pct,
                CASE
                    WHEN beta > 1.5 THEN 'High beta'
                    WHEN beta > 1.0 THEN 'Med-high beta'
                    WHEN beta > 0.7 THEN 'Med beta'
                    ELSE 'Low beta'
                END AS risk_profile
            FROM signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
            ORDER BY beta DESC NULLS LAST
        """,
    },
}


def ticker_history_query(ticker: str) -> str:
    """Gera SQL pra histórico de um ticker específico."""
    return f"""
        SELECT
            signal_date,
            universe,
            rank_position AS rank,
            ROUND(close, 2) AS preco,
            ROUND(rsi14, 1) AS rsi,
            ROUND(composite_score, 1) AS score,
            ROUND(pe_forward, 1) AS pe_fwd,
            insider_n_sells_6m AS insider_sells,
            ROUND(analyst_rating, 2) AS rating
        FROM signals
        WHERE ticker = '{ticker.upper()}'
        ORDER BY signal_date DESC
    """


def print_result(rows, cols):
    """Imprime resultado como tabela usando polars (pretty print nativo)."""
    if not rows:
        print("(sem resultados)")
        return

    df = pl.DataFrame(rows, schema=cols, orient="row")
    # Configura display do polars pra mostrar mais
    with pl.Config(
        tbl_rows=100,
        tbl_cols=20,
        fmt_str_lengths=80,
        tbl_width_chars=200,
    ):
        print(df)


def run_query(sql: str):
    """Executa SQL e imprime."""
    if not DB_PATH.exists():
        print(f"❌ DB não existe: {DB_PATH}")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        result = conn.execute(sql)
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        print_result(rows, cols)
        print(f"\n({len(rows)} linhas)")
    except Exception as e:
        print(f"❌ Erro na query: {e}")
        sys.exit(1)
    finally:
        conn.close()


def list_queries():
    """Lista todas as queries pré-definidas com descrição."""
    print("\n=== QUERIES DISPONÍVEIS ===\n")
    for name, q in QUERIES.items():
        print(f"  {name:25s} {q['desc']}")
    print(f"\nUso: python src/q.py <nome>")
    print(f"     python src/q.py ticker-history --ticker AAPL")
    print(f"     python src/q.py --sql \"SELECT * FROM signals LIMIT 5\"")
    print(f"     python src/q.py --interactive")


def interactive_repl():
    """Modo REPL interativo no DuckDB."""
    if not DB_PATH.exists():
        print(f"❌ DB não existe: {DB_PATH}")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    print("=== Modo Interativo DuckDB ===")
    print("Digite SQL e Enter. Pra sair: 'quit' ou Ctrl+D")
    print("Tabelas disponíveis: signals, analyses_llm, signal_outcomes, etc.\n")

    while True:
        try:
            sql = input("sql> ").strip()
            if not sql:
                continue
            if sql.lower() in ("quit", "exit", "q"):
                break
            if not sql.endswith(";"):
                sql += " LIMIT 50;"  # safety limit
            result = conn.execute(sql)
            cols = [d[0] for d in result.description]
            rows = result.fetchall()
            print_result(rows, cols)
            print(f"({len(rows)} linhas)\n")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        except Exception as e:
            print(f"❌ {e}\n")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Query CLI para Equity Research Lab")
    parser.add_argument("query", nargs="?", help="Nome da query pré-definida")
    parser.add_argument("--sql", help="SQL custom pra rodar")
    parser.add_argument("--list", action="store_true", help="Lista queries disponíveis")
    parser.add_argument("--interactive", action="store_true", help="REPL SQL")
    parser.add_argument("--ticker", help="Ticker para ticker-history (ex: AAPL)")

    args = parser.parse_args()

    if args.list:
        list_queries()
        return

    if args.interactive:
        interactive_repl()
        return

    if args.sql:
        run_query(args.sql)
        return

    if not args.query:
        list_queries()
        return

    if args.query not in QUERIES:
        print(f"❌ Query '{args.query}' não existe.")
        list_queries()
        sys.exit(1)

    q = QUERIES[args.query]

    # Query dinâmica
    if q.get("dynamic") and args.query == "ticker-history":
        if not args.ticker:
            print("❌ Use --ticker AAPL (ou similar) com ticker-history")
            sys.exit(1)
        sql = ticker_history_query(args.ticker)
    else:
        sql = q["sql"]

    print(f"=== {args.query}: {q['desc']} ===\n")
    run_query(sql)


if __name__ == "__main__":
    main()
