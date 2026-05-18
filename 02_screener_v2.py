"""
Equity Research Lab — Layer 1 + Layer 2 (V2 — Dual Universe)
=============================================================

Versão atualizada com suporte a DOIS UNIVERSOS paralelos:
- Universo A (estável):  mid/large caps, $2B  - $500B  (~1.500 nomes)
- Universo B (alpha):    small caps,     $300M -  $2B  (~2.500 nomes)

Sinais e análises são armazenados com flag `universe` ('A' ou 'B') no DuckDB,
permitindo comparar hit rate entre os dois universos ao longo do tempo.

USO:
    # Modo padrão: roda ambos universos
    python screener.py --top-n-per-universe 5

    # Apenas um universo
    python screener.py --universe A --top-n-per-universe 10
    python screener.py --universe B --top-n-per-universe 10

    # Modo teste rápido
    python screener.py --sample 100 --universe A

    # Atualiza lista de tickers
    python screener.py --refresh-universe

INSTALAÇÃO:
    pip install yfinance polars duckdb requests ta-lib-binary tqdm python-dotenv

ESTRUTURA DE SAÍDA:
    data/
        universe.parquet                       # Lista de tickers + flag de universo
        prices/YYYY-MM-DD.parquet              # OHLCV diário
        signals/YYYY-MM-DD_universe_A.parquet  # Top N do Universo A
        signals/YYYY-MM-DD_universe_B.parquet  # Top N do Universo B
        research_lab.duckdb                    # DB principal
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl
import requests
import yfinance as yf
from tqdm import tqdm

try:
    import talib

    USE_TALIB = True
except ImportError:
    USE_TALIB = False
    logging.warning("ta-lib não instalado; usando implementações nativas")


# =====================================================================
# CONFIGURAÇÃO
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

UniverseFlag = Literal["A", "B"]


@dataclass
class UniverseConfig:
    """Define um universo (faixa de market cap)."""

    flag: UniverseFlag
    name: str
    min_market_cap_usd: float
    max_market_cap_usd: float


UNIVERSE_A = UniverseConfig(
    flag="A",
    name="Mid/Large Caps",
    min_market_cap_usd=2_000_000_000,    # $2B
    max_market_cap_usd=500_000_000_000,  # $500B
)

UNIVERSE_B = UniverseConfig(
    flag="B",
    name="Small Caps",
    min_market_cap_usd=300_000_000,    # $300M
    max_market_cap_usd=2_000_000_000,  # $2B
)


@dataclass
class FilterConfig:
    """Configuração dos filtros do screener. Tudo ajustável."""

    # Filtros básicos (independentes do universo)
    min_avg_volume: int = 200_000
    min_price: float = 5.00
    max_price: float = 10_000

    # Filtros técnicos
    rsi_min: float = 35.0
    rsi_max: float = 70.0
    must_be_above_mm50: bool = True
    max_distance_from_52w_high_pct: float = 25.0
    min_above_52w_low_pct: float = 20.0

    # Lookback
    history_days: int = 252
    volume_lookback: int = 20

    # Output (por universo) — Top 10 por universo = Top 20 total
    top_n_per_universe: int = 10

    # Versionamento (gravado no DuckDB)
    screener_version: str = "v2.0_dual_universe"


# Pesos do composite score (Fase MVP - apenas Momentum e Setup disponíveis)
RANKING_WEIGHTS_MVP = {
    "momentum": 0.60,
    "setup": 0.40,
}


# =====================================================================
# UNIVERSO — Tickers NYSE + NASDAQ com market cap
# =====================================================================


def fetch_us_ticker_universe() -> pl.DataFrame:
    """
    Baixa lista oficial de tickers NYSE + NASDAQ.
    """
    logger.info("Baixando universo NYSE + NASDAQ...")

    urls = {
        "nasdaq": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "other": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    }

    dfs = []
    for exchange, url in urls.items():
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pl.read_csv(StringIO(resp.text), separator="|")
            df = df.filter(pl.col(df.columns[0]).str.contains("File Creation") == False)
            df = df.with_columns(pl.lit(exchange).alias("source"))
            dfs.append(df)
            logger.info(f"  {exchange}: {len(df)} tickers")
        except Exception as e:
            logger.error(f"Falha ao baixar {exchange}: {e}")

    if not dfs:
        raise RuntimeError("Falha ao baixar universo")

    universe_dfs = []
    for df in dfs:
        cols_lower = {c: c.lower().replace(" ", "_") for c in df.columns}
        df = df.rename(cols_lower)
        ticker_col = next(
            (c for c in df.columns if "symbol" in c.lower() or c == "act_symbol"), None
        )
        if ticker_col and ticker_col != "ticker":
            df = df.rename({ticker_col: "ticker"})

        keep_cols = ["ticker"]
        for col in ["security_name", "company", "etf", "market_category", "test_issue"]:
            if col in df.columns:
                keep_cols.append(col)
        universe_dfs.append(df.select(keep_cols))

    universe = pl.concat(universe_dfs, how="diagonal").unique(subset=["ticker"])
    universe = universe.filter(
        ~pl.col("ticker").str.contains(r"\$"),
        ~pl.col("ticker").str.contains(r"\."),
    )

    if "etf" in universe.columns:
        universe = universe.filter(pl.col("etf") != "Y")
    if "test_issue" in universe.columns:
        universe = universe.filter(pl.col("test_issue") != "Y")

    logger.info(f"Universo bruto: {len(universe)} tickers (antes de market cap)")
    return universe


def fetch_market_caps(tickers: list[str], max_workers: int = 8) -> pl.DataFrame:
    """
    Busca market cap via yfinance para cada ticker.
    Retorna DataFrame: ticker, market_cap_usd.

    Esta é uma operação custosa (1 chamada por ticker). Roda só ao
    rebaixar o universo (--refresh-universe), depois fica em cache.
    """
    logger.info(f"Buscando market caps de {len(tickers)} tickers...")
    results = []

    def _fetch_one(ticker: str) -> dict | None:
        try:
            info = yf.Ticker(ticker).info
            mc = info.get("marketCap")
            sector = info.get("sector", None)
            industry = info.get("industry", None)
            if mc and mc > 0:
                return {
                    "ticker": ticker,
                    "market_cap_usd": float(mc),
                    "sector": sector,
                    "industry": industry,
                }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_fetch_one, t): t for t in tickers}
        for fut in tqdm(as_completed(futures), total=len(tickers), desc="Market caps"):
            res = fut.result()
            if res is not None:
                results.append(res)

    df = pl.DataFrame(results)
    logger.info(f"Market caps obtidos: {len(df)} / {len(tickers)}")
    return df


def assign_universes(universe_df: pl.DataFrame) -> pl.DataFrame:
    """
    Atribui flag 'A' ou 'B' a cada ticker baseado no market cap.
    Tickers fora dos ranges ficam com universe = None (descartados).
    """
    df = universe_df.with_columns(
        pl.when(
            (pl.col("market_cap_usd") >= UNIVERSE_A.min_market_cap_usd)
            & (pl.col("market_cap_usd") <= UNIVERSE_A.max_market_cap_usd)
        )
        .then(pl.lit("A"))
        .when(
            (pl.col("market_cap_usd") >= UNIVERSE_B.min_market_cap_usd)
            & (pl.col("market_cap_usd") < UNIVERSE_B.max_market_cap_usd)
        )
        .then(pl.lit("B"))
        .otherwise(pl.lit(None))
        .alias("universe")
    )

    df = df.filter(pl.col("universe").is_not_null())

    counts = df.group_by("universe").len()
    logger.info(f"Distribuição por universo:\n{counts}")

    return df


# =====================================================================
# DOWNLOAD DE PREÇOS (yfinance)
# =====================================================================


def download_price_history(
    tickers: list[str], days: int = 252, max_workers: int = 10
) -> pl.DataFrame:
    """Baixa histórico via yfinance em paralelo."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=int(days * 1.5))

    logger.info(f"Baixando preços de {len(tickers)} tickers...")
    results = []
    failed = []

    def _fetch_one(ticker: str) -> pl.DataFrame | None:
        try:
            hist = yf.Ticker(ticker).history(
                start=start_date, end=end_date, auto_adjust=True, actions=False
            )
            if hist.empty or len(hist) < 50:
                return None
            df = pl.from_pandas(hist.reset_index())
            df = df.with_columns(pl.lit(ticker).alias("ticker"))
            df = df.rename({c: c.lower() for c in df.columns})
            return df
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_fetch_one, t): t for t in tickers}
        for fut in tqdm(as_completed(futures), total=len(tickers), desc="Preços"):
            ticker = futures[fut]
            df = fut.result()
            if df is not None:
                results.append(df)
            else:
                failed.append(ticker)

    logger.info(f"Preços: sucesso {len(results)}, falhou {len(failed)}")
    if not results:
        return pl.DataFrame()

    return pl.concat(results, how="diagonal")


