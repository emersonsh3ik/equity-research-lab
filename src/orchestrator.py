"""
Equity Research Lab — Orchestrator
===================================

Master runner que executa as camadas em sequência com tratamento de erros,
logging consolidado e notificações de falha.

Roda diariamente via cron. Comandos:

    python orchestrator.py daily      # Pipeline completo do dia
    python orchestrator.py screener   # Só Layer 1+2
    python orchestrator.py outcomes   # Só Layer 4
    python orchestrator.py snapshot   # Só backup
    python orchestrator.py status     # Mostra estado atual

Princípios:
- Idempotente: pode rodar 2x no mesmo dia sem corromper dados
- Self-healing: retry em falhas transientes
- Self-monitoring: notifica em falhas críticas
- Logging estruturado: tudo vai pra logs/orchestrator_YYYYMMDD.log
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).parent.parent
LOG_DIR = PROJECT_DIR / "logs"
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR.mkdir(exist_ok=True)

# Logging consolidado
log_file = LOG_DIR / f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("orchestrator")


class Status(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    name: str
    status: Status
    elapsed_seconds: float
    message: str = ""
    error: str = ""


def notify_desktop(title: str, message: str, urgency: str = "normal"):
    """Notificação desktop nativa (macOS ou Linux). Não falha se indisponível."""
    try:
        if sys.platform == "darwin":
            # macOS via terminal-notifier ou osascript
            if subprocess.run(["which", "terminal-notifier"], capture_output=True).returncode == 0:
                subprocess.run(
                    ["terminal-notifier", "-title", title, "-message", message],
                    timeout=5,
                )
            else:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'display notification "{message}" with title "{title}"',
                    ],
                    timeout=5,
                )
        elif sys.platform.startswith("linux"):
            subprocess.run(
                ["notify-send", "--urgency", urgency, title, message],
                timeout=5,
            )
    except Exception as e:
        logger.warning(f"Falha em notify_desktop: {e}")


def run_step(name: str, func, *args, **kwargs) -> StepResult:
    """Executa um step com timing, logging e captura de exceções."""
    logger.info(f"━━━ {name} ━━━")
    t0 = time.time()
    try:
        result = func(*args, **kwargs)
        elapsed = time.time() - t0
        logger.info(f"✓ {name} OK em {elapsed:.1f}s")
        return StepResult(name=name, status=Status.SUCCESS, elapsed_seconds=elapsed, message=str(result)[:200])
    except Exception as e:
        elapsed = time.time() - t0
        tb = traceback.format_exc()
        logger.error(f"✗ {name} FALHOU em {elapsed:.1f}s: {e}\n{tb}")
        return StepResult(name=name, status=Status.FAILED, elapsed_seconds=elapsed, error=str(e))


# =====================================================================
# STEPS DA PIPELINE
# =====================================================================


def step_screener() -> dict:
    """Layer 1 + 2: data ingestion + screener + ranking."""
    from screener_v2 import run_screener, FilterConfig

    config = FilterConfig(top_n_per_universe=10)  # Top 20 total (10 por universo)
    results = run_screener(
        output_dir=DATA_DIR,
        config=config,
        refresh_universe=False,
        sample_size=None,
        universe_filter=None,
    )

    if not results:
        raise RuntimeError("Screener retornou vazio")

    return {
        "tickers_processed": sum(len(df) for df in results.values()),
        "universes": list(results.keys()),
    }


def step_insert_analyses() -> dict:
    """Layer 3.5: insere JSONs do Cowork no DuckDB."""
    from insert_analyses import insert_pending_analyses

    result = insert_pending_analyses()
    return result


def step_outcomes() -> dict:
    """Layer 4: tracking de outcomes 7d/30d/60d/90d."""
    from outcome_tracker import track_outcomes

    result = track_outcomes()
    return result


def step_health_check() -> dict:
    """Verifica saúde da pipeline."""
    from monitor import run_health_check

    return run_health_check()


def step_snapshot() -> dict:
    """Backup do DuckDB pro OneDrive."""
    from snapshot import create_snapshot

    return create_snapshot()


# =====================================================================
# COMANDOS
# =====================================================================


def cmd_daily():
    """Pipeline completo do dia."""
    logger.info("=" * 60)
    logger.info(f"DAILY PIPELINE — {datetime.now().isoformat()}")
    logger.info("=" * 60)

    results: list[StepResult] = []

    # Layer 1+2
    results.append(run_step("Layer 1+2 — Screener", step_screener))

    # Layer 3.5 (se houver análises pendentes do Cowork)
    results.append(run_step("Layer 3.5 — Insert analyses", step_insert_analyses))

    # Layer 4
    results.append(run_step("Layer 4 — Outcome tracking", step_outcomes))

    # Health check
    results.append(run_step("Health check", step_health_check))

    # Summary
    success_count = sum(1 for r in results if r.status == Status.SUCCESS)
    failed = [r for r in results if r.status == Status.FAILED]

    logger.info("=" * 60)
    logger.info("RESUMO")
    logger.info("=" * 60)
    for r in results:
        symbol = "✓" if r.status == Status.SUCCESS else "✗"
        logger.info(f"  {symbol} {r.name:35s} ({r.elapsed_seconds:6.1f}s)")

    if failed:
        msg = f"Pipeline com falhas: {[r.name for r in failed]}"
        logger.error(msg)
        notify_desktop("Research Lab — FALHA", msg, urgency="critical")
        return 1
    else:
        logger.info(f"✓ Pipeline completo: {success_count}/{len(results)} steps OK")
        notify_desktop("Research Lab", f"Pipeline OK ({success_count} steps)")
        return 0


def cmd_screener():
    """Roda apenas o screener."""
    result = run_step("Screener", step_screener)
    return 0 if result.status == Status.SUCCESS else 1


def cmd_outcomes():
    """Roda apenas outcome tracking."""
    result = run_step("Outcomes", step_outcomes)
    return 0 if result.status == Status.SUCCESS else 1


def cmd_snapshot():
    """Roda apenas backup."""
    result = run_step("Snapshot", step_snapshot)
    return 0 if result.status == Status.SUCCESS else 1


def cmd_status():
    """Mostra estado atual da pipeline."""
    import duckdb

    db_path = DATA_DIR / "research_lab.duckdb"
    if not db_path.exists():
        print("Database não inicializado. Rode: python src/database_init.py")
        return 1

    conn = duckdb.connect(str(db_path))
    try:
        print("\n" + "=" * 60)
        print("ESTADO DO RESEARCH LAB")
        print("=" * 60)

        # Sinais
        n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        last_signal_date = conn.execute(
            "SELECT MAX(signal_date) FROM signals"
        ).fetchone()[0]
        print(f"  Sinais totais:       {n_signals:>8,}")
        print(f"  Último sinal em:     {last_signal_date}")

        # Análises LLM
        n_analyses = conn.execute("SELECT COUNT(*) FROM analyses_llm").fetchone()[0]
        print(f"  Análises LLM:        {n_analyses:>8,}")

        # Outcomes
        n_outcomes = conn.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0]
        print(f"  Outcomes medidos:    {n_outcomes:>8,}")

        # Hit rate (se houver dados)
        try:
            hr = conn.execute(
                """
                SELECT
                  AVG(CASE WHEN hit_target_1 THEN 1.0 ELSE 0.0 END) * 100 as hr,
                  AVG(r_multiple) as r,
                  COUNT(*) as n
                FROM signal_outcomes
                WHERE days_elapsed = 30 AND NOT is_open
                """
            ).fetchone()
            if hr[2] and hr[2] > 0:
                print(f"\n  Hit rate 30d:        {hr[0]:.1f}%")
                print(f"  R-multiple médio:    {hr[1]:.2f}")
                print(f"  Amostra:             {hr[2]} sinais")
            else:
                print("\n  Hit rate 30d:        (dados insuficientes — precisa 30+ dias)")
        except Exception:
            pass

        # Última execução
        try:
            last_run = conn.execute(
                "SELECT run_date, status, elapsed_seconds FROM runs ORDER BY run_date DESC LIMIT 1"
            ).fetchone()
            if last_run:
                print(f"\n  Última execução:     {last_run[0]} ({last_run[1]}, {last_run[2]:.1f}s)")
        except Exception:
            pass

        print()

    finally:
        conn.close()

    return 0


# =====================================================================
# CLI
# =====================================================================


def main():
    parser = argparse.ArgumentParser(description="Equity Research Lab — Orchestrator")
    parser.add_argument(
        "command",
        choices=["daily", "screener", "outcomes", "snapshot", "status"],
        help="O que rodar",
    )
    args = parser.parse_args()

    # Adiciona src/ ao path
    sys.path.insert(0, str(Path(__file__).parent))

    if args.command == "daily":
        sys.exit(cmd_daily())
    elif args.command == "screener":
        sys.exit(cmd_screener())
    elif args.command == "outcomes":
        sys.exit(cmd_outcomes())
    elif args.command == "snapshot":
        sys.exit(cmd_snapshot())
    elif args.command == "status":
        sys.exit(cmd_status())


if __name__ == "__main__":
    main()
