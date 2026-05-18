"""
Equity Research Lab — Layer 4: Outcome Tracker
================================================

Para cada sinal de 7d/30d/60d/90d atrás, mede o preço atual e calcula:
- R-multiple (retorno em unidades de risco)
- Hit rate dos targets
- Excess return vs SPY benchmark

Roda diariamente às 20h via cron. Idempotente — não duplica medições.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"

# Janelas de medição em dias
MEASUREMENT_WINDOWS = [7, 30, 60, 90]


def _get_price_data(ticker: str, start_date: str) -> dict | None:
    """Busca preços via yfinance. Retorna dict com current/high/low ou None."""
    try:
        hist = yf.Ticker(ticker).history(start=start_date)
        if hist.empty:
            return None
        return {
            "current": float(hist["Close"].iloc[-1]),
            "high_since": float(hist["High"].max()),
            "low_since": float(hist["Low"].min()),
        }
    except Exception as e:
        logger.warning(f"Falha ao buscar preços de {ticker}: {e}")
        return None


def _get_spy_change(start_date: str) -> float | None:
    """Calcula variação % do SPY desde start_date."""
    try:
        spy_hist = yf.Ticker("SPY").history(start=start_date)
        if spy_hist.empty or len(spy_hist) < 2:
            return None
        spy_then = float(spy_hist["Close"].iloc[0])
        spy_now = float(spy_hist["Close"].iloc[-1])
        return ((spy_now - spy_then) / spy_then) * 100
    except Exception:
        return None


def track_outcomes() -> dict:
    """Mede outcomes para todos os sinais nas janelas configuradas."""
    if not DB_PATH.exists():
        logger.warning("DB não existe")
        return {"measured": 0}

    conn = duckdb.connect(str(DB_PATH))
    stats = {"measured": 0, "skipped": 0, "errors": 0}

    try:
        for days in MEASUREMENT_WINDOWS:
            target_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            # Busca sinais ainda sem outcome nessa janela
            pending = conn.execute(
                """
                SELECT s.signal_id, s.ticker, s.close as entry_price, s.signal_date,
                       a.analysis_id, a.stop_loss, a.target_1, a.target_2
                FROM signals s
                LEFT JOIN analyses_llm a ON a.signal_id = s.signal_id
                WHERE s.signal_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM signal_outcomes o
                      WHERE o.signal_id = s.signal_id AND o.days_elapsed = ?
                  )
                """,
                [target_date, days],
            ).fetchall()

            if not pending:
                continue

            logger.info(f"Janela {days}d: {len(pending)} sinais pendentes")

            spy_change = _get_spy_change(target_date)

            for sig in pending:
                signal_id, ticker, entry_price, signal_date, analysis_id, stop, t1, t2 = sig

                price_data = _get_price_data(ticker, signal_date)
                if not price_data:
                    stats["errors"] += 1
                    continue

                current = price_data["current"]
                high_since = price_data["high_since"]
                low_since = price_data["low_since"]

                pct_change = ((current - entry_price) / entry_price) * 100
                excess = pct_change - spy_change if spy_change is not None else None

                # Hit checks (só se tivermos plano da análise LLM)
                hit_stop = bool(stop and low_since <= stop) if stop else False
                hit_t1 = bool(t1 and high_since >= t1) if t1 else False
                hit_t2 = bool(t2 and high_since >= t2) if t2 else False

                # R-multiple
                r_multiple = None
                if stop and entry_price and stop < entry_price:
                    risk = entry_price - stop
                    profit = current - entry_price
                    if risk > 0:
                        r_multiple = profit / risk

                is_open = days < 90
                is_closed_winner = (not is_open) and pct_change > 0
                is_closed_loser = (not is_open) and pct_change <= 0

                conn.execute(
                    """
                    INSERT INTO signal_outcomes (
                        outcome_id, signal_id, analysis_id, ticker, signal_date,
                        measurement_date, days_elapsed, price_at_measurement,
                        high_since_signal, low_since_signal, entry_price, pct_change,
                        hit_stop, hit_target_1, hit_target_2, r_multiple,
                        spy_pct_change, excess_return,
                        is_open, is_closed_winner, is_closed_loser
                    )
                    VALUES (
                        nextval('outcome_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        signal_id, analysis_id, ticker, signal_date,
                        datetime.now().date(), days, current, high_since, low_since,
                        entry_price, pct_change, hit_stop, hit_t1, hit_t2,
                        r_multiple, spy_change, excess,
                        is_open, is_closed_winner, is_closed_loser,
                    ],
                )
                stats["measured"] += 1

        conn.commit()

    finally:
        conn.close()

    logger.info(
        f"Outcomes processados: {stats['measured']} medidos, "
        f"{stats['errors']} erros"
    )
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    track_outcomes()
