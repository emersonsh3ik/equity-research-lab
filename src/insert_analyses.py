"""
Equity Research Lab — Layer 3.5: Insert Analyses
=================================================

Lê os JSONs gerados pela scheduled task do Cowork (Layer 3) na pasta
do OneDrive e os insere no DuckDB. Move arquivos processados pra
subpasta `processed/` pra evitar reprocessamento.

Roda diariamente via cron às 8h (depois do Cowork ter terminado às 7h).

ESTRUTURA ESPERADA NA PASTA:
    ~/OneDrive/research_lab/analyses/YYYY-MM-DD/
        TICKER1_universe_A.json
        TICKER1_universe_A.md
        TICKER2_universe_B.json
        TICKER2_universe_B.md
        ...

APÓS PROCESSAR:
    ~/OneDrive/research_lab/analyses/YYYY-MM-DD/processed/
        (arquivos movidos pra cá)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"
ONEDRIVE_PATH = Path(os.getenv("ONEDRIVE_PATH", str(Path.home() / "OneDrive" / "research_lab")))
ANALYSES_DIR = ONEDRIVE_PATH / "analyses"


def validate_analysis(data: dict) -> tuple[bool, list[str]]:
    """Valida que o JSON tem os campos obrigatórios e está consistente."""
    errors = []

    required = [
        "ticker",
        "analysis_date",
        "verdict",
        "confidence_score",
        "thesis_summary",
        "bull_arguments_json",
        "bear_arguments_json",
        "operational_plan",
    ]
    for field in required:
        if field not in data:
            errors.append(f"Campo obrigatório ausente: {field}")

    if errors:
        return False, errors

    # Verdict válido
    if data["verdict"] not in ["COMPRAR", "OBSERVAR", "EVITAR", "SHORTAR"]:
        errors.append(f"Verdict inválido: {data['verdict']}")

    # Confidence em range
    if not (1 <= data["confidence_score"] <= 10):
        errors.append(f"Confidence fora do range: {data['confidence_score']}")

    # Bear case obrigatório
    if not data["bear_arguments_json"]:
        errors.append("Bear case vazio — proibido pela política do lab")

    # Plano operacional consistente
    op = data.get("operational_plan", {})
    if op:
        stop = op.get("stop_loss")
        entry_low = op.get("entry_zone_low")
        target_1 = op.get("target_1")
        target_2 = op.get("target_2")

        if stop and entry_low and stop >= entry_low:
            errors.append("Stop loss acima da entrada")
        if entry_low and target_1 and target_1 <= entry_low:
            errors.append("Target 1 abaixo da entrada")
        if target_1 and target_2 and target_2 < target_1:
            errors.append("Target 2 abaixo do target 1")
        if op.get("rr_target_1") and op["rr_target_1"] < 1.5:
            errors.append(f"R:R target 1 muito baixo: {op['rr_target_1']}")

    return len(errors) == 0, errors


def insert_analysis(conn, data: dict) -> int:
    """Insere uma análise no DuckDB. Retorna analysis_id."""
    op = data.get("operational_plan", {})

    # Tenta achar signal_id correspondente
    signal_id = None
    if data.get("signal_id"):
        signal_id = data["signal_id"]
    else:
        # Busca por ticker + data
        row = conn.execute(
            """
            SELECT signal_id FROM signals
            WHERE ticker = ? AND signal_date = ?
            LIMIT 1
            """,
            [data["ticker"], data["analysis_date"]],
        ).fetchone()
        if row:
            signal_id = row[0]

    conn.execute(
        """
        INSERT INTO analyses_llm (
            analysis_id, signal_id, analysis_date, ticker, verdict, confidence_score,
            thesis_summary, bull_arguments_json, bear_arguments_json,
            catalysts_json, entry_zone_low, entry_zone_high, stop_loss,
            target_1, target_2, rr_target_1, rr_target_2,
            suggested_position_size_pct, kill_switch_long, kill_switch_short,
            raw_json, model_used, prompt_version
        )
        VALUES (
            nextval('analysis_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            signal_id,
            data["analysis_date"],
            data["ticker"],
            data["verdict"],
            data["confidence_score"],
            data["thesis_summary"],
            json.dumps(data["bull_arguments_json"]),
            json.dumps(data["bear_arguments_json"]),
            json.dumps(data.get("catalysts_json", [])),
            op.get("entry_zone_low"),
            op.get("entry_zone_high"),
            op.get("stop_loss"),
            op.get("target_1"),
            op.get("target_2"),
            op.get("rr_target_1"),
            op.get("rr_target_2"),
            op.get("suggested_position_size_pct"),
            data.get("kill_switch_long"),
            data.get("kill_switch_short"),
            json.dumps(data),
            data.get("metadata", {}).get("model_used", "unknown"),
            data.get("metadata", {}).get("prompt_version", "unknown"),
        ],
    )

    return conn.execute(
        "SELECT analysis_id FROM analyses_llm WHERE ticker = ? AND analysis_date = ?",
        [data["ticker"], data["analysis_date"]],
    ).fetchone()[0]


