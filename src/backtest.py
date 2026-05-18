"""
Equity Research Lab — Backtest Framework
==========================================

Walk-forward backtest do screener: pra cada dia útil em um período passado,
simula o que o screener teria recomendado e mede o que aconteceu depois.

LIMITAÇÕES:
  - Apenas filtros técnicos (Momentum + Setup) — fundamentals não são
    point-in-time no yfinance
  - Usa o universo atual (snapshot de tickers que existem hoje) — viés de
    survivorship
  - Ignora custos de transação

USO:
  # Backtest do último ano
  python src/backtest.py --start 2025-05-18 --end 2026-05-18

  # Backtest mais curto (rápido)
  python src/backtest.py --start 2026-01-01 --end 2026-04-30

  # Limitando aos top 100 tickers do universo
  python src/backtest.py --start 2026-01-01 --sample 100

  # Comparar com config alternativa
  python src/backtest.py --start 2026-01-01 --label "v2_rsi_40_70" --rsi-min 40 --rsi-max 70

OUTPUTS:
  - Tabela backtest_signals no DuckDB (1 linha por sinal hipotético)
  - Tabela backtest_outcomes no DuckDB (1 linha por sinal × janela)
  - Resumo impresso no console
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import yfinance as yf
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
UNIVERSE_PATH = PROJECT_DIR / "data" / "universe.parquet"


# ============================================================
# CONFIG
# ============================================================


@dataclass
class BacktestConfig:
    """Filtros do screener — espelha FilterConfig do screener_v2 mas só técnico."""

    min_avg_volume: int = 200_000
    min_price: float = 5.00
    max_price: float = 10_000
    rsi_min: float = 35.0
    rsi_max: float = 70.0
    must_be_above_mm50: bool = True
    max_distance_from_52w_high_pct: float = 25.0
    min_above_52w_low_pct: float = 20.0
    top_n_per_universe: int = 10
    label: str = "default"


# ============================================================
# SCHEMA DO BACKTEST
# ============================================================

BACKTEST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id BIGINT PRIMARY KEY,
    label VARCHAR,
    start_date DATE,
    end_date DATE,
    n_days INTEGER,
    n_signals INTEGER,
    config_json JSON,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    elapsed_seconds DOUBLE
);

CREATE SEQUENCE IF NOT EXISTS backtest_run_id_seq START 1;

CREATE TABLE IF NOT EXISTS backtest_signals (
    backtest_signal_id BIGINT PRIMARY KEY,
    run_id BIGINT,
    label VARCHAR,
    signal_date DATE,
    universe VARCHAR,
    ticker VARCHAR,
    sector VARCHAR,
    close DOUBLE,
    rsi14 DOUBLE,
    mm50 DOUBLE,
    composite_score DOUBLE,
    rank_position INTEGER
);

CREATE SEQUENCE IF NOT EXISTS backtest_signal_id_seq START 1;

CREATE TABLE IF NOT EXISTS backtest_outcomes (
    backtest_outcome_id BIGINT PRIMARY KEY,
    backtest_signal_id BIGINT,
    ticker VARCHAR,
    signal_date DATE,
    days_elapsed INTEGER,
    entry_price DOUBLE,
    price_at_measurement DOUBLE,
    high_since_signal DOUBLE,
    low_since_signal DOUBLE,
    pct_change DOUBLE,
    spy_pct_change DOUBLE,
    excess_return DOUBLE,
    won BOOLEAN
);

CREATE SEQUENCE IF NOT EXISTS backtest_outcome_id_seq START 1;
"""


def ensure_backtest_schema(conn):
    conn.execute(BACKTEST_SCHEMA_SQL)


# ============================================================
# DOWNLOAD HISTÓRICO DE PREÇOS (bulk)
# ============================================================


