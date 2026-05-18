#!/usr/bin/env bash
# =============================================================================
# Equity Research Lab — Validação Multi-Período (FASE 2)
# =============================================================================
# Roda backtests em períodos com regimes diferentes pra testar se o edge
# do screener persiste em condições variadas:
#   - 2022: BEAR market (-19% SPY)
#   - 2023: RECOVERY (+24% SPY)
#   - 2024: MIXED
#   - 2025: PARTIAL (até nov pra evitar overlap com nosso backtest atual)
#
# USO:
#   ./src/validate_multiperiod.sh
#
# ⏱ Demora MUITO (1-3 horas). Recomendado rodar de noite.
# =============================================================================

set -e

cd "$(dirname "$0")/.."

mkdir -p logs

LOG="logs/multiperiod_$(date +%Y%m%d_%H%M%S).log"

echo "════════════════════════════════════════════════════════════════"
echo "VALIDAÇÃO MULTI-PERÍODO — iniciado $(date)"
echo "Log: $LOG"
echo "════════════════════════════════════════════════════════════════"

source venv/bin/activate

run_backtest_and_validate() {
    local START=$1
    local END=$2
    local LABEL=$3
    local REGIME=$4

    echo ""
    echo "▶ $LABEL ($REGIME) — $START a $END"
    echo "  Iniciando backtest..."

    python src/backtest.py \
        --start "$START" \
        --end "$END" \
        --label "$LABEL" 2>&1 | tee -a "$LOG"

    # Pega o run_id criado (último)
    RUN_ID=$(python -c "
import duckdb
conn = duckdb.connect('data/research_lab.duckdb', read_only=True)
row = conn.execute('SELECT MAX(run_id) FROM backtest_runs').fetchone()
print(row[0])
")

    echo "  Validando run $RUN_ID..."
    python src/validate.py --run-id "$RUN_ID" 2>&1 | tee -a "$LOG"
}

# Período 1: BEAR market 2022
run_backtest_and_validate "2022-01-01" "2022-12-31" "year_2022_bear" "BEAR (-19% SPY)"

# Período 2: RECOVERY 2023
run_backtest_and_validate "2023-01-01" "2023-12-31" "year_2023_recovery" "RECOVERY (+24% SPY)"

# Período 3: MIXED 2024
run_backtest_and_validate "2024-01-01" "2024-12-31" "year_2024_mixed" "MIXED (+23% SPY)"

# Período 4: ÚLTIMOS 6 MESES (out-of-sample do nosso backtest anterior)
run_backtest_and_validate "2025-06-01" "2025-11-17" "h2_2025_oos" "OUT-OF-SAMPLE"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ MULTI-PERÍODO COMPLETO — terminado $(date)"
echo "Log completo: $LOG"
echo "════════════════════════════════════════════════════════════════"

# Resumo comparativo
echo ""
echo "🔬 Resumo comparativo dos 4 períodos:"
python src/q.py backtest-performance
