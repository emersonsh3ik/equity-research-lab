#!/usr/bin/env bash
# =============================================================================
# Equity Research Lab — Pipeline Diário (cron)
# =============================================================================
# Roda o screener, publica os sinais, e dá push pro GitHub.
# Pra ser executado diariamente via cron em horário pre-market.
#
# USO MANUAL:
#   cd ~/Documents/equity-research-lab
#   ./daily_pipeline.sh
#
# CRON (toda segunda a sexta às 6h):
#   0 6 * * 1-5 cd ~/Documents/equity-research-lab && ./daily_pipeline.sh >> logs/daily_$(date +\%Y\%m\%d).log 2>&1
# =============================================================================

set -e

cd "$(dirname "$0")"

mkdir -p logs

echo "════════════════════════════════════════════════════════════════"
echo "Daily Pipeline — $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════════════"

# 1. Ativa virtualenv
source venv/bin/activate

# 2. Roda o screener (sem refresh-universe — usa cache do market caps)
echo ""
echo "[1/3] Executando screener..."
python 02_screener_v2.py --top-n-per-universe 10

# 3. Publica os sinais em JSON pra GitHub
echo ""
echo "[2/3] Publicando sinais pra published/..."
python src/publish_signals.py

# 4. Commit e push pro GitHub
echo ""
echo "[3/3] Commit + push pro GitHub..."
TODAY=$(date +%Y-%m-%d)
git add published/
if git diff --cached --quiet; then
    echo "Sem mudanças pra commitar (sinais idênticos a ontem?)"
else
    git commit -m "data: sinais $TODAY"
    git push
    echo "✓ Push concluído"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "Pipeline diário concluído"
echo "════════════════════════════════════════════════════════════════"
