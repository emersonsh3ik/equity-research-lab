# Published Signals

Esta pasta é versionada no Git. Contém os sinais diários do screener em formato JSON, servindo como fonte de dados para:

1. A scheduled task do Cowork (que lê `latest.json` via GitHub raw URL)
2. Histórico permanente dos top 20 candidatos de cada dia (arquivos `YYYY-MM-DD.json`)

## Arquivos

- **`latest.json`** — Sempre os top 20 do último dia processado. Sobrescrito a cada execução.
- **`YYYY-MM-DD.json`** — Histórico imutável de cada dia.

## Como é gerado

Automaticamente via `src/publish_signals.py`, que é chamado pelo `daily_pipeline.sh` após o screener rodar.

## Esquema do JSON

```json
{
  "generated_at": "2026-05-18T07:00:00",
  "signal_date": "2026-05-18",
  "screener_version": "v2.0_dual_universe",
  "n_signals": 20,
  "universes": {
    "A": [/* 10 signals, mid/large caps */],
    "B": [/* 10 signals, small caps */]
  }
}
```