# =====================================================================
# CÁLCULO DE INDICADORES TÉCNICOS
# =====================================================================


def compute_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """Computa indicadores por ticker."""
    df = df.sort(["ticker", "date"])
    results = []

    for ticker_name, group in df.group_by("ticker"):
        group = group.sort("date")
        if len(group) < 50:
            continue

        close = group["close"].to_numpy()
        high = group["high"].to_numpy()
        low = group["low"].to_numpy()
        volume = group["volume"].to_numpy()

        for window in [5, 21, 50, 100, 200]:
            if len(close) >= window:
                ma_values = pl.Series(close).rolling_mean(window).to_numpy()
            else:
                ma_values = [None] * len(close)
            group = group.with_columns(pl.Series(f"mm{window}", ma_values))

        if USE_TALIB:
            rsi = talib.RSI(close, timeperiod=14)
            atr = talib.ATR(high, low, close, timeperiod=14)
        else:
            rsi = _rsi_native(close, 14)
            atr = _atr_native(high, low, close, 14)

        group = group.with_columns(
            pl.Series("rsi14", rsi),
            pl.Series("atr14", atr),
        )

        vol_avg_20 = pl.Series(volume).rolling_mean(20).to_numpy()
        group = group.with_columns(pl.Series("vol_avg_20", vol_avg_20))

        last_252 = group.tail(252)
        high_52w = last_252["high"].max()
        low_52w = last_252["low"].min()
        group = group.with_columns(
            pl.lit(high_52w).alias("high_52w"),
            pl.lit(low_52w).alias("low_52w"),
        )
        results.append(group)

    return pl.concat(results, how="diagonal") if results else pl.DataFrame()


