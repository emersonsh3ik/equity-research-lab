"""
Equity Research Lab — Enriquecimento de Sinais
================================================

Pega os top sinais do screener e adiciona dados que enriquecem a análise LLM:
- Fundamentals via yfinance (P/E TTM, P/E Forward, EPS, dividend yield, próxima earnings)
- Insider transactions via yfinance (net buying/selling 6 meses)
- Short interest (se disponível)

Roda DEPOIS do screener mas ANTES do publish_signals.py.

USO:
    python src/enrich_signals.py

Lê o último arquivo em data/signals/, enriquece com dados extras, e salva
de volta no mesmo arquivo (preservando os campos originais).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import yfinance as yf
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
SIGNALS_DIR = PROJECT_DIR / "data" / "signals"
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"

# Mapeia campo Python → tipo SQL pra criar colunas no DuckDB
NEW_COLUMNS_SQL = {
    # ===== Valuation original =====
    "pe_ttm": "DOUBLE",
    "pe_forward": "DOUBLE",
    "eps_ttm": "DOUBLE",
    "eps_forward": "DOUBLE",
    "dividend_yield": "DOUBLE",
    # ===== Margens =====
    "profit_margin": "DOUBLE",
    "operating_margin": "DOUBLE",
    "gross_margin": "DOUBLE",
    "ebitda_margin": "DOUBLE",
    # ===== Rentabilidade / Balanço =====
    "debt_to_equity": "DOUBLE",
    "return_on_equity": "DOUBLE",
    "current_ratio": "DOUBLE",
    "quick_ratio": "DOUBLE",
    # ===== Crescimento =====
    "revenue_growth_yoy": "DOUBLE",
    "earnings_growth_yoy": "DOUBLE",
    "next_earnings_date": "VARCHAR",
    # ===== Short =====
    "short_percent_of_float": "DOUBLE",
    "short_ratio": "DOUBLE",
    # ===== Insider =====
    "insider_net_shares_6m": "BIGINT",
    "insider_net_value_6m": "DOUBLE",
    "insider_n_buys_6m": "INTEGER",
    "insider_n_sells_6m": "INTEGER",
    "insider_top_sellers": "VARCHAR",
    "insider_last_transaction_date": "VARCHAR",
    # ===== TIER 1 NOVO: Valuation expandido =====
    "enterprise_value": "DOUBLE",
    "ev_to_ebitda": "DOUBLE",
    "ev_to_revenue": "DOUBLE",
    "price_to_book": "DOUBLE",
    "book_value": "DOUBLE",
    "ebitda": "DOUBLE",
    "free_cash_flow": "DOUBLE",
    "operating_cash_flow": "DOUBLE",
    # ===== TIER 1 NOVO: Balanço =====
    "total_cash": "DOUBLE",
    "total_debt": "DOUBLE",
    "net_debt": "DOUBLE",
    # ===== TIER 1 NOVO: Mercado e risco =====
    "beta": "DOUBLE",
    "change_52w_pct": "DOUBLE",
    "float_shares": "BIGINT",
    "shares_outstanding": "BIGINT",
    # ===== TIER 1 NOVO: Analyst coverage =====
    "analyst_rating": "DOUBLE",  # 1=Strong Buy, 5=Strong Sell
    "analyst_count": "INTEGER",
    "target_mean_price": "DOUBLE",
    "target_high_price": "DOUBLE",
    "target_low_price": "DOUBLE",
    "target_upside_pct": "DOUBLE",  # calculado: (target_mean / close - 1) * 100
    # ===== TIER 1 NOVO: Ownership =====
    "held_pct_insiders": "DOUBLE",
    "held_pct_institutions": "DOUBLE",
    # ===== TIER 2: Earnings history =====
    "earnings_beats_4q": "INTEGER",  # quantos beats nos últimos 4 trimestres
    "earnings_avg_surprise_pct": "DOUBLE",
    # ===== TIER 2: Analyst momentum =====
    "analyst_upgrades_90d": "INTEGER",
    "analyst_downgrades_90d": "INTEGER",
    "analyst_revisions_net": "INTEGER",  # upgrades - downgrades
    # ===== TIER 2: Ownership detail =====
    "top_institutional_holder": "VARCHAR",
}


def fetch_fundamentals(ticker: str, current_price: float | None = None) -> dict:
    """
    Busca fundamentals via yfinance.info (uma chamada por ticker).

    Inclui Tier 1 expandido: valuation completo (EV/EBITDA, EV/Sales, P/B, FCF),
    balanço (cash, debt, net debt, liquidity ratios), risco (beta, 52w change),
    analyst coverage (rating, targets), ownership (% insiders, % institutions).

    Campos não disponíveis retornam None — NUNCA inventa.
    """
    result = {k: None for k in NEW_COLUMNS_SQL.keys()}

    try:
        t = yf.Ticker(ticker)
        info = t.info

        # === Mapeamento direto info → nosso schema ===
        field_map = {
            # Valuation original
            "trailingPE": "pe_ttm",
            "forwardPE": "pe_forward",
            "trailingEps": "eps_ttm",
            "forwardEps": "eps_forward",
            "dividendYield": "dividend_yield",
            # Margens
            "profitMargins": "profit_margin",
            "operatingMargins": "operating_margin",
            "grossMargins": "gross_margin",
            "ebitdaMargins": "ebitda_margin",
            # Rentabilidade / Balanço
            "debtToEquity": "debt_to_equity",
            "returnOnEquity": "return_on_equity",
            "currentRatio": "current_ratio",
            "quickRatio": "quick_ratio",
            # Crescimento
            "revenueGrowth": "revenue_growth_yoy",
            "earningsGrowth": "earnings_growth_yoy",
            # Short
            "shortPercentOfFloat": "short_percent_of_float",
            "shortRatio": "short_ratio",
            # Tier 1 — Valuation expandido
            "enterpriseValue": "enterprise_value",
            "enterpriseToEbitda": "ev_to_ebitda",
            "enterpriseToRevenue": "ev_to_revenue",
            "priceToBook": "price_to_book",
            "bookValue": "book_value",
            "ebitda": "ebitda",
            "freeCashflow": "free_cash_flow",
            "operatingCashflow": "operating_cash_flow",
            # Tier 1 — Balanço
            "totalCash": "total_cash",
            "totalDebt": "total_debt",
            # Tier 1 — Mercado
            "beta": "beta",
            "52WeekChange": "change_52w_pct",
            "floatShares": "float_shares",
            "sharesOutstanding": "shares_outstanding",
            # Tier 1 — Analyst coverage
            "recommendationMean": "analyst_rating",
            "numberOfAnalystOpinions": "analyst_count",
            "targetMeanPrice": "target_mean_price",
            "targetHighPrice": "target_high_price",
            "targetLowPrice": "target_low_price",
            # Tier 1 — Ownership
            "heldPercentInsiders": "held_pct_insiders",
            "heldPercentInstitutions": "held_pct_institutions",
        }

        for yf_key, our_key in field_map.items():
            val = info.get(yf_key)
            if val is not None:
                try:
                    result[our_key] = float(val)
                except (TypeError, ValueError):
                    pass

        # === Cálculos derivados ===
        # Net debt = total debt - total cash
        if result["total_debt"] is not None and result["total_cash"] is not None:
            result["net_debt"] = result["total_debt"] - result["total_cash"]

        # Target upside %
        price = current_price or info.get("currentPrice") or info.get("regularMarketPrice")
        if price and result["target_mean_price"]:
            try:
                result["target_upside_pct"] = (
                    (result["target_mean_price"] / float(price)) - 1
                ) * 100
            except (ZeroDivisionError, TypeError):
                pass

        # Próxima earnings — yfinance .calendar
        try:
            cal = t.calendar
            if cal is not None and isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                # Prefere data futura
                today = datetime.now().date()
                future = None
                for d in earnings_dates:
                    try:
                        d_obj = d if hasattr(d, "year") else datetime.fromisoformat(str(d)).date()
                        if d_obj >= today:
                            future = d_obj
                            break
                    except Exception:
                        continue
                if future is not None:
                    result["next_earnings_date"] = future.isoformat()
                elif earnings_dates:
                    d = earnings_dates[0]
                    result["next_earnings_date"] = (
                        d.isoformat() if hasattr(d, "isoformat") else str(d)
                    )
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"Falha em fundamentals de {ticker}: {e}")

    return result


def fetch_tier2_metrics(ticker: str) -> dict:
    """
    TIER 2: métricas que requerem chamadas adicionais ao yfinance.

    - earnings_history: % de beats e surprise médio últimos 4 trimestres
    - upgrades_downgrades: contagem de revisões últimos 90 dias
    - institutional_holders: top holder

    Cada chamada falha graciosamente se o dado não estiver disponível.
    """
    result = {
        "earnings_beats_4q": None,
        "earnings_avg_surprise_pct": None,
        "analyst_upgrades_90d": None,
        "analyst_downgrades_90d": None,
        "analyst_revisions_net": None,
        "top_institutional_holder": None,
    }

    try:
        t = yf.Ticker(ticker)

        # === Earnings history ===
        try:
            eh = t.earnings_history
            if eh is not None and len(eh) > 0:
                # eh é DataFrame; pega últimos 4 trimestres
                recent = eh.head(4) if len(eh) >= 4 else eh
                if "epsActual" in recent.columns and "epsEstimate" in recent.columns:
                    beats = 0
                    surprises = []
                    for _, row in recent.iterrows():
                        actual = row.get("epsActual")
                        est = row.get("epsEstimate")
                        if actual is not None and est is not None:
                            try:
                                if float(actual) > float(est):
                                    beats += 1
                                if float(est) != 0:
                                    surprises.append(
                                        ((float(actual) - float(est)) / abs(float(est))) * 100
                                    )
                            except (TypeError, ValueError):
                                continue
                    result["earnings_beats_4q"] = beats
                    if surprises:
                        result["earnings_avg_surprise_pct"] = sum(surprises) / len(surprises)
        except Exception as e:
            logger.debug(f"earnings_history falhou {ticker}: {e}")

        # === Upgrades/downgrades últimos 90 dias ===
        try:
            ud = t.upgrades_downgrades
            if ud is not None and len(ud) > 0:
                cutoff = datetime.now() - timedelta(days=90)
                # Index é data
                recent = ud[ud.index >= cutoff] if hasattr(ud.index, "to_pydatetime") else ud.head(20)

                upgrades = 0
                downgrades = 0
                if "Action" in recent.columns:
                    for _, row in recent.iterrows():
                        action = str(row.get("Action", "")).lower()
                        if "up" in action or "upgrade" in action:
                            upgrades += 1
                        elif "down" in action or "downgrade" in action:
                            downgrades += 1

                result["analyst_upgrades_90d"] = upgrades
                result["analyst_downgrades_90d"] = downgrades
                result["analyst_revisions_net"] = upgrades - downgrades
        except Exception as e:
            logger.debug(f"upgrades_downgrades falhou {ticker}: {e}")

        # === Top institutional holder ===
        try:
            ih = t.institutional_holders
            if ih is not None and len(ih) > 0:
                # DataFrame ordenado por % held
                if "Holder" in ih.columns:
                    result["top_institutional_holder"] = str(ih.iloc[0]["Holder"])
        except Exception as e:
            logger.debug(f"institutional_holders falhou {ticker}: {e}")

    except Exception as e:
        logger.debug(f"Falha em tier2 de {ticker}: {e}")

    return result


def fetch_insider_summary(ticker: str) -> dict:
    """
    Busca resumo de insider transactions via yfinance.

    Retorna dict com:
    - insider_net_shares_6m: número líquido de ações compradas (positivo) ou
      vendidas (negativo) por insiders nos últimos 6 meses
    - insider_net_value_6m: valor líquido em USD
    - insider_n_buys_6m: número de transações de compra
    - insider_n_sells_6m: número de transações de venda
    - insider_top_sellers: lista dos top 3 vendedores no período
    - insider_last_transaction_date: data da última transação

    Campos não disponíveis retornam None.
    """
    result = {
        "insider_net_shares_6m": None,
        "insider_net_value_6m": None,
        "insider_n_buys_6m": None,
        "insider_n_sells_6m": None,
        "insider_top_sellers": None,
        "insider_last_transaction_date": None,
    }

    try:
        t = yf.Ticker(ticker)
        trans = t.insider_transactions

        if trans is None or len(trans) == 0:
            return result

        # trans é um pandas DataFrame
        # Colunas típicas: Insider, Position, URL, Transaction, Text, Start Date, Shares, Value, Ownership
        # Filtra últimos 6 meses
        cutoff = datetime.now() - timedelta(days=180)

        if "Start Date" in trans.columns:
            trans["Start Date"] = pl.from_pandas(trans[["Start Date"]]).to_pandas()["Start Date"]
            recent = trans[trans["Start Date"] >= cutoff].copy()
        else:
            recent = trans.head(20).copy()  # fallback: últimas 20 transações

        if len(recent) == 0:
            return result

        # Classifica buy vs sell pela coluna Transaction ou Text
        def is_buy(row):
            text = str(row.get("Transaction", "") or row.get("Text", "")).lower()
            return "buy" in text or "purchase" in text or "acquisition" in text

        def is_sell(row):
            text = str(row.get("Transaction", "") or row.get("Text", "")).lower()
            return "sale" in text or "sell" in text or "disposition" in text

        n_buys = sum(1 for _, row in recent.iterrows() if is_buy(row))
        n_sells = sum(1 for _, row in recent.iterrows() if is_sell(row))

        # Tenta calcular shares e value líquidos
        net_shares = 0
        net_value = 0
        for _, row in recent.iterrows():
            shares = row.get("Shares")
            value = row.get("Value")
            sign = 1 if is_buy(row) else (-1 if is_sell(row) else 0)
            if sign != 0 and shares is not None:
                try:
                    net_shares += sign * int(shares)
                except (TypeError, ValueError):
                    pass
            if sign != 0 and value is not None:
                try:
                    net_value += sign * float(value)
                except (TypeError, ValueError):
                    pass

        result["insider_net_shares_6m"] = net_shares
        result["insider_net_value_6m"] = net_value
        result["insider_n_buys_6m"] = n_buys
        result["insider_n_sells_6m"] = n_sells

        # Top 3 vendedores (por valor total)
        sells_df = recent[recent.apply(is_sell, axis=1)].copy() if len(recent) > 0 else recent
        if len(sells_df) > 0 and "Insider" in sells_df.columns:
            top_sellers = (
                sells_df.groupby("Insider")
                .apply(lambda x: x["Value"].sum() if "Value" in x.columns else len(x), include_groups=False)
                .sort_values(ascending=False)
                .head(3)
                .index.tolist()
            )
            result["insider_top_sellers"] = top_sellers

        # Última transação
        if "Start Date" in recent.columns and len(recent) > 0:
            last = recent["Start Date"].max()
            if last is not None and hasattr(last, "isoformat"):
                result["insider_last_transaction_date"] = last.isoformat()

    except Exception as e:
        logger.debug(f"Falha em insider de {ticker}: {e}")

    return result


def ensure_db_columns(conn):
    """Garante que as colunas novas existam na tabela signals do DuckDB."""
    for col_name, col_type in NEW_COLUMNS_SQL.items():
        try:
            conn.execute(
                f"ALTER TABLE signals ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
            )
        except Exception as e:
            logger.debug(f"ALTER TABLE para {col_name} falhou (provavelmente já existe): {e}")


def update_db_row(conn, ticker: str, signal_date: str, enrichment: dict):
    """Atualiza linha do DuckDB com dados enriquecidos."""
    set_clauses = []
    values = []
    for col, val in enrichment.items():
        if col not in NEW_COLUMNS_SQL:
            continue
        # Serializa listas como JSON
        if isinstance(val, list):
            val = json.dumps(val, ensure_ascii=False)
        set_clauses.append(f"{col} = ?")
        values.append(val)

    if not set_clauses:
        return

    values.extend([ticker, signal_date])
    sql = f"UPDATE signals SET {', '.join(set_clauses)} WHERE ticker = ? AND signal_date = ?"
    conn.execute(sql, values)


def enrich_signals_file(signals_path: Path, conn) -> int:
    """
    Lê um arquivo parquet de sinais, enriquece cada ticker com fundamentals
    e insider data, salva de volta no parquet E atualiza o DuckDB.

    Retorna o número de tickers processados.
    """
    # Extrai signal_date do nome do arquivo (formato: YYYY-MM-DD_universe_X.parquet)
    date_match = re.match(
        r"(\d{4}-\d{2}-\d{2})_universe_[AB]\.parquet", signals_path.name
    )
    if not date_match:
        logger.error(f"Não consegui extrair data do nome do arquivo: {signals_path.name}")
        return 0
    signal_date = date_match.group(1)

    df = pl.read_parquet(signals_path)
    n = len(df)
    logger.info(f"Enriquecendo {n} sinais em {signals_path.name} (signal_date={signal_date})")

    enriched_rows = []
    for row in tqdm(df.iter_rows(named=True), total=n, desc=signals_path.stem):
        ticker = row["ticker"]
        current_price = row.get("close")
        fundamentals = fetch_fundamentals(ticker, current_price=current_price)
        insider = fetch_insider_summary(ticker)
        tier2 = fetch_tier2_metrics(ticker)

        # Atualiza DB
        enrichment = {**fundamentals, **insider, **tier2}
        try:
            update_db_row(conn, ticker, signal_date, enrichment)
        except Exception as e:
            logger.warning(f"Update DB falhou para {ticker}: {e}")

        merged = {**row, **fundamentals, **insider, **tier2}
        enriched_rows.append(merged)

    # Reconstrói DataFrame e escreve parquet
    enriched_df = pl.DataFrame(enriched_rows, strict=False)
    enriched_df.write_parquet(signals_path)
    logger.info(f"Salvou {n} sinais enriquecidos (parquet + DB)")
    return n


def enrich_today():
    """Enriquece os arquivos de sinais do dia atual (Universos A e B)."""
    today = datetime.now().strftime("%Y-%m-%d")
    files = [
        SIGNALS_DIR / f"{today}_universe_A.parquet",
        SIGNALS_DIR / f"{today}_universe_B.parquet",
    ]

    if not DB_PATH.exists():
        logger.error(f"DB não existe: {DB_PATH}")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH))
    try:
        ensure_db_columns(conn)

        total = 0
        for f in files:
            if not f.exists():
                logger.warning(f"Arquivo não existe: {f.name} — pulando")
                continue
            total += enrich_signals_file(f, conn)

        conn.commit()
    finally:
        conn.close()

    if total == 0:
        logger.error("Nenhum arquivo de sinais encontrado para hoje")
        sys.exit(1)

    print(f"\n✓ Enriquecidos {total} sinais com fundamentals + insider")


if __name__ == "__main__":
    enrich_today()
