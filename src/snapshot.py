"""
Equity Research Lab — Snapshot semanal
========================================

Backup do DuckDB pro OneDrive. Roda toda segunda 22h via cron.

Mantém os últimos 12 snapshots (≈3 meses). Snapshots mais antigos
são deletados automaticamente.

ESTRUTURA:
    ~/OneDrive/research_lab/backups/
        research_lab_2026-05-17.duckdb.gz
        research_lab_2026-05-10.duckdb.gz
        ...
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
ONEDRIVE_PATH = Path(os.getenv("ONEDRIVE_PATH", str(Path.home() / "OneDrive" / "research_lab")))
BACKUP_DIR = ONEDRIVE_PATH / "backups"

MAX_SNAPSHOTS = 12


def create_snapshot() -> dict:
    """
    Cria snapshot do DuckDB comprimido em .gz.
    Limpa snapshots antigos (mantém últimos 12).
    Retorna dict com stats.
    """
    if not DB_PATH.exists():
        logger.warning(f"DB não existe ainda: {DB_PATH}")
        return {"created": False, "reason": "no_db"}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    snapshot_path = BACKUP_DIR / f"research_lab_{today_str}.duckdb.gz"

    if snapshot_path.exists():
        logger.info(f"Snapshot já existe para {today_str}: {snapshot_path}")
        return {"created": False, "reason": "already_exists", "path": str(snapshot_path)}

    # Comprime o DB
    logger.info(f"Criando snapshot {snapshot_path}...")
    original_size = DB_PATH.stat().st_size

    with open(DB_PATH, "rb") as f_in:
        with gzip.open(snapshot_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)

    compressed_size = snapshot_path.stat().st_size
    ratio = compressed_size / original_size * 100

    logger.info(
        f"Snapshot criado: {compressed_size / 1024 / 1024:.1f} MB "
        f"({ratio:.0f}% do original de {original_size / 1024 / 1024:.1f} MB)"
    )

    # Limpa snapshots antigos
    snapshots = sorted(
        BACKUP_DIR.glob("research_lab_*.duckdb.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    for old in snapshots[MAX_SNAPSHOTS:]:
        logger.info(f"Removendo snapshot antigo: {old.name}")
        old.unlink()
        removed += 1

    return {
        "created": True,
        "path": str(snapshot_path),
        "original_size_mb": original_size / 1024 / 1024,
        "compressed_size_mb": compressed_size / 1024 / 1024,
        "ratio_pct": ratio,
        "old_snapshots_removed": removed,
        "total_snapshots": len(snapshots) - removed,
    }


def restore_from_snapshot(snapshot_path: Path, target_path: Path | None = None):
    """
    Restaura um snapshot. Útil quando troca de máquina.

    USO MANUAL:
        python -c "from snapshot import restore_from_snapshot; from pathlib import Path; \\
                   restore_from_snapshot(Path('~/OneDrive/research_lab/backups/research_lab_2026-05-17.duckdb.gz'))"
    """
    if target_path is None:
        target_path = DB_PATH

    if target_path.exists():
        backup_existing = target_path.with_suffix(".duckdb.before_restore")
        logger.warning(f"DB existente movido para: {backup_existing}")
        shutil.move(str(target_path), str(backup_existing))

    logger.info(f"Restaurando de {snapshot_path}...")
    with gzip.open(snapshot_path, "rb") as f_in:
        with open(target_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    logger.info(f"Restauração completa: {target_path}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = create_snapshot()
    print(f"Resultado: {result}")
