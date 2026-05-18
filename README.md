# Equity Research Lab

Sistema de descoberta sistemática de oportunidades em ações americanas (NYSE + NASDAQ, ~6.000 ativos), com aprendizado composto ao longo do tempo via banco de dados próprio.

**Objetivo:** identificar empresas com alta probabilidade de valorização, registrar a tese e o setup, acompanhar o que aconteceu, e iterar sobre o processo medindo edge real.

**Não é** um bot de trading. Sem dinheiro real envolvido — pelo menos até o sistema provar edge consistente.

## Arquitetura em 5 camadas

```
Layer 1 — Data ingestion       → yfinance baixa OHLCV de NYSE+NASDAQ diariamente
Layer 2 — Quant screener       → filtros + ranking multifatorial; reduz 6.000 → 20
Layer 3 — LLM deep analysis    → Claude analisa os top 20 (Cowork scheduled task)
Layer 4 — Outcome tracking     → mede R-multiple 7d/30d/60d/90d após cada sinal
Layer 5 — Delivery             → relatórios markdown, dashboard, eventual bot Telegram
```

## Dois universos paralelos

- **Universo A** — Mid/Large caps (US$2B - US$500B, ~1.500 nomes)
- **Universo B** — Small caps (US$300M - US$2B, ~2.500 nomes)

Top 10 de cada universo = top 20 análises LLM por dia.

## Stack

- Python 3.11+, Polars, DuckDB
- yfinance (dados grátis)
- Claude via Cowork (Layer 3)
- cron local pra automação

## Estrutura do projeto

```
.
├── 00_decisoes_finais.md      ← Source of truth das decisões
├── 01_arquitetura.md           ← Arquitetura detalhada
├── 02_screener_v2.py           ← Screener com dual universe
├── 03_database_init.py         ← Schema DuckDB
├── 04_prompt_v3_daily.md       ← Prompt LLM com JSON estruturado
├── 05_README_setup.md          ← Instruções de instalação
├── 06_roadmap_8_semanas.md     ← Cronograma
├── 07_cowork_scheduled_task.md ← Configuração da scheduled task
├── setup.sh                    ← Bootstrap automatizado
├── requirements.txt            ← Dependências Python
├── .env.example                ← Template de variáveis
└── src/
    ├── orchestrator.py         ← Master runner
    ├── insert_analyses.py      ← Layer 3.5
    ├── outcome_tracker.py      ← Layer 4
    ├── monitor.py              ← Health checks
    └── snapshot.py             ← Backup automático
```

## Setup

Veja [`05_README_setup.md`](./05_README_setup.md) para instruções completas, ou simplesmente rode:

```bash
chmod +x setup.sh
./setup.sh
```

## Roadmap

Plano de 8 semanas em [`06_roadmap_8_semanas.md`](./06_roadmap_8_semanas.md).

## Status

🚧 Em desenvolvimento — Semana 0 (setup inicial).

## Histórico

- 2026-05-17 — Setup inicial: repositório criado, arquitetura definida, scripts base versionados.
