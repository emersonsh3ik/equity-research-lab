"""
Equity Research Lab — Health Monitor
=====================================

Verifica saúde da pipeline e alerta em caso de problemas.

Roda a cada 4h em dias úteis via cron.

CHECKS:
  1. Screener rodou hoje? (signals do dia presentes)
  2. Análises LLM presentes (top 20 esperados em dias úteis)?
  3. Outcomes sendo medidos?
  4. DuckDB acessível?
  5. OneDrive sincronizando?
  6. Espaço em disco suficiente?

ALERTAS:
  - Desktop notification em macOS/Linux
  - Log file dedicado
  - (Opcional) Cowork scheduled task pode ler esse log e alertar via outra forma
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
SIGNALS_DIR = PROJECT_DIR / "data" / "signals"
ONEDRIVE_PATH = Path(os.getenv("ONEDRIVE_PATH", str(Path.home() / "OneDrive" / "research_lab")))


class Severity(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Check:
    name: str
    severity: Severity
    message: str
    details: dict | None = None


def notify(title: str, message: str, urgency: str = "normal"):
    """Notificação desktop. Silencia em ambientes headless."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                timeout=5,
            )
        elif sys.platform.startswith("linux"):
            urgency_map = {"normal": "normal", "warning": "normal", "critical": "critical"}
            subprocess.run(
                [
                    "notify-send",
                    "--urgency",
                    urgency_map.get(urgency, "normal"),
                    title,
                    message,
                ],
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"notify falhou (provavelmente headless): {e}")


# =====================================================================
# CHECKS INDIVIDUAIS
# =====================================================================


def check_database_accessible() -> Check:
    """O DuckDB está acessível?"""
    try:
        if not DB_PATH.exists():
            return Check(
                name="database_accessible",
                severity=Severity.CRITICAL,
                message=f"DuckDB não existe: {DB_PATH}",
            )

        conn = duckdb.connect(str(DB_PATH), read_only=True)
        n_tables = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main'"
        ).fetchone()[0]
        conn.close()

        if n_tables < 5:
            return Check(
                name="database_accessible",
                severity=Severity.WARNING,
                message=f"DuckDB acessível mas só {n_tables} tabelas (esperado >=10)",
            )

        return Check(
            name="database_accessible",
            severity=Severity.OK,
            message=f"DuckDB OK ({n_tables} tabelas)",
        )
    except Exception as e:
        return Check(
            name="database_accessible",
            severity=Severity.CRITICAL,
            message=f"Erro ao acessar DB: {e}",
        )


def check_screener_ran_today() -> Check:
    """O screener gerou sinais hoje (em dia útil)?"""
    today = datetime.now()

    # Skip weekends
    if today.weekday() >= 5:
        return Check(
            name="screener_ran_today",
            severity=Severity.OK,
            message="Final de semana — skip",
        )

    # Verifica se já passou de 7h (screener roda às 6h)
    if today.hour < 7:
        return Check(
            name="screener_ran_today",
            severity=Severity.OK,
            message="Ainda cedo (screener roda às 6h)",
        )

    today_str = today.strftime("%Y-%m-%d")

    # Check via parquet files
    expected_a = SIGNALS_DIR / f"{today_str}_universe_A.parquet"
    expected_b = SIGNALS_DIR / f"{today_str}_universe_B.parquet"

    missing = []
    if not expected_a.exists():
        missing.append("Universe A")
    if not expected_b.exists():
        missing.append("Universe B")

    if missing:
        return Check(
            name="screener_ran_today",
            severity=Severity.CRITICAL,
            message=f"Screener NÃO rodou hoje. Missing: {', '.join(missing)}",
        )

    # Check via DB
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        n_today = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_date = ?", [today_str]
        ).fetchone()[0]
        conn.close()

        if n_today < 15:  # Esperado: ~20 (10 por universo)
            return Check(
                name="screener_ran_today",
                severity=Severity.WARNING,
                message=f"Só {n_today} sinais hoje (esperado >=15)",
            )

        return Check(
            name="screener_ran_today",
            severity=Severity.OK,
            message=f"Screener OK ({n_today} sinais hoje)",
        )
    except Exception as e:
        return Check(
            name="screener_ran_today",
            severity=Severity.WARNING,
            message=f"DB check falhou: {e}",
        )


def check_llm_analyses_today() -> Check:
    """Análises LLM presentes hoje (após 8h)?"""
    today = datetime.now()

    if today.weekday() >= 5 or today.hour < 9:
        return Check(
            name="llm_analyses_today",
            severity=Severity.OK,
            message="Skip (final de semana ou cedo demais)",
        )

    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        n_today = conn.execute(
            "SELECT COUNT(*) FROM analyses_llm WHERE analysis_date = ?",
            [today.strftime("%Y-%m-%d")],
        ).fetchone()[0]
        conn.close()

        if n_today == 0:
            return Check(
                name="llm_analyses_today",
                severity=Severity.WARNING,
                message="Nenhuma análise LLM hoje. Cowork scheduled task rodando?",
            )

        if n_today < 15:
            return Check(
                name="llm_analyses_today",
                severity=Severity.WARNING,
                message=f"Só {n_today} análises hoje (esperado ~20)",
            )

        return Check(
            name="llm_analyses_today",
            severity=Severity.OK,
            message=f"Análises LLM OK ({n_today} hoje)",
        )
    except Exception as e:
        return Check(
            name="llm_analyses_today",
            severity=Severity.WARNING,
            message=f"DB check falhou: {e}",
        )


