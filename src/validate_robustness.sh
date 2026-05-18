#!/usr/bin/env bash
# =============================================================================
# Equity Research Lab — Validação de Robustez Paramétrica (FASE 3)
# =============================================================================
# Roda o mesmo backtest com variações nos parâmetros do screener pra ver se o
# edge é estável ou frágil (overfit).
#
# Testa:
#   - RSI ranges: (30,75), (35,70) [baseline], (40,65)
#   - Distance from 52w high: 15%, 25% [baseline], 35%
#   - Top N: 5, 10 [baseline], 20
#
# Se o edge é REAL, ele deve persistir em todas as variações.
# Se é OVERFIT, ele só funciona no setup específico do baseline.
#
# USO:
#   ./src/validate_robustness.sh
#
# ⏱ Demora ~2-4 horas (9 backtests). Recomendado rodar de noite.
# =============================================================================

set -e

cd "$(dirname "$0")/.."

mkdir -p logs

LOG="logs/robustness_$(date +%Y%m%d_%H%M%S).log"

# Período fixo pra todos os testes (6 meses recentes)
START="2025-08-01"
END="2026-02-28"

echo "════════════════════════════════════════════════════════════════"
echo "VALIDAÇÃO ROBUSTEZ PARAMÉTRICA — iniciado $(date)"
echo "Período: $START a $END"
echo "Log: $LOG"
echo "════════════════════════════════════════════════════════════════"

source venv/bin/activate

run_param() {
    local LABEL=$1
    shift
    echo ""
    echo "▶ $LABEL"
    echo "  Args: $@"
    python src/backtest.py \
        --start "$START" \
        --end "$END" \
        --label "$LABEL" \
        "$@" 2>&1 | tee -a "$LOG"
}

# ===== RSI ranges =====
run_param "rsi_30_75" --rsi-min 30 --rsi-max 75
run_param "rsi_35_70_baseline" --rsi-min 35 --rsi-max 70
run_param "rsi_40_65" --rsi-min 40 --rsi-max 65

# ===== Distance from 52w high =====
run_param "high_15" --max-from-high 15
run_param "high_25_baseline" --max-from-high 25
run_param "high_35" --max-from-high 35

# ===== Top N =====
run_param "topn_5" --top-n 5
run_param "topn_10_baseline" --top-n 10
run_param "topn_20" --top-n 20

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ ROBUSTEZ COMPLETO — terminado $(date)"
echo "Log completo: $LOG"
echo "════════════════════════════════════════════════════════════════"

# Resumo
echo ""
echo "🔬 Comparação dos 9 backtests:"
python src/q.py backtest-performance
