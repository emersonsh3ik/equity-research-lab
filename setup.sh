#!/usr/bin/env bash
# =============================================================================
# Equity Research Lab — Bootstrap completo
# =============================================================================
# Roda UMA VEZ depois de clonar o repositório. Faz tudo possível
# automaticamente. Coisas que não puder, lista o que falta fazer.
#
# USO:
#   chmod +x setup.sh
#   ./setup.sh
#
# REQUISITOS PRÉ-EXISTENTES (você precisa ter antes):
#   - macOS ou Linux
#   - git instalado
#   - bash (não zsh-only)
#
# O QUE ESTE SCRIPT FAZ:
#   1. Verifica Python 3.11+ (instala se conseguir)
#   2. Cria virtualenv
#   3. Instala dependências (requirements.txt)
#   4. Cria estrutura de pastas
#   5. Configura .env a partir do template
#   6. Inicializa o DuckDB com schema
#   7. Configura cron jobs (com aprovação do usuário)
#   8. Testa que tudo funciona (modo --sample)
#
# =============================================================================

set -euo pipefail

# Cores pro terminal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_DIR="$(pwd)"
LOG_FILE="$PROJECT_DIR/setup.log"

log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

step() {
    log "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    log "${BLUE}▶ $1${NC}"
    log "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

ok() { log "${GREEN}✓ $1${NC}"; }
warn() { log "${YELLOW}⚠ $1${NC}"; }
err() { log "${RED}✗ $1${NC}"; }

# =============================================================================
# DETECTAR OS
# =============================================================================
OS=""
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux"* ]]; then
    OS="linux"
else
    err "Sistema operacional não suportado: $OSTYPE"
    exit 1
fi
ok "Sistema detectado: $OS"

# =============================================================================
# 1. PYTHON 3.11+
# =============================================================================
step "1/8 Verificando Python 3.11+"

PYTHON_BIN=""
for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        VERSION=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if [[ "$VERSION" == "3.11" || "$VERSION" == "3.12" || "$VERSION" == "3.13" ]]; then
            PYTHON_BIN="$py"
            ok "Encontrado: $py (versão $VERSION)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    warn "Python 3.11+ não encontrado. Tentando instalar..."
    if [[ "$OS" == "macos" ]]; then
        if ! command -v brew &>/dev/null; then
            err "Homebrew não está instalado. Instale primeiro: https://brew.sh/"
            err "Depois rode novamente: ./setup.sh"
            exit 1
        fi
        brew install python@3.12
        PYTHON_BIN="python3.12"
    else
        err "Instale Python 3.11+ manualmente:"
        err "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
        err "  Fedora:        sudo dnf install python3.12"
        exit 1
    fi
fi

# =============================================================================
# 2. VIRTUALENV
# =============================================================================
step "2/8 Criando virtualenv"

if [[ ! -d "venv" ]]; then
    "$PYTHON_BIN" -m venv venv
    ok "Virtualenv criado em ./venv/"
else
    ok "Virtualenv já existe"
fi

# shellcheck disable=SC1091
source venv/bin/activate

# =============================================================================
# 3. DEPENDÊNCIAS
# =============================================================================
step "3/8 Instalando dependências"

pip install --upgrade pip --quiet

if [[ -f "requirements.txt" ]]; then
    pip install -r requirements.txt --quiet
    ok "Dependências instaladas"
else
    err "requirements.txt não encontrado"
    exit 1
fi

# =============================================================================
# 4. ESTRUTURA DE PASTAS
# =============================================================================
step "4/8 Criando estrutura de pastas"

mkdir -p data/{prices,signals,analyses,backups}
mkdir -p logs
mkdir -p reports
mkdir -p prompts/archive
mkdir -p tasks
ok "Pastas criadas"

# =============================================================================
# 5. CONFIGURAR .env
# =============================================================================
step "5/8 Configurando .env"

if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        warn "Criado .env a partir do template"
        warn "EDITE o arquivo .env e adicione sua ANTHROPIC_API_KEY (opcional se usar só Cowork)"
    else
        cat > .env << EOF
# Equity Research Lab — Environment Variables
ANTHROPIC_API_KEY=
DATA_DIR=./data
LOG_DIR=./logs
ONEDRIVE_PATH=$HOME/OneDrive/research_lab
LOG_LEVEL=INFO
EOF
        warn "Criado .env padrão"
    fi
else
    ok ".env já existe"
fi

# =============================================================================
# 6. INICIALIZAR DUCKDB
# =============================================================================
step "6/8 Inicializando DuckDB"

if [[ ! -f "data/research_lab.duckdb" ]]; then
    python src/database_init.py --db-path ./data/research_lab.duckdb
    ok "Database inicializado"
else
    ok "Database já existe"
    python src/database_init.py --db-path ./data/research_lab.duckdb --stats
fi

# =============================================================================
# 7. CRON JOBS
# =============================================================================
step "7/8 Configurando cron jobs"

CRONTAB_NEW=$(mktemp)
CURRENT_CRON=$(crontab -l 2>/dev/null || echo "")

# Remove entradas antigas do lab (idempotência)
echo "$CURRENT_CRON" | grep -v "equity-research-lab" > "$CRONTAB_NEW" || true

# Adiciona as entradas novas
PYTHON_PATH="$PROJECT_DIR/venv/bin/python"
ORCHESTRATOR="$PROJECT_DIR/src/orchestrator.py"
SNAPSHOT="$PROJECT_DIR/src/snapshot.py"
MONITOR="$PROJECT_DIR/src/monitor.py"

cat >> "$CRONTAB_NEW" << EOF

# === equity-research-lab cron jobs ===
# Pipeline diário: screener + outcomes (Layer 1, 2, 4)
0 6 * * 1-5 cd $PROJECT_DIR && $PYTHON_PATH $ORCHESTRATOR daily >> $PROJECT_DIR/logs/orchestrator_\$(date +\%Y\%m\%d).log 2>&1

# Insere análises do Cowork no DB (Layer 3.5)
0 8 * * 1-5 cd $PROJECT_DIR && $PYTHON_PATH $PROJECT_DIR/src/insert_analyses.py >> $PROJECT_DIR/logs/insert_\$(date +\%Y\%m\%d).log 2>&1

# Outcome tracking (Layer 4 dedicado)
0 20 * * 1-5 cd $PROJECT_DIR && $PYTHON_PATH $PROJECT_DIR/src/outcome_tracker.py >> $PROJECT_DIR/logs/outcomes_\$(date +\%Y\%m\%d).log 2>&1

# Monitor de saúde da pipeline (a cada 4h em dias úteis)
0 10,14,18 * * 1-5 cd $PROJECT_DIR && $PYTHON_PATH $MONITOR >> $PROJECT_DIR/logs/monitor.log 2>&1

# Snapshot semanal pro OneDrive (domingo 22h)
0 22 * * 0 cd $PROJECT_DIR && $PYTHON_PATH $SNAPSHOT >> $PROJECT_DIR/logs/snapshot.log 2>&1
EOF

echo ""
log "Cron jobs que serão instalados:"
log ""
grep -A1 "equity-research-lab" "$CRONTAB_NEW" || true
log ""

read -p "Confirmar instalação dos cron jobs? (y/N): " confirm
if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
    crontab "$CRONTAB_NEW"
    ok "Cron jobs instalados"
else
    warn "Pulando instalação de cron. Você pode rodar manualmente."
fi

rm "$CRONTAB_NEW"

# =============================================================================
# 8. TESTE DE FUMAÇA
# =============================================================================
step "8/8 Teste rápido (sample=50 tickers)"

if python src/screener_v2.py --sample 50 --top-n-per-universe 2; then
    ok "Teste passou! Pipeline está funcional."
else
    err "Teste falhou. Veja logs."
    exit 1
fi

# =============================================================================
# RESUMO FINAL
# =============================================================================
step "Setup completo!"

cat << EOF

${GREEN}═══════════════════════════════════════════════════════════════════${NC}
${GREEN}✓ Equity Research Lab está pronto pra rodar${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

PRÓXIMOS PASSOS MANUAIS (que não dá pra automatizar):

1. ${YELLOW}Sincronizar com OneDrive${NC}
   Configure o OneDrive Client pra sincronizar:
     ${PROJECT_DIR}/data/  ←→  ~/OneDrive/research_lab/

2. ${YELLOW}Ativar Cowork scheduled task${NC}
   (Quando chegar na Semana 2 do roadmap)
   Use o arquivo tasks/daily_llm_analysis.md como referência.

3. ${YELLOW}Push pro GitHub${NC}
   git remote add origin git@github.com:SEU-USUARIO/equity-research-lab.git
   git push -u origin main

4. ${YELLOW}Rodar primeiro screener completo${NC}
   make refresh-universe    # Demora ~1h (busca market caps de 6.000 ações)
   # OU
   python src/screener_v2.py --refresh-universe

COMANDOS ÚTEIS:

  python src/orchestrator.py daily       # Roda pipeline manualmente
  python src/monitor.py                  # Check de saúde
  python src/database_init.py --stats    # Stats do DB
  duckdb data/research_lab.duckdb        # Abrir DB pra queries SQL

LOGS em:
  $PROJECT_DIR/logs/

EOF