def check_outcomes_tracked() -> Check:
    """Outcomes sendo medidos?"""
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)

        # Quantos outcomes nas últimas 48h
        recent = conn.execute(
            """
            SELECT COUNT(*) FROM signal_outcomes
            WHERE created_at >= CURRENT_DATE - INTERVAL '2 days'
            """
        ).fetchone()[0]

        # Quantos sinais antigos sem outcome 7d
        unmeasured = conn.execute(
            """
            SELECT COUNT(*) FROM signals s
            WHERE signal_date <= CURRENT_DATE - INTERVAL '8 days'
              AND NOT EXISTS (
                  SELECT 1 FROM signal_outcomes o
                  WHERE o.signal_id = s.signal_id AND o.days_elapsed = 7
              )
            """
        ).fetchone()[0]

        conn.close()

        if unmeasured > 50:
            return Check(
                name="outcomes_tracked",
                severity=Severity.WARNING,
                message=f"{unmeasured} sinais sem outcome 7d. Outcome tracker rodando?",
            )

        return Check(
            name="outcomes_tracked",
            severity=Severity.OK,
            message=f"Outcomes OK ({recent} medidos últimas 48h, {unmeasured} pendentes)",
        )
    except Exception as e:
        return Check(
            name="outcomes_tracked",
            severity=Severity.WARNING,
            message=f"DB check falhou: {e}",
        )


def check_disk_space() -> Check:
    """Espaço em disco suficiente?"""
    try:
        total, used, free = shutil.disk_usage(PROJECT_DIR)
        free_gb = free / 1024 / 1024 / 1024

        if free_gb < 1:
            return Check(
                name="disk_space",
                severity=Severity.CRITICAL,
                message=f"Disco quase cheio! {free_gb:.1f} GB livres",
            )
        elif free_gb < 5:
            return Check(
                name="disk_space",
                severity=Severity.WARNING,
                message=f"Disco com {free_gb:.1f} GB livres",
            )

        return Check(
            name="disk_space",
            severity=Severity.OK,
            message=f"Disco OK ({free_gb:.1f} GB livres)",
        )
    except Exception as e:
        return Check(
            name="disk_space",
            severity=Severity.WARNING,
            message=f"Check falhou: {e}",
        )


def check_onedrive_sync() -> Check:
    """OneDrive está sincronizando?"""
    if not ONEDRIVE_PATH.exists():
        return Check(
            name="onedrive_sync",
            severity=Severity.WARNING,
            message=f"OneDrive path não existe: {ONEDRIVE_PATH}",
        )

    # Verifica timestamp do arquivo mais recente
    try:
        latest = max(ONEDRIVE_PATH.rglob("*"), key=lambda p: p.stat().st_mtime, default=None)
        if latest is None:
            return Check(
                name="onedrive_sync",
                severity=Severity.WARNING,
                message="OneDrive pasta vazia",
            )

        age_hours = (datetime.now().timestamp() - latest.stat().st_mtime) / 3600

        if age_hours > 48:
            return Check(
                name="onedrive_sync",
                severity=Severity.WARNING,
                message=f"OneDrive não atualiza há {age_hours:.0f}h",
            )

        return Check(
            name="onedrive_sync",
            severity=Severity.OK,
            message=f"OneDrive OK (último arquivo {age_hours:.1f}h atrás)",
        )
    except Exception as e:
        return Check(
            name="onedrive_sync",
            severity=Severity.WARNING,
            message=f"Check falhou: {e}",
        )


# =====================================================================
# ORQUESTRAÇÃO DOS CHECKS
# =====================================================================


def run_health_check() -> dict:
    """Roda todos os checks. Retorna dict resumido."""
    checks = [
        check_database_accessible(),
        check_screener_ran_today(),
        check_llm_analyses_today(),
        check_outcomes_tracked(),
        check_disk_space(),
        check_onedrive_sync(),
    ]

    n_ok = sum(1 for c in checks if c.severity == Severity.OK)
    n_warning = sum(1 for c in checks if c.severity == Severity.WARNING)
    n_critical = sum(1 for c in checks if c.severity == Severity.CRITICAL)

    overall = "OK" if n_critical == 0 and n_warning == 0 else "WARNING" if n_critical == 0 else "CRITICAL"

    print(f"\n{'=' * 60}")
    print(f"HEALTH CHECK — {datetime.now().isoformat()}")
    print(f"{'=' * 60}")
    for c in checks:
        symbol = {Severity.OK: "✓", Severity.WARNING: "⚠", Severity.CRITICAL: "✗"}[c.severity]
        print(f"  {symbol} {c.name:30s} {c.message}")
    print(f"\n  Geral: {overall} ({n_ok} OK, {n_warning} warnings, {n_critical} críticos)")

    # Notifica em desktop se houver problemas
    if n_critical > 0:
        critical_msgs = [c.message for c in checks if c.severity == Severity.CRITICAL]
        notify("Research Lab — CRÍTICO", "; ".join(critical_msgs), "critical")
    elif n_warning > 0:
        warning_msgs = [c.message for c in checks if c.severity == Severity.WARNING][:2]
        notify("Research Lab — Atenção", "; ".join(warning_msgs), "warning")

    return {
        "overall": overall,
        "n_ok": n_ok,
        "n_warning": n_warning,
        "n_critical": n_critical,
        "checks": [
            {"name": c.name, "severity": c.severity.value, "message": c.message}
            for c in checks
        ],
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = run_health_check()
    # Exit code reflete severidade (útil pra cron)
    sys.exit(0 if result["n_critical"] == 0 else 1)
