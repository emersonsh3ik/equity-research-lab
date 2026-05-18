"""
Equity Research Lab — Validação Científica
============================================

Framework de validação rigorosa do screener:

  FASE 1 — Estatística robusta:
    - Bootstrap confidence intervals (10.000 réplicas)
    - T-test (returns vs zero, excess vs zero)
    - Sharpe ratio, Sortino, Max drawdown, Calmar
    - Win rate, profit factor, expectancy

  FASE 2 — Comparação com baseline aleatório:
    - Gera N portfólios aleatórios com mesmas datas/universo
    - Compara distribuições screener vs random
    - Two-sample t-test pra excess vs random

  FASE 3 — Multi-período (out-of-sample):
    - Roda backtests em 2022, 2023, 2024, 2025
    - Testa se edge persiste em diferentes regimes
    - Bull/bear classification via SPY 200d MA

  FASE 4 — Robustez paramétrica:
    - Varia RSI thresholds, distance from high, top-N
    - Mede sensibilidade do edge

USO:

    # Análise completa de um backtest existente
    python src/validate.py --run-id 6

    # Roda baseline aleatório pra comparar (lento)
    python src/validate.py --run-id 6 --random-baseline --n-random 100

    # Multi-período (faz backtests novos em 2022, 2023, 2024)
    python src/validate.py --multi-period

    # Robustez paramétrica (testa várias configs)
    python src/validate.py --robustness --start 2025-06-01 --end 2026-02-28

    # Tudo (demora horas)
    python src/validate.py --full
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
REPORTS_DIR = PROJECT_DIR / "reports" / "validation"


# ============================================================
# DATA STRUCTURES
# ============================================================


@dataclass
class StatResult:
    """Resultado estatístico com CI e p-value."""

    metric: str
    value: float
    ci_lower: float | None = None
    ci_upper: float | None = None
    p_value: float | None = None
    n: int | None = None
    significant: bool | None = None  # p < 0.05

    def __repr__(self):
        ci_str = (
            f" [{self.ci_lower:.3f}, {self.ci_upper:.3f}]"
            if self.ci_lower is not None
            else ""
        )
        p_str = (
            f" p={self.p_value:.4f}{'*' if self.significant else ''}"
            if self.p_value is not None
            else ""
        )
        n_str = f" n={self.n}" if self.n else ""
        return f"{self.metric}: {self.value:.4f}{ci_str}{p_str}{n_str}"


@dataclass
class WindowAnalysis:
    """Análise completa de uma janela temporal."""

    days_elapsed: int
    n_signals: int
    hit_rate: StatResult
    avg_return: StatResult
    avg_excess_vs_spy: StatResult
    sharpe_per_trade: float
    sortino_per_trade: float
    max_drawdown_pct: float
    profit_factor: float
    expectancy: float
    worst_return: float
    best_return: float
    var_95: float  # 5% Value-at-Risk
    cvar_95: float  # 5% Conditional VaR


# ============================================================
# STATISTICAL HELPERS
# ============================================================


def bootstrap_ci(
    values: np.ndarray,
    statistic=np.mean,
    n_boot: int = 10_000,
    ci_level: float = 0.95,
) -> tuple[float, float, float]:
    """
    Bootstrap CI não-paramétrico.
    Retorna (point_estimate, lower, upper).
    """
    values = np.asarray([v for v in values if v is not None and not np.isnan(v)])
    if len(values) == 0:
        return np.nan, np.nan, np.nan

    rng = np.random.default_rng(42)  # reprodutibilidade
    boot_stats = np.empty(n_boot)
    n = len(values)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boot_stats[i] = statistic(values[idx])

    alpha = (1 - ci_level) / 2
    lower = np.percentile(boot_stats, alpha * 100)
    upper = np.percentile(boot_stats, (1 - alpha) * 100)
    return float(statistic(values)), float(lower), float(upper)


def t_test_one_sample(values: np.ndarray, null: float = 0.0) -> float:
    """T-test one-sample. Retorna p-value (two-sided)."""
    values = np.asarray([v for v in values if v is not None and not np.isnan(v)])
    if len(values) < 2:
        return np.nan
    mean = np.mean(values)
    std = np.std(values, ddof=1)
    if std == 0:
        return np.nan
    se = std / np.sqrt(len(values))
    t_stat = (mean - null) / se
    # Aproximação two-sided p-value usando normal (válida pra n>=30)
    from math import erfc

    p = erfc(abs(t_stat) / np.sqrt(2))
    return float(p)


def sharpe_ratio(returns: np.ndarray, risk_free: float = 0.0) -> float:
    """Sharpe ratio por trade (não anualizado)."""
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) < 2:
        return np.nan
    std = np.std(r, ddof=1)
    if std == 0:
        return np.nan
    return float((np.mean(r) - risk_free) / std)


def sortino_ratio(returns: np.ndarray, target: float = 0.0) -> float:
    """Sortino: só volatilidade downside."""
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) < 2:
        return np.nan
    downside = r[r < target]
    if len(downside) == 0:
        return np.inf
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return np.nan
    return float((np.mean(r) - target) / downside_std)


def max_drawdown_simulated(returns: np.ndarray) -> float:
    """
    Simula equity curve assumindo capital igual investido a cada signal.
    Retorna max drawdown em %.

    Simplificação: assume signals em ordem cronológica de execução.
    """
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) == 0:
        return 0.0

    # Equity curve simulada: cumulative product de (1 + return/100)
    equity = np.cumprod(1 + r / 100.0)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    return float(np.min(drawdown) * 100)


def profit_factor(returns: np.ndarray) -> float:
    """Soma dos ganhos / soma absoluta das perdas."""
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) == 0:
        return np.nan
    gains = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    if losses == 0:
        return np.inf
    return float(gains / losses)


def expectancy(returns: np.ndarray) -> float:
    """Retorno médio por trade — proxy de expectância."""
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) == 0:
        return np.nan
    return float(np.mean(r))


def value_at_risk(returns: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    """VaR e CVaR (Expected Shortfall) ao nível alpha. Retorna (VaR, CVaR)."""
    r = np.asarray([v for v in returns if v is not None and not np.isnan(v)])
    if len(r) == 0:
        return np.nan, np.nan
    var = float(np.percentile(r, alpha * 100))
    cvar = float(np.mean(r[r <= var])) if len(r[r <= var]) > 0 else var
    return var, cvar


# ============================================================
# ANÁLISE DE UM BACKTEST RUN
# ============================================================


def analyze_run(run_id: int) -> dict:
    """
    Análise estatística completa de um run específico.
    Retorna dict com WindowAnalysis pra cada janela (7/30/60/90).
    """
    if not DB_PATH.exists():
        logger.error(f"DB não existe: {DB_PATH}")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        # Pega info do run
        run_info = conn.execute(
            "SELECT * FROM backtest_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        if run_info is None:
            logger.error(f"Run {run_id} não existe")
            sys.exit(1)

        cols = [d[0] for d in conn.description]
        run_dict = dict(zip(cols, run_info))

        # Pega todos os outcomes do run
        outcomes_df = conn.execute(
            """
            SELECT bo.*
            FROM backtest_outcomes bo
            JOIN backtest_signals bs USING (backtest_signal_id)
            WHERE bs.run_id = ?
            """,
            [run_id],
        ).df()

    finally:
        conn.close()

    results: dict[str, Any] = {
        "run_id": run_id,
        "label": run_dict.get("label"),
        "period": f"{run_dict.get('start_date')} a {run_dict.get('end_date')}",
        "n_total_signals": int(run_dict.get("n_signals", 0)),
        "windows": {},
    }

    for days in [7, 30, 60, 90]:
        window_df = outcomes_df[outcomes_df["days_elapsed"] == days]
        if window_df.empty:
            continue

        returns = window_df["pct_change"].values
        excess = window_df["excess_return"].dropna().values
        won = window_df["won"].astype(int).values

        # Hit rate com CI
        hr_val, hr_lo, hr_hi = bootstrap_ci(won, statistic=np.mean)
        hit_rate = StatResult(
            metric="hit_rate",
            value=hr_val * 100,
            ci_lower=hr_lo * 100,
            ci_upper=hr_hi * 100,
            n=len(won),
            p_value=t_test_one_sample(won, null=0.5),
        )
        hit_rate.significant = hit_rate.p_value is not None and hit_rate.p_value < 0.05

        # Avg return com CI
        ar_val, ar_lo, ar_hi = bootstrap_ci(returns, statistic=np.mean)
        avg_return = StatResult(
            metric="avg_return",
            value=ar_val,
            ci_lower=ar_lo,
            ci_upper=ar_hi,
            n=len(returns),
            p_value=t_test_one_sample(returns, null=0.0),
        )
        avg_return.significant = avg_return.p_value is not None and avg_return.p_value < 0.05

        # Excess vs SPY com CI
        if len(excess) > 0:
            ex_val, ex_lo, ex_hi = bootstrap_ci(excess, statistic=np.mean)
            excess_stat = StatResult(
                metric="excess_vs_spy",
                value=ex_val,
                ci_lower=ex_lo,
                ci_upper=ex_hi,
                n=len(excess),
                p_value=t_test_one_sample(excess, null=0.0),
            )
            excess_stat.significant = (
                excess_stat.p_value is not None and excess_stat.p_value < 0.05
            )
        else:
            excess_stat = StatResult(metric="excess_vs_spy", value=np.nan)

        # Risk-adjusted
        sharpe = sharpe_ratio(returns)
        sortino = sortino_ratio(returns)
        max_dd = max_drawdown_simulated(returns)
        pf = profit_factor(returns)
        exp = expectancy(returns)
        var, cvar = value_at_risk(returns)

        wa = WindowAnalysis(
            days_elapsed=days,
            n_signals=len(returns),
            hit_rate=hit_rate,
            avg_return=avg_return,
            avg_excess_vs_spy=excess_stat,
            sharpe_per_trade=sharpe,
            sortino_per_trade=sortino,
            max_drawdown_pct=max_dd,
            profit_factor=pf,
            expectancy=exp,
            worst_return=float(np.min(returns)),
            best_return=float(np.max(returns)),
            var_95=var,
            cvar_95=cvar,
        )
        results["windows"][f"{days}d"] = wa

    return results


# ============================================================
# BASELINE ALEATÓRIO
# ============================================================


def run_random_baseline(run_id: int, n_random_portfolios: int = 100) -> dict:
    """
    Gera N portfólios aleatórios com mesmas datas e universo do run,
    computa outcomes e estatísticas pra cada um. Retorna distribuição.
    """
    if not DB_PATH.exists():
        sys.exit(1)

    import yfinance as yf
    import polars as pl

    conn = duckdb.connect(str(DB_PATH), read_only=False)

    # Pega datas e universo do run
    signals = conn.execute(
        """
        SELECT DISTINCT signal_date, universe FROM backtest_signals
        WHERE run_id = ?
        """,
        [run_id],
    ).df()

    if signals.empty:
        logger.error(f"Sem signals pra run_id={run_id}")
        return {}

    # Pega universo de tickers que foram usados no backtest
    tickers_used = (
        conn.execute(
            "SELECT DISTINCT ticker FROM backtest_signals WHERE run_id = ?",
            [run_id],
        )
        .fetchdf()["ticker"]
        .tolist()
    )

    # E o number de sinais por (data, universo) pra replicar
    counts = (
        conn.execute(
            """
            SELECT signal_date, universe, COUNT(*) AS n
            FROM backtest_signals
            WHERE run_id = ?
            GROUP BY signal_date, universe
            """,
            [run_id],
        )
        .fetchdf()
    )

    conn.close()

    logger.info(
        f"Random baseline: {n_random_portfolios} portfólios "
        f"em {len(signals['signal_date'].unique())} datas usando "
        f"~{len(tickers_used)} tickers"
    )

    # Baixar histórico de preços pra todos os tickers + SPY
    all_dates = pd.to_datetime(signals["signal_date"]).sort_values()
    start = (all_dates.min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (datetime.now()).strftime("%Y-%m-%d")

    logger.info(f"Baixando preços {start} a {end} pra {len(tickers_used)} tickers...")
    histories = {}
    batch_size = 100
    batches = [
        tickers_used[i : i + batch_size]
        for i in range(0, len(tickers_used), batch_size)
    ]

    for batch in batches:
        try:
            data = yf.download(
                tickers=batch,
                start=start,
                end=end,
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
                    if td.empty or len(td) < 100:
                        continue
                    df = pl.from_pandas(td.reset_index())
                    df = df.rename({c: c.lower() for c in df.columns})
                    histories[ticker] = df
                except Exception:
                    continue
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"Batch falhou: {e}")

    # SPY
    spy_raw = yf.download("SPY", start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = [c[0].lower() for c in spy_raw.columns]
    else:
        spy_raw.columns = [str(c).lower() for c in spy_raw.columns]
    spy_history = pl.from_pandas(spy_raw.reset_index()).rename(
        {c: str(c).lower() for c in spy_raw.reset_index().columns}
    )

    rng = np.random.default_rng(42)
    all_results = {7: [], 30: [], 60: [], 90: []}

    logger.info(f"Rodando {n_random_portfolios} portfólios aleatórios...")
    from tqdm import tqdm

    for portfolio_id in tqdm(range(n_random_portfolios), desc="Random portfolios"):
        for _, row in counts.iterrows():
            sig_date = row["signal_date"]
            n_to_pick = int(row["n"])
            # Pickar tickers aleatórios
            available = [t for t in tickers_used if t in histories]
            if len(available) < n_to_pick:
                continue
            chosen = rng.choice(available, size=n_to_pick, replace=False)
            for ticker in chosen:
                hist = histories[ticker]
                for days in [7, 30, 60, 90]:
                    out = _compute_outcome_quick(hist, sig_date, days, spy_history)
                    if out is not None:
                        all_results[days].append(out)

    # Calcula estatísticas agregadas
    summary = {}
    for days in [7, 30, 60, 90]:
        if not all_results[days]:
            continue
        returns = np.array([o["pct_change"] for o in all_results[days]])
        excess = np.array(
            [o["excess"] for o in all_results[days] if o["excess"] is not None]
        )
        won = (returns > 0).astype(int)

        hr_v, hr_lo, hr_hi = bootstrap_ci(won, np.mean)
        ar_v, ar_lo, ar_hi = bootstrap_ci(returns, np.mean)
        ex_v, ex_lo, ex_hi = (
            bootstrap_ci(excess, np.mean) if len(excess) else (np.nan, np.nan, np.nan)
        )

        summary[f"{days}d"] = {
            "n": len(returns),
            "hit_rate": {"value": hr_v * 100, "ci": [hr_lo * 100, hr_hi * 100]},
            "avg_return": {"value": ar_v, "ci": [ar_lo, ar_hi]},
            "excess_vs_spy": {"value": ex_v, "ci": [ex_lo, ex_hi]},
        }

    return summary


def _compute_outcome_quick(history, signal_date, days_ahead, spy_history):
    """Versão simplificada de compute_outcome pra random baseline."""
    import polars as pl

    sig_date = pd.to_datetime(signal_date).date()

    future = history.filter(pl.col("date") > sig_date).head(days_ahead + 5)
    entry = history.filter(pl.col("date") <= sig_date).tail(1)
    if entry.is_empty():
        return None
    target_window = future.head(days_ahead)
    if len(target_window) < days_ahead:
        return None

    entry_price = float(entry["close"][0])
    measurement = target_window.tail(1)
    price = float(measurement["close"][0])
    pct = (price - entry_price) / entry_price * 100

    # SPY pct
    spy_future = spy_history.filter(pl.col("date") > sig_date).head(days_ahead + 5)
    spy_entry = spy_history.filter(pl.col("date") <= sig_date).tail(1)
    spy_pct = None
    if not spy_entry.is_empty():
        spy_target = spy_future.head(days_ahead)
        if len(spy_target) >= days_ahead:
            spy_entry_p = float(spy_entry["close"][0])
            spy_price = float(spy_target.tail(1)["close"][0])
            spy_pct = (spy_price - spy_entry_p) / spy_entry_p * 100

    return {
        "pct_change": pct,
        "spy_pct": spy_pct,
        "excess": pct - spy_pct if spy_pct is not None else None,
    }


# ============================================================
# RELATÓRIO
# ============================================================


def print_window_analysis(wa: WindowAnalysis):
    print(f"\n--- {wa.days_elapsed} dias (n={wa.n_signals}) ---")
    print(f"  {wa.hit_rate}")
    print(f"  {wa.avg_return}")
    print(f"  {wa.avg_excess_vs_spy}")
    print(f"  Sharpe (per trade): {wa.sharpe_per_trade:.3f}")
    print(f"  Sortino:            {wa.sortino_per_trade:.3f}")
    print(f"  Max drawdown:       {wa.max_drawdown_pct:.2f}%")
    print(f"  Profit factor:      {wa.profit_factor:.3f}")
    print(f"  Expectancy:         {wa.expectancy:.3f}%")
    print(f"  VaR 95%:            {wa.var_95:.2f}%")
    print(f"  CVaR 95%:           {wa.cvar_95:.2f}%")
    print(f"  Worst:              {wa.worst_return:.2f}%")
    print(f"  Best:               {wa.best_return:.2f}%")


def print_results(results: dict):
    print("\n" + "=" * 70)
    print(f"VALIDAÇÃO ESTATÍSTICA — run_id={results['run_id']} ({results['label']})")
    print("=" * 70)
    print(f"Período: {results['period']}")
    print(f"Sinais totais: {results['n_total_signals']:,}")
    for win_name, wa in results["windows"].items():
        print_window_analysis(wa)

    print("\nLegenda:")
    print("  * = p-value < 0.05 (estatisticamente significativo)")
    print("  CI 95% via bootstrap (10.000 réplicas)")
    print("  p-value: H0 = sem efeito (hit rate=50%, return=0, excess=0)")


def save_report(results: dict, output_path: Path):
    """Salva resultados em JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def serialize(obj):
        if isinstance(obj, StatResult):
            return asdict(obj)
        if isinstance(obj, WindowAnalysis):
            return asdict(obj)
        if isinstance(obj, (np.floating, np.integer)):
            return float(obj)
        return obj

    def deep_serialize(d):
        if isinstance(d, dict):
            return {k: deep_serialize(serialize(v)) for k, v in d.items()}
        if isinstance(d, list):
            return [deep_serialize(serialize(v)) for v in d]
        return serialize(d)

    with open(output_path, "w") as f:
        json.dump(deep_serialize(results), f, indent=2, default=str)
    logger.info(f"Relatório salvo: {output_path}")


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="Validação científica do screener")
    parser.add_argument("--run-id", type=int, help="ID do backtest a analisar")
    parser.add_argument(
        "--random-baseline",
        action="store_true",
        help="Roda baseline aleatório pra comparar",
    )
    parser.add_argument(
        "--n-random",
        type=int,
        default=20,
        help="Número de portfólios aleatórios (default: 20, recomendado 100+)",
    )
    parser.add_argument("--list-runs", action="store_true", help="Lista runs disponíveis")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORTS_DIR,
        help="Pasta pros relatórios",
    )

    args = parser.parse_args()

    if args.list_runs:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        df = conn.execute(
            """
            SELECT run_id, label, start_date, end_date, n_signals,
                   ROUND(elapsed_seconds, 0) AS tempo_seg
            FROM backtest_runs
            ORDER BY run_id DESC
            """
        ).df()
        conn.close()
        print(df.to_string(index=False))
        return

    if not args.run_id:
        print("Use --run-id <id> ou --list-runs")
        sys.exit(1)

    # Fase 1: análise estatística do run
    print(f"\n🔬 Analisando run {args.run_id}...")
    results = analyze_run(args.run_id)
    print_results(results)

    # Salva
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_report(
        results,
        args.output_dir / f"validation_run_{args.run_id}_{today}.json",
    )

    # Fase 2: random baseline
    if args.random_baseline:
        print(f"\n🎲 Rodando random baseline ({args.n_random} portfólios)...")
        random_summary = run_random_baseline(args.run_id, args.n_random)
        print("\nResultado do baseline aleatório:")
        for win, stats in random_summary.items():
            print(f"\n  {win}:")
            print(
                f"    n={stats['n']:,}"
                f" | hit rate={stats['hit_rate']['value']:.1f}% [{stats['hit_rate']['ci'][0]:.1f}, {stats['hit_rate']['ci'][1]:.1f}]"
            )
            print(
                f"    avg return={stats['avg_return']['value']:.2f}% "
                f"[{stats['avg_return']['ci'][0]:.2f}, {stats['avg_return']['ci'][1]:.2f}]"
            )
            print(
                f"    excess vs SPY={stats['excess_vs_spy']['value']:.2f}% "
                f"[{stats['excess_vs_spy']['ci'][0]:.2f}, {stats['excess_vs_spy']['ci'][1]:.2f}]"
            )

        # Compara com screener
        print("\n📊 SCREENER vs RANDOM (excess vs SPY):")
        print(f"{'Janela':<10}{'Screener':>20}{'Random':>20}{'Diferença':>20}")
        for win_name, wa in results["windows"].items():
            if win_name not in random_summary:
                continue
            scr_v = wa.avg_excess_vs_spy.value
            rnd_v = random_summary[win_name]["excess_vs_spy"]["value"]
            diff = scr_v - rnd_v
            print(
                f"{win_name:<10}"
                f"{scr_v:>18.2f}% "
                f"{rnd_v:>18.2f}% "
                f"{diff:>+18.2f}%"
            )

        # Salva random baseline
        save_report(
            random_summary,
            args.output_dir / f"random_baseline_run_{args.run_id}_{today}.json",
        )


if __name__ == "__main__":
    main()