def insert_pending_analyses() -> dict:
    """
    Processa todos os JSONs ainda não processados em ~/OneDrive/.../analyses/.
    Retorna dict com estatísticas.
    """
    if not ANALYSES_DIR.exists():
        logger.warning(f"Pasta de análises não existe: {ANALYSES_DIR}")
        return {"inserted": 0, "skipped": 0, "errors": 0, "no_directory": True}

    stats = {"inserted": 0, "skipped": 0, "errors": 0, "validation_failed": 0}

    conn = duckdb.connect(str(DB_PATH))

    try:
        # Itera por todas as subpastas de data (YYYY-MM-DD)
        for date_dir in sorted(ANALYSES_DIR.iterdir()):
            if not date_dir.is_dir() or date_dir.name == "processed":
                continue

            processed_dir = date_dir / "processed"
            processed_dir.mkdir(exist_ok=True)

            # JSONs pendentes nessa data
            jsons = list(date_dir.glob("*.json"))
            if not jsons:
                continue

            logger.info(f"Processando {len(jsons)} JSONs em {date_dir.name}")

            for json_file in jsons:
                try:
                    with open(json_file) as f:
                        data = json.load(f)

                    # Verifica se já existe no DB
                    exists = conn.execute(
                        """
                        SELECT 1 FROM analyses_llm
                        WHERE ticker = ? AND analysis_date = ?
                        LIMIT 1
                        """,
                        [data["ticker"], data["analysis_date"]],
                    ).fetchone()

                    if exists:
                        logger.info(f"  ⏭  Já existe: {json_file.name}")
                        stats["skipped"] += 1
                        # Move pra processed mesmo assim
                        shutil.move(str(json_file), str(processed_dir / json_file.name))
                        # Move o .md junto
                        md_file = json_file.with_suffix(".md")
                        if md_file.exists():
                            shutil.move(str(md_file), str(processed_dir / md_file.name))
                        continue

                    # Valida
                    valid, errors = validate_analysis(data)
                    if not valid:
                        logger.warning(f"  ✗ Validação falhou {json_file.name}: {errors}")
                        stats["validation_failed"] += 1
                        # Move pra subpasta de erro
                        error_dir = date_dir / "validation_errors"
                        error_dir.mkdir(exist_ok=True)
                        shutil.move(str(json_file), str(error_dir / json_file.name))
                        continue

                    # Insere
                    analysis_id = insert_analysis(conn, data)
                    logger.info(f"  ✓ Inserido {data['ticker']} (id={analysis_id})")
                    stats["inserted"] += 1

                    # Move arquivos processados
                    shutil.move(str(json_file), str(processed_dir / json_file.name))
                    md_file = json_file.with_suffix(".md")
                    if md_file.exists():
                        shutil.move(str(md_file), str(processed_dir / md_file.name))

                except json.JSONDecodeError as e:
                    logger.error(f"  ✗ JSON inválido em {json_file.name}: {e}")
                    stats["errors"] += 1
                except Exception as e:
                    logger.error(f"  ✗ Erro em {json_file.name}: {e}")
                    stats["errors"] += 1

        conn.commit()

    finally:
        conn.close()

    logger.info(
        f"Resumo: {stats['inserted']} inseridos, "
        f"{stats['skipped']} skipped (já existiam), "
        f"{stats['validation_failed']} falharam validação, "
        f"{stats['errors']} erros"
    )

    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    insert_pending_analyses()
