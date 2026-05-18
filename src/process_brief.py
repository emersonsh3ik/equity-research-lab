"""
Equity Research Lab — Processa Daily Brief
============================================

Pega o markdown do relatório do Cowork (salvo manualmente ou via cron),
extrai o JSON estruturado embutido, e:
  1. Gera um HTML formatado (que pode ser impresso como PDF via browser)
  2. Insere as análises de tickers no DuckDB (tabela analyses_llm)
  3. Salva o markdown original no reports/ pra histórico

USO:
    # Modo automático: lê reports/inbox/*.md e processa
    python src/process_brief.py

    # Modo arquivo específico:
    python src/process_brief.py path/to/brief.md

WORKFLOW:
    1. De manhã, abre Cowork → scheduled task "daily-equity-research-lab"
    2. Copia o markdown gerado
    3. Cola num arquivo: ~/Documents/equity-research-lab/reports/inbox/YYYY-MM-DD.md
    4. Roda este script
    5. PDF aparece em reports/ — abre no browser e Cmd+P → Save as PDF
    6. Análises ficam no DuckDB pra queries futuras
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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
REPORTS_DIR = PROJECT_DIR / "reports"
INBOX_DIR = REPORTS_DIR / "inbox"


# =====================================================================
# MARKDOWN → HTML
# =====================================================================


def markdown_to_html(md: str, title: str = "Equity Research Lab") -> str:
    """
    Converte markdown para HTML usando uma implementação simples.
    Não precisa de biblioteca externa; cobre os elementos usados nos relatórios.
    """
    # Tenta usar markdown lib se disponível (melhor)
    try:
        import markdown as md_lib

        body = md_lib.markdown(
            md,
            extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        )
    except ImportError:
        # Fallback: conversão básica
        body = _simple_md_to_html(md)

    return _wrap_html(body, title)


def _simple_md_to_html(md: str) -> str:
    """Conversão simples sem dependência externa."""
    lines = md.split("\n")
    out = []
    in_code = False
    in_list = False

    for line in lines:
        # Code blocks
        if line.startswith("```"):
            if not in_code:
                out.append("<pre><code>")
                in_code = True
            else:
                out.append("</code></pre>")
                in_code = False
            continue
        if in_code:
            out.append(line)
            continue

        # Headers
        if line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
            continue
        if line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
            continue
        if line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
            continue

        # Bold
        line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
        # Italic
        line = re.sub(r"\*(.*?)\*", r"<em>\1</em>", line)
        # Links
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        # Inline code
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)

        # Lists
        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        elif in_list:
            out.append("</ul>")
            in_list = False

        # Paragraph
        if line.strip():
            out.append(f"<p>{line}</p>")
        else:
            out.append("")

    if in_list:
        out.append("</ul>")

    return "\n".join(out)


def _wrap_html(body: str, title: str) -> str:
    """Envolve o body HTML num documento completo com CSS."""
    css = """
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap');

    @page {
        size: A4;
        margin: 2cm 1.5cm;
    }

    body {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        line-height: 1.6;
        color: #1a1a1a;
        max-width: 900px;
        margin: 0 auto;
        padding: 2rem;
        background: #fff;
    }

    h1 {
        font-size: 2rem;
        border-bottom: 3px solid #1a1a1a;
        padding-bottom: 0.5rem;
        margin-top: 0;
        page-break-after: avoid;
    }

    h2 {
        font-size: 1.5rem;
        margin-top: 2rem;
        border-bottom: 1px solid #ccc;
        padding-bottom: 0.3rem;
        page-break-after: avoid;
    }

    h3 {
        font-size: 1.15rem;
        color: #333;
        margin-top: 1.5rem;
        page-break-after: avoid;
    }

    p {
        margin: 0.6rem 0;
    }

    a {
        color: #0066cc;
        text-decoration: none;
    }

    a:hover {
        text-decoration: underline;
    }

    strong {
        color: #000;
    }

    ul, ol {
        margin: 0.5rem 0;
        padding-left: 1.5rem;
    }

    li {
        margin: 0.2rem 0;
    }

    code {
        background: #f4f4f4;
        padding: 0.1rem 0.3rem;
        border-radius: 3px;
        font-family: 'SF Mono', Monaco, monospace;
        font-size: 0.9em;
    }

    pre {
        background: #f4f4f4;
        padding: 1rem;
        border-radius: 6px;
        overflow-x: auto;
        page-break-inside: avoid;
    }

    pre code {
        background: none;
        padding: 0;
    }

    table {
        border-collapse: collapse;
        width: 100%;
        margin: 1rem 0;
        font-size: 0.9em;
    }

    th, td {
        border: 1px solid #ddd;
        padding: 0.5rem;
        text-align: left;
    }

    th {
        background: #f0f0f0;
        font-weight: 700;
    }

    .header-meta {
        color: #666;
        font-size: 0.85rem;
        margin-bottom: 2rem;
    }

    .ticker-block {
        background: #fafafa;
        border-left: 3px solid #0066cc;
        padding: 1rem;
        margin: 1rem 0;
        page-break-inside: avoid;
    }

    @media print {
        body { padding: 0; }
        a { color: #000; }
    }
    """

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{css}</style>
</head>
<body>
<div class="header-meta">Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
{body}
</body>
</html>"""


# =====================================================================
# EXTRAIR JSON ESTRUTURADO DO MARKDOWN
# =====================================================================


def extract_structured_json(md: str) -> dict | None:
    """
    Procura no markdown por um bloco ```json ... ``` que contenha as
    análises estruturadas. Retorna o dict parseado ou None.
    """
    pattern = r"```json\s*\n(\{.*?\})\s*\n```"
    matches = re.findall(pattern, md, re.DOTALL)

    if not matches:
        logger.warning("Nenhum bloco JSON estruturado encontrado no markdown")
        return None

    # Pega o maior bloco (mais provável ser o relatório completo)
    largest = max(matches, key=len)
    try:
        return json.loads(largest)
    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido: {e}")
        return None


# =====================================================================
# INGESTÃO NO DUCKDB
# =====================================================================


def insert_analyses(structured: dict, source_file: str) -> int:
    """
    Insere as análises de cada ticker na tabela analyses_llm.
    Retorna número de análises inseridas.
    """
    if not DB_PATH.exists():
        logger.error(f"DB não existe: {DB_PATH}")
        return 0

    analysis_date = structured.get("analysis_date") or structured.get(
        "signal_date"
    ) or datetime.now().strftime("%Y-%m-%d")

    tickers = structured.get("tickers", [])
    if not tickers:
        # Tenta formato alternativo com universes A/B
        universes = structured.get("universes", {})
        tickers = universes.get("A", []) + universes.get("B", [])

    if not tickers:
        logger.warning("Nenhuma análise de ticker encontrada no JSON")
        return 0

    conn = duckdb.connect(str(DB_PATH))
    inserted = 0
    try:
        for t in tickers:
            ticker = t.get("ticker")
            if not ticker:
                continue

            # Verifica se já existe (idempotência)
            existing = conn.execute(
                "SELECT 1 FROM analyses_llm WHERE ticker = ? AND analysis_date = ? LIMIT 1",
                [ticker, analysis_date],
            ).fetchone()
            if existing:
                logger.info(f"  ⏭  Já existe: {ticker} {analysis_date}")
                continue

            op = t.get("operational_plan", {}) or t.get("plano_operacional", {}) or {}

            conn.execute(
                """
                INSERT INTO analyses_llm (
                    analysis_id, analysis_date, ticker, verdict, confidence_score,
                    thesis_summary, bull_arguments_json, bear_arguments_json,
                    catalysts_json, entry_zone_low, entry_zone_high, stop_loss,
                    target_1, target_2, rr_target_1, rr_target_2,
                    kill_switch_long, kill_switch_short,
                    raw_json, model_used, prompt_version
                )
                VALUES (
                    nextval('analysis_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    analysis_date,
                    ticker,
                    t.get("verdict"),
                    t.get("confidence_score"),
                    t.get("thesis_summary") or t.get("tese") or t.get("justificativa"),
                    json.dumps(t.get("bull_arguments_json") or t.get("bull_arguments", []), ensure_ascii=False),
                    json.dumps(t.get("bear_arguments_json") or t.get("bear_arguments", []), ensure_ascii=False),
                    json.dumps(t.get("catalysts_json") or t.get("catalysts", []), ensure_ascii=False),
                    op.get("entry_zone_low"),
                    op.get("entry_zone_high"),
                    op.get("stop_loss"),
                    op.get("target_1"),
                    op.get("target_2"),
                    op.get("rr_target_1"),
                    op.get("rr_target_2"),
                    t.get("kill_switch_long"),
                    t.get("kill_switch_short"),
                    json.dumps(t, ensure_ascii=False),
                    structured.get("metadata", {}).get("model_used", "cowork-claude"),
                    structured.get("metadata", {}).get("prompt_version", "v2"),
                ],
            )
            inserted += 1
            logger.info(f"  ✓ {ticker} inserido")

        conn.commit()
    finally:
        conn.close()

    return inserted


# =====================================================================
# WORKFLOW PRINCIPAL
# =====================================================================


def process_brief(md_path: Path):
    """Processa um arquivo de daily brief."""
    if not md_path.exists():
        logger.error(f"Arquivo não existe: {md_path}")
        return

    md = md_path.read_text(encoding="utf-8")
    logger.info(f"Processando {md_path.name} ({len(md)} chars)")

    # Extrai data do nome do arquivo ou do conteúdo
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
    if date_match:
        date_str = date_match.group(1)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Gera HTML
    REPORTS_DIR.mkdir(exist_ok=True)
    html_path = REPORTS_DIR / f"daily-brief-{date_str}.html"
    html = markdown_to_html(md, title=f"Daily Brief — {date_str}")
    html_path.write_text(html, encoding="utf-8")
    logger.info(f"  ✓ HTML salvo: {html_path}")

    # 2. Salva cópia do markdown
    md_archive_path = REPORTS_DIR / f"daily-brief-{date_str}.md"
    if not md_archive_path.exists() or md_archive_path != md_path:
        md_archive_path.write_text(md, encoding="utf-8")
        logger.info(f"  ✓ Markdown arquivado: {md_archive_path}")

    # 3. Extrai JSON estruturado e insere no DB
    structured = extract_structured_json(md)
    if structured:
        inserted = insert_analyses(structured, str(md_path))
        logger.info(f"  ✓ {inserted} análises inseridas no DB")
    else:
        logger.warning("  ⚠ Nenhum JSON estruturado encontrado — só HTML gerado, sem ingestão no DB")

    print(f"\n✓ Daily Brief processado.")
    print(f"  HTML: {html_path}")
    print(f"  Pra gerar PDF: abrir o HTML no browser e Cmd+P → 'Save as PDF'")


def process_inbox():
    """Processa todos os .md em reports/inbox/."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX_DIR.glob("*.md"))
    if not files:
        print(f"Nenhum arquivo em {INBOX_DIR}/")
        print(f"\nWorkflow:")
        print(f"  1. Abra o Cowork e copie o markdown do daily brief")
        print(f"  2. Cole em: {INBOX_DIR}/YYYY-MM-DD.md")
        print(f"  3. Rode novamente: python src/process_brief.py")
        sys.exit(0)

    for f in files:
        process_brief(f)
        # Move pro arquivo (já foi copiado pra reports/)
        processed_dir = INBOX_DIR / "processed"
        processed_dir.mkdir(exist_ok=True)
        f.rename(processed_dir / f.name)


def main():
    parser = argparse.ArgumentParser(description="Processa Daily Brief do Cowork")
    parser.add_argument("path", nargs="?", help="Arquivo .md (opcional)")
    args = parser.parse_args()

    if args.path:
        process_brief(Path(args.path))
    else:
        process_inbox()


if __name__ == "__main__":
    main()