def _rsi_native(close, period=14):
    import numpy as np

    close = np.asarray(close, dtype=float)
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


def _atr_native(high, low, close, period=14):
    import numpy as np

    high, low, close = map(lambda x: np.asarray(x, dtype=float), (high, low, close))
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))),
    )
    tr[0] = high[0] - low[0]
    return pl.Series(tr).rolling_mean(period).to_numpy()


# =====================================================================
# FILTROS E RANKING
# =====================================================================


def apply_filters(
    df: pl.DataFrame, config: FilterConfig, universe_meta: pl.DataFrame
) -> pl.DataFrame:
    """Aplica filtros básicos e técnicos. Recebe df de últimas obs + universe_meta com market cap."""
    df = df.join(universe_meta.select(["ticker", "market_cap_usd", "universe", "sector"]), on="ticker", how="inner")

    # Básicos
    df = df.filter(
        pl.col("close") >= config.min_price,
        pl.col("close") <= config.max_price,
        pl.col("vol_avg_20") >= config.min_avg_volume,
    )

    # Técnicos
    df = df.filter(
        pl.col("rsi14") >= config.rsi_min,
        pl.col("rsi14") <= config.rsi_max,
    )

    if config.must_be_above_mm50:
        df = df.filter(pl.col("close") >= pl.col("mm50"))

    df = df.with_columns(
        ((pl.col("high_52w") - pl.col("close")) / pl.col("close") * 100).alias(
            "pct_below_52w_high"
        ),
        ((pl.col("close") - pl.col("low_52w")) / pl.col("low_52w") * 100).alias(
            "pct_above_52w_low"
        ),
    )

    df = df.filter(
        pl.col("pct_below_52w_high") <= config.max_distance_from_52w_high_pct,
        pl.col("pct_above_52w_low") >= config.min_above_52w_low_pct,
    )

    return df