def download_full_history(
    tickers: list[str], start_date: str, end_date: str, batch_size: int = 100
) -> dict[str, pl.DataFrame]:
    """
    Baixa OHLCV histórico de todos os tickers via yf.download em batches.

    Retorna dict {ticker: polars DataFrame com colunas date, open, high, low, close, volume}.

    Buffer:
      - 380 dias antes do start_date pra ter MM200 e 52w high/low
      - end_date é sempre extendido até HOJE, pra ter dados de FUTURO necessários
        pros outcomes (signals de janeiro precisam de dados até abril pro 90d)
    """
    buffer_start = (
        datetime.fromisoformat(start_date) - timedelta(days=380)
    ).strftime("%Y-%m-%d")

    # Extende end_date até hoje pra capturar outcomes futuros
    today = datetime.now().strftime("%Y-%m-%d")
    download_end = max(end_date, today)

    logger.info(
        f"Baixando histórico de {len(tickers)} tickers de {buffer_start} a {download_end} "
        f"(em batches de {batch_size})..."
    )

    histories: dict[str, pl.DataFrame] = {}
    batches = [tickers[i : i + batch_size] for i in range(0, len(tickers), batch_size)]

    for batch in tqdm(batches, desc="Batches"):
        try:
            data = yf.download(
                tickers=batch,
                start=buffer_start,
                end=download_end,
                auto_adjust=True,
                actions=False,
                threads=True,
                progress=False,
                group_by="ticker",
            )
            if data is None or data.empty:
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        td = data
                    else:
                        if ticker not in data.columns.get_level_values(0):
                            continue
                        td = data[ticker]

                    td = td.dropna(how="all")
                    if td.empty or len(td) < 200:
                        continue

                    df = pl.from_pandas(td.reset_index())
                    df = df.rename({c: c.lower() for c in df.columns})
                    histories[ticker] = df
                except Exception:
                    continue

            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Batch falhou: {e}")
            continue

    logger.info(f"Sucesso: {len(histories)} / {len(tickers)} tickers com histórico")
    return histories


# ============================================================
# INDICADORES TÉCNICOS (NumPy puro pra performance)
# ============================================================


