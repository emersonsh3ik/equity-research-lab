"""
Equity Research Lab — Publish Signals
======================================

Lê os sinais mais recentes do DuckDB e exporta para JSON na pasta
`published/`. Essa pasta É VERSIONADA no git (não está no .gitignore),
permitindo que a scheduled task do Cowork leia os sinais de hoje via
GitHub raw URL.

USO:
    python src/publish_signals.py

OUTPUTS:
    published/latest.json           — Top 20 do dia atual (sobrescrito)
    published/YYYY-MM-DD.json       — Histórico permanente (não sobrescreve)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
PUBLISHED_DIR = PROJECT_DIR / "published"


def publish_today():
    """Lê sinais de hoje do DB e escreve JSONs em published/."""
    if not DB_PATH.exists():
        logger.error(f"DB não existe: {DB_PATH}")
        sys.exit(1)

    PUBLISHED_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        # Pega todas as colunas que existem na tabela (o enrich pode ter adicionado novas)
        cols_info = conn.execute("PRAGMA table_info(signals)").fetchall()
        existing_cols = [c[1] for c in cols_info]

        # Colunas obrigatórias (sempre devem existir)
        base_cols = [
            "signal_date", "ticker", "universe", "sector", "market_cap_usd",
            "close", "vol_avg_20", "rsi14", "mm50", "mm200",
            "high_52w", "low_52w", "pct_below_52w_high", "pct_above_52w_low",
            "momentum_score", "setup_score", "composite_score", "rank_position",
            "screener_version",
        ]

        # Colunas opcionais (do enrich_signals.py)
        optional_cols = [
            "pe_ttm", "pe_forward", "eps_ttm", "eps_forward",
            "dividend_yield", "profit_margin", "operating_margin", "gross_margin",
            "debt_to_equity", "return_on_equity",
            "revenue_growth_yoy", "earnings_growth_yoy",
            "next_earnings_date",
            "short_percent_of_float", "short_ratio",
            "insider_net_shares_6m", "insider_net_value_6m",
            "insider_n_buys_6m", "insider_n_sells_6m",
            "insider_top_sellers", "insider_last_transaction_date",
        ]

        cols = [c for c in base_cols if c in existing_cols] + [
            c for c in optional_cols if c in existing_cols
        ]

        rows = conn.execute(
            f"""
            SELECT {','.join(cols)}
            FROM signals
            WHERE signal_date = ?
            ORDER BY universe, rank_position
            """,
            [today],
        ).fetchall()

        if not rows:
            logger.warning(f"Nenhum sinal encontrado para {today}")
            sys.exit(1)

        signals = []
        for row in rows:
            d = dict(zip(cols, row))
            # Converte tipos para JSON-friendly
            d["signal_date"] = d["signal_date"].isoformat() if d["signal_date"] else None
            for k, v in d.items():
                if isinstance(v, float) and (v != v):  # NaN
                    d[k] = None
            signals.append(d)

        payload = {
            "generated_at": datetime.now().isoformat(),
            "signal_date": today,
            "screener_version": signals[0]["screener_version"] if signals else None,
            "n_signals": len(signals),
            "universes": {
                "A": [s for s in signals if s["universe"] == "A"],
                "B": [s for s in signals if s["universe"] == "B"],
            },
        }

    finally:
        conn.close()

    # Escreve latest.json (sempre sobrescreve)
    latest_path = PUBLISHED_DIR / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"Escreveu {latest_path}")

    # Escreve YYYY-MM-DD.json (histórico)
    history_path = PUBLISHED_DIR / f"{today}.json"
    with open(history_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"Escreveu {history_path}")

    print(f"\n✓ Publicados {len(signals)} sinais ({len(payload['universes']['A'])} A + {len(payload['universes']['B'])} B)")


if __name__ == "__main__":
    publish_today()