def rank_candidates(df: pl.DataFrame) -> pl.DataFrame:
    """Calcula composite score (versão MVP com 2 fatores) e ranqueia."""
    # Momentum score
    df = df.with_columns(
        ((pl.col("close") - pl.col("mm50")) / pl.col("mm50") * 100).alias("dist_mm50"),
        ((pl.col("rsi14") - 35) / (70 - 35) * 100).alias("rsi_score_raw"),
    )
    df = df.with_columns(
        pl.col("dist_mm50").clip(0, 30).truediv(30).mul(100).alias("ma_score"),
        pl.col("rsi_score_raw").clip(0, 100).alias("rsi_score"),
    )
    df = df.with_columns(
        ((pl.col("ma_score") + pl.col("rsi_score")) / 2).alias("momentum_score")
    )

    # Setup score
    df = df.with_columns(
        (pl.col("volume") / pl.col("vol_avg_20")).alias("vol_ratio"),
    )
    df = df.with_columns(
        (
            (pl.col("vol_ratio").clip(0.5, 3.0) - 0.5) / (3.0 - 0.5) * 100 * 0.5
            + (100 - pl.col("pct_below_52w_high").clip(0, 25)) / 25 * 100 * 0.5
        ).alias("setup_score"),
    )

    # Composite (MVP weights)
    df = df.with_columns(
        (
            pl.col("momentum_score") * RANKING_WEIGHTS_MVP["momentum"]
            + pl.col("setup_score") * RANKING_WEIGHTS_MVP["setup"]
        ).alias("composite_score")
    )

    return df


# =====================================================================
# PIPELINE PRINCIPAL
# =====================================================================