def calc_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI(14) com smoothing de Wilder."""
    deltas = np.diff(close)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(close)
    rsi[:period] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(period, len(close)):
        delta = deltas[i - 1]
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def calc_indicators_for_date(history: pl.DataFrame, target_date) -> dict | None:
    """
    Calcula indicadores técnicos do ticker considerando APENAS dados até target_date.
    Garante zero look-ahead bias.

    Retorna dict com close, rsi14, mm50, mm200, vol_avg_20, high_52w, low_52w,
    pct_below_52w_high, pct_above_52w_low — ou None se dados insuficientes.
    """
    # Filtra histórico até target_date (inclusive)
    h = history.filter(pl.col("date") <= target_date)
    if len(h) < 200:
        return None

    close = h["close"].to_numpy()
    high = h["high"].to_numpy()
    low = h["low"].to_numpy()
    volume = h["volume"].to_numpy()

    # MM50 e MM200
    mm50 = float(np.mean(close[-50:]))
    mm200 = float(np.mean(close[-200:]))

    # RSI(14)
    rsi = calc_rsi(close)
    rsi14 = float(rsi[-1])

    # Volume médio 20d
    vol_avg_20 = float(np.mean(volume[-20:]))

    # 52w high/low
    last_252 = close[-252:] if len(close) >= 252 else close
    high_252 = high[-252:] if len(high) >= 252 else high
    low_252 = low[-252:] if len(low) >= 252 else low
    high_52w = float(np.max(high_252))
    low_52w = float(np.min(low_252))
    current = float(close[-1])
    pct_below_52w_high = (high_52w - current) / current * 100
    pct_above_52w_low = (current - low_52w) / low_52w * 100

    return {
        "close": current,
        "rsi14": rsi14,
        "mm50": mm50,
        "mm200": mm200,
        "vol_avg_20": vol_avg_20,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_below_52w_high": pct_below_52w_high,
        "pct_above_52w_low": pct_above_52w_low,
        "volume": float(volume[-1]),
    }


# ============================================================
# RANKING (replica o composite_score do screener)
# ============================================================


def compute_composite_score(indicators: dict) -> float:
    """
    Replica o composite score MVP (Momentum 60% + Setup 40%) do screener.
    """
    # Momentum score (MA + RSI)
    dist_mm50 = (indicators["close"] - indicators["mm50"]) / indicators["mm50"] * 100
    ma_score = min(max(dist_mm50, 0), 30) / 30 * 100
    rsi_score = (indicators["rsi14"] - 35) / (70 - 35) * 100
    rsi_score = max(min(rsi_score, 100), 0)
    momentum_score = (ma_score + rsi_score) / 2

    # Setup score (volume + proximidade da high)
    vol_ratio = indicators["volume"] / indicators["vol_avg_20"] if indicators["vol_avg_20"] > 0 else 0
    vol_clipped = min(max(vol_ratio, 0.5), 3.0)
    vol_part = (vol_clipped - 0.5) / 2.5 * 100 * 0.5
    high_part = (100 - min(max(indicators["pct_below_52w_high"], 0), 25)) / 25 * 100 * 0.5
    setup_score = vol_part + high_part

    return momentum_score * 0.60 + setup_score * 0.40


# ============================================================
# OUTCOMES (preço futuro N dias depois)
# ============================================================


def compute_outcome(
    history: pl.DataFrame,
    signal_date,
    days_ahead: int,
) -> dict | None:
    """
    Mede o preço N dias úteis após signal_date.
    Retorna dict com entry_price, price_at_measurement, high_since, low_since, pct_change.
    """
    # Pega dados após signal_date
    future = history.filter(pl.col("date") > signal_date).head(days_ahead + 10)
    if future.is_empty():
        return None

    entry = history.filter(pl.col("date") <= signal_date).tail(1)
    if entry.is_empty():
        return None

    entry_price = float(entry["close"][0])

    # Pega exatamente o dia N — exige no mínimo days_ahead dias úteis disponíveis
    target_window = future.head(days_ahead)
    if len(target_window) < days_ahead:
        # Sem dados suficientes pra computar outcome dessa janela
        return None

    measurement = target_window.tail(1)
    price_at_measurement = float(measurement["close"][0])
    high_since = float(target_window["high"].max())
    low_since = float(target_window["low"].min())
    pct_change = (price_at_measurement - entry_price) / entry_price * 100

    return {
        "entry_price": entry_price,
        "price_at_measurement": price_at_measurement,
        "high_since_signal": high_since,
        "low_since_signal": low_since,
        "pct_change": pct_change,
    }


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================


def run_backtest(
    start_date: str,
    end_date: str,
    config: BacktestConfig,
    universe_filter: str | None = None,
    sample_size: int | None = None,
) -> dict:
    """Roda o backtest completo."""
    if not UNIVERSE_PATH.exists():
        logger.error(f"Universe não existe: {UNIVERSE_PATH}")
        logger.error("Rode primeiro: python 02_screener_v2.py --refresh-universe")
        sys.exit(1)

    universe = pl.read_parquet(UNIVERSE_PATH)
    if universe_filter:
        universe = universe.filter(pl.col("universe") == universe_filter)

    tickers = universe["ticker"].to_list()
    if sample_size and len(tickers) > sample_size:
        tickers = tickers[:sample_size]

    logger.info(f"Backtest: {len(tickers)} tickers, {start_date} a {end_date}")

    # 1. Baixa histórico completo (uma vez)
    t0 = time.time()
    histories = download_full_history(tickers, start_date, end_date)
    if not histories:
        logger.error("Nenhum histórico baixado")
        return {}

    # 1.5. Baixa SPY pra benchmark (também extende até hoje pros outcomes)
    today = datetime.now().strftime("%Y-%m-%d")
    spy_data = yf.download(
        "SPY",
        start=(datetime.fromisoformat(start_date) - timedelta(days=10)).strftime("%Y-%m-%d"),
        end=max(end_date, today),
        auto_adjust=True,
        progress=False,
    )
    spy_history = None
    if spy_data is not None and not spy_data.empty:
        # yfinance 1.3 retorna MultiIndex columns; achata
        import pandas as pd
        if isinstance(spy_data.columns, pd.MultiIndex):
            spy_data.columns = [col[0].lower() for col in spy_data.columns]
        else:
            spy_data.columns = [str(c).lower() for c in spy_data.columns]
        spy_history = pl.from_pandas(spy_data.reset_index())
        spy_history = spy_history.rename({c: str(c).lower() for c in spy_history.columns})

    elapsed_dl = time.time() - t0
    logger.info(f"Download completo em {elapsed_dl:.0f}s")

    # 2. Conecta no DB
    conn = duckdb.connect(str(DB_PATH))
    ensure_backtest_schema(conn)

    # 3. Cria run
    run_id = conn.execute("SELECT nextval('backtest_run_id_seq')").fetchone()[0]
    import json

    conn.execute(
        """
        INSERT INTO backtest_runs (run_id, label, start_date, end_date, config_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [run_id, config.label, start_date, end_date, json.dumps(config.__dict__)],
    )

    # 4. Gera datas úteis no range
    start_dt = datetime.fromisoformat(start_date).date()
    end_dt = datetime.fromisoformat(end_date).date()
    business_days = []
    current = start_dt
    while current <= end_dt:
        # 0=monday, 4=friday
        if current.weekday() < 5:
            business_days.append(current)
        current += timedelta(days=1)

    logger.info(f"{len(business_days)} dias úteis no range")

    # 5. Walk-forward
    total_signals = 0
    t1 = time.time()

    universe_map = {row["ticker"]: row["universe"] for row in universe.iter_rows(named=True)}
    sector_map = {row["ticker"]: row.get("sector") for row in universe.iter_rows(named=True)}

    for bday in tqdm(business_days, desc="Walk-forward"):
        # Pra cada ticker, calcula indicadores no dia
        candidates = []
        for ticker, hist in histories.items():
            if ticker not in universe_map:
                continue
            uni_flag = universe_map[ticker]
            sector = sector_map.get(ticker)

            ind = calc_indicators_for_date(hist, bday)
            if ind is None:
                continue

            # Aplica filtros
            if ind["close"] < config.min_price or ind["close"] > config.max_price:
                continue
            if ind["vol_avg_20"] < config.min_avg_volume:
                continue
            if ind["rsi14"] < config.rsi_min or ind["rsi14"] > config.rsi_max:
                continue
            if config.must_be_above_mm50 and ind["close"] < ind["mm50"]:
                continue
            if ind["pct_below_52w_high"] > config.max_distance_from_52w_high_pct:
                continue
            if ind["pct_above_52w_low"] < config.min_above_52w_low_pct:
                continue

            ind["ticker"] = ticker
            ind["universe"] = uni_flag
            ind["sector"] = sector
            ind["composite_score"] = compute_composite_score(ind)
            candidates.append(ind)

        if not candidates:
            continue

        # Ordena e pega top N por universo
        for uni_flag in ["A", "B"]:
            uni_candidates = sorted(
                [c for c in candidates if c["universe"] == uni_flag],
                key=lambda x: x["composite_score"],
                reverse=True,
            )[: config.top_n_per_universe]

            for rank, c in enumerate(uni_candidates, start=1):
                # Insere signal
                sig_id = conn.execute(
                    "SELECT nextval('backtest_signal_id_seq')"
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO backtest_signals (
                        backtest_signal_id, run_id, label, signal_date, universe,
                        ticker, sector, close, rsi14, mm50, composite_score, rank_position
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        sig_id, run_id, config.label, bday, uni_flag,
                        c["ticker"], c["sector"], c["close"], c["rsi14"], c["mm50"],
                        c["composite_score"], rank,
                    ],
                )
                total_signals += 1

                # Calcula outcomes pra cada janela
                hist = histories[c["ticker"]]
                for days in [7, 30, 60, 90]:
                    outcome = compute_outcome(hist, bday, days)
                    if outcome is None:
                        continue

                    spy_pct = None
                    if spy_history is not None:
                        spy_outcome = compute_outcome(spy_history, bday, days)
                        if spy_outcome is not None:
                            spy_pct = spy_outcome["pct_change"]

                    excess = outcome["pct_change"] - spy_pct if spy_pct is not None else None

                    out_id = conn.execute(
                        "SELECT nextval('backtest_outcome_id_seq')"
                    ).fetchone()[0]
                    conn.execute(
                        """
                        INSERT INTO backtest_outcomes (
                            backtest_outcome_id, backtest_signal_id, ticker, signal_date,
                            days_elapsed, entry_price, price_at_measurement,
                            high_since_signal, low_since_signal, pct_change,
                            spy_pct_change, excess_return, won
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            out_id, sig_id, c["ticker"], bday, days,
                            outcome["entry_price"], outcome["price_at_measurement"],
                            outcome["high_since_signal"], outcome["low_since_signal"],
                            outcome["pct_change"], spy_pct, excess,
                            outcome["pct_change"] > 0,
                        ],
                    )

    conn.commit()

    elapsed_total = time.time() - t0
    conn.execute(
        "UPDATE backtest_runs SET n_signals = ?, elapsed_seconds = ?, n_days = ? WHERE run_id = ?",
        [total_signals, elapsed_total, len(business_days), run_id],
    )
    conn.commit()

    # 6. Imprime resumo
    print("\n" + "=" * 70)
    print(f"BACKTEST CONCLUÍDO — run_id={run_id} label={config.label}")
    print("=" * 70)
    print(f"Período: {start_date} a {end_date} ({len(business_days)} dias úteis)")
    print(f"Tickers analisados: {len(histories)}")
    print(f"Sinais gerados: {total_signals}")
    print(f"Tempo total: {elapsed_total:.0f}s")
    print()

    # Resumo de performance
    summary = conn.execute(
        """
        SELECT
            days_elapsed,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN won THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate_pct,
            ROUND(AVG(pct_change), 2) AS avg_return_pct,
            ROUND(AVG(excess_return), 2) AS avg_excess_vs_spy,
            ROUND(MIN(pct_change), 2) AS worst_pct,
            ROUND(MAX(pct_change), 2) AS best_pct
        FROM backtest_outcomes
        WHERE backtest_signal_id IN (
            SELECT backtest_signal_id FROM backtest_signals WHERE run_id = ?
        )
        GROUP BY days_elapsed
        ORDER BY days_elapsed
        """,
        [run_id],
    ).df()

    print("Performance por janela temporal:")
    print(summary.to_string(index=False))

    conn.close()
    return {"run_id": run_id, "n_signals": total_signals, "elapsed": elapsed_total}


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="Backtest do Equity Research Lab")
    parser.add_argument("--start", required=True, help="Data inicial (YYYY-MM-DD)")
    parser.add_argument("--end", help="Data final (YYYY-MM-DD, default: hoje)")
    parser.add_argument("--label", default="default", help="Label do run")
    parser.add_argument("--sample", type=int, help="Limitar a N tickers")
    parser.add_argument("--universe", choices=["A", "B"], help="Só um universo")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rsi-min", type=float, default=35)
    parser.add_argument("--rsi-max", type=float, default=70)
    parser.add_argument("--max-from-high", type=float, default=25)

    args = parser.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")

    config = BacktestConfig(
        label=args.label,
        top_n_per_universe=args.top_n,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        max_distance_from_52w_high_pct=args.max_from_high,
    )

    run_backtest(
        start_date=args.start,
        end_date=end,
        config=config,
        universe_filter=args.universe,
        sample_size=args.sample,
    )


if __name__ == "__main__":
    main()