def run_screener(
    output_dir: Path,
    config: FilterConfig,
    refresh_universe: bool = False,
    sample_size: int | None = None,
    universe_filter: str | None = None,
) -> dict:
    """
    Roda pipeline completo. Retorna dict {universe_flag: top_n_df}.

    Args:
        output_dir: pasta de output
        config: filtros
        refresh_universe: rebaixar lista de tickers + market caps
        sample_size: para teste, limita N tickers totais
        universe_filter: 'A', 'B', ou None (ambos)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. UNIVERSE com market caps
    universe_path = output_dir / "universe.parquet"
    if refresh_universe or not universe_path.exists():
        raw = fetch_us_ticker_universe()
        tickers_to_query = raw["ticker"].to_list()
        if sample_size:
            tickers_to_query = tickers_to_query[:sample_size]
        market_caps = fetch_market_caps(tickers_to_query)
        universe = market_caps  # já tem ticker + market_cap + sector + industry
        universe = assign_universes(universe)
        universe.write_parquet(universe_path)
        logger.info(f"Universe salvo: {universe_path}")
    else:
        universe = pl.read_parquet(universe_path)
        logger.info(f"Universe carregado do cache: {len(universe)} tickers")

    # Filtro de universe se especificado
    if universe_filter:
        universe = universe.filter(pl.col("universe") == universe_filter)
        logger.info(f"Universo {universe_filter} apenas: {len(universe)} tickers")

    tickers = universe["ticker"].to_list()
    if sample_size and len(tickers) > sample_size:
        tickers = tickers[:sample_size]
        logger.info(f"SAMPLE: limitando a {sample_size} tickers")

    # 2. PRICES
    prices = download_price_history(tickers, days=config.history_days)
    if prices.is_empty():
        logger.error("Nenhum preço baixado")
        return {}

    prices_path = output_dir / "prices" / f"{today_str}.parquet"
    prices_path.parent.mkdir(exist_ok=True)
    prices.write_parquet(prices_path)

    # 3. INDICATORS
    with_ind = compute_indicators(prices)
    if with_ind.is_empty():
        return {}

    # 4. LATEST PER TICKER
    latest = with_ind.sort(["ticker", "date"]).group_by("ticker").last()
    latest = latest.drop_nulls(["rsi14", "mm50"])
    logger.info(f"Tickers válidos após indicadores: {len(latest)}")

    # 5. FILTROS
    filtered = apply_filters(latest, config, universe)
    logger.info(f"Após filtros: {len(filtered)} tickers")
    if filtered.is_empty():
        logger.warning("Nenhum ticker passou nos filtros")
        return {}

    # 6. RANKING e SPLIT POR UNIVERSO
    ranked = rank_candidates(filtered)
    db_path = output_dir / "research_lab.duckdb"
    results = {}

    for uni_flag in ["A", "B"]:
        if universe_filter and uni_flag != universe_filter:
            continue

        uni_df = ranked.filter(pl.col("universe") == uni_flag).sort(
            "composite_score", descending=True
        ).head(config.top_n_per_universe)

        if uni_df.is_empty():
            logger.warning(f"Universo {uni_flag}: nenhum candidato")
            continue

        # Salva por universo
        sig_path = output_dir / "signals" / f"{today_str}_universe_{uni_flag}.parquet"
        sig_path.parent.mkdir(exist_ok=True)
        uni_df.write_parquet(sig_path)

        # Insere no DB
        _save_signals_to_db(uni_df, db_path, today_str, uni_flag, config.screener_version)

        results[uni_flag] = uni_df
        logger.info(f"Universo {uni_flag}: top {len(uni_df)} salvo")

    return results


def _save_signals_to_db(
    signals_df: pl.DataFrame,
    db_path: Path,
    signal_date: str,
    universe: str,
    version: str,
):
    """Insere sinais no DuckDB com flag de universo."""
    conn = duckdb.connect(str(db_path))
    try:
        # Garante coluna universe na tabela
        conn.execute(
            """
            ALTER TABLE signals ADD COLUMN IF NOT EXISTS universe VARCHAR;
            ALTER TABLE signals ADD COLUMN IF NOT EXISTS screener_version VARCHAR;
            ALTER TABLE signals ADD COLUMN IF NOT EXISTS sector VARCHAR;
            ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_cap_usd DOUBLE;
            """
        )

        signals = signals_df.with_row_count("rank_position", offset=1)
        signals = signals.with_columns(
            pl.lit(signal_date).alias("signal_date"),
            pl.lit(universe).alias("universe"),
            pl.lit(version).alias("screener_version"),
        )

        cols = [
            "signal_date",
            "ticker",
            "universe",
            "screener_version",
            "sector",
            "market_cap_usd",
            "close",
            "vol_avg_20",
            "rsi14",
            "mm50",
            "mm200",
            "high_52w",
            "low_52w",
            "pct_below_52w_high",
            "pct_above_52w_low",
            "momentum_score",
            "setup_score",
            "composite_score",
            "rank_position",
        ]
        cols_present = [c for c in cols if c in signals.columns]

        arrow_data = signals.select(cols_present).to_arrow()
        conn.register("temp_signals", arrow_data)
        conn.execute(
            f"INSERT INTO signals (signal_id, {','.join(cols_present)}) "
            f"SELECT nextval('signal_id_seq'), {','.join(cols_present)} FROM temp_signals"
        )
        conn.unregister("temp_signals")
        logger.info(f"Inseridos {len(signals)} sinais no DB (Universe {universe})")
    finally:
        conn.close()


# =====================================================================
# CLI
# =====================================================================


def main():
    parser = argparse.ArgumentParser(description="Equity Research Lab Screener v2 — Dual Universe")
    parser.add_argument("--output-dir", type=Path, default=Path("./data"))
    parser.add_argument("--top-n-per-universe", type=int, default=10)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument(
        "--universe",
        choices=["A", "B"],
        default=None,
        help="Roda só um universo (default: ambos)",
    )

    args = parser.parse_args()

    config = FilterConfig(top_n_per_universe=args.top_n_per_universe)

    t0 = time.time()
    results = run_screener(
        output_dir=args.output_dir,
        config=config,
        refresh_universe=args.refresh_universe,
        sample_size=args.sample,
        universe_filter=args.universe,
    )
    elapsed = time.time() - t0

    if not results:
        print("\nNenhum candidato encontrado")
        sys.exit(1)

    for uni_flag, df in results.items():
        uni_name = "Mid/Large Caps" if uni_flag == "A" else "Small Caps"
        print(f"\n{'=' * 70}")
        print(f"TOP {len(df)} — UNIVERSO {uni_flag} ({uni_name})")
        print(f"{'=' * 70}")
        print(
            df.select(
                [
                    "ticker",
                    "sector",
                    "close",
                    "rsi14",
                    "pct_below_52w_high",
                    "composite_score",
                ]
            )
            .to_pandas()
            .to_string(index=False)
        )

    print(f"\nTempo total: {elapsed:.1f}s")
    print(f"Outputs em: {args.output_dir}")


if __name__ == "__main__":
    main()
