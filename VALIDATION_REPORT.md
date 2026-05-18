# Validation Report — Equity Research Lab

Documento vivo das descobertas científicas sobre o sistema. Atualizado conforme novos testes.

**Última atualização:** 2026-05-18

---

## TL;DR

✅ **O screener técnico tem edge mensurável, especialmente em horizontes longos (60-90 dias).**

- O ranking por `composite_score` adiciona valor consistente sobre random selection no mesmo universo de qualifiers (+0,62 a +14,12 pp dependendo do horizonte).
- Os filtros adicionam valor em horizontes longos (≥60 dias) mas não fazem diferença significativa em curto prazo.
- Edge cresce com o horizonte temporal (momentum técnico se desenvolve em 60-90 dias).

⏳ **Validação pendente:** persistência em múltiplos regimes de mercado (bear, recovery, mixed). Em andamento via `validate_multiperiod.sh`.

---

## Metodologia

### Universo
- ~3.700 tickers NYSE+NASDAQ
- Dois universos paralelos:
  - **A:** Mid/Large caps ($2B - $500B market cap), ~1.500 tickers
  - **B:** Small caps ($300M - $2B), ~2.500 tickers

### Filtros do screener (técnicos)
- Preço entre $5 e $10.000
- Volume médio 20d ≥ 200.000 ações
- RSI(14) entre 35 e 70
- Preço acima da MM50
- Distância máxima da máxima de 52 semanas: 25%
- Distância mínima da mínima de 52 semanas: 20%

### Ranking (composite_score, v1 MVP)
- Momentum score: 60% (distância da MM50, RSI, força relativa)
- Setup score: 40% (volume confirmando, proximidade da máxima de 52w)

### Outcomes medidos
- Janelas: 7, 30, 60 e 90 dias úteis após o sinal
- Métricas: retorno absoluto, hit rate (won = retorno > 0), excess vs SPY benchmark

### Rigor estatístico aplicado
- Bootstrap CI 95% com 10.000 réplicas
- T-test de duas caudas para hipótese nula (return=0, hit_rate=50%, excess=0)
- Sharpe e Sortino ratios per-trade
- Max drawdown via equity curve simulada
- VaR 95% e CVaR 95%

---

## Resultados — Backtest 6m_full_v2_qualifiers (run_id=7)

**Período:** 2025-11-18 a 2026-02-28 (74 dias úteis, regime majoritariamente bullish)
**Sinais:** 1.480 (20 por dia útil)

### Performance absoluta do screener

| Janela | n | Hit rate (CI 95%) | Avg return (CI 95%) | Excess vs SPY (CI 95%) | Sharpe | Profit factor |
|---|---|---|---|---|---|---|
| 7d | 1480 | 51,9% [49,3; 54,5] | +1,44% [+0,76; +2,13]* | +1,13% [+0,46; +1,81]* | 0,107 | 1,372 |
| 30d | 1480 | 49,1% [46,6; 51,7] | +2,49% [+1,13; +3,98]* | +3,24% [+1,88; +4,71]* | 0,089 | 1,327 |
| 60d | 1360 | 51,1% [48,5; 53,8] | +8,71% [+6,42; +11,06]* | +7,81% [+5,55; +10,15]* | 0,197 | 1,900 |
| 90d | 720 | 57,4% [53,8; 61,0]* | +22,66% [+17,07; +28,72]* | +18,37% [+12,80; +24,41]* | 0,281 | 3,334 |

\* = estatisticamente significativo (p < 0.05)

### Risco

| Janela | VaR 95% | CVaR 95% | Worst | Best |
|---|---|---|---|---|
| 7d | -17,5% | -25,0% | -64,8% | +99,2% |
| 30d | -31,3% | -40,5% | -58,2% | +343,8% |
| 60d | -40,9% | -49,6% | -69,2% | +362,6% |
| 90d | -46,0% | -54,4% | -71,7% | +696,3% |

**Observação:** distribuição é altamente assimétrica para o lado positivo (best »|worst|). Estratégia tipo momentum/right-tail.

### Comparação com baselines aleatórios

#### Random Qualifiers (mais rigoroso)
Random pick entre tickers que passaram nos MESMOS filtros do MESMO dia.

| Janela | Screener excess | Random qualifiers excess | **Edge do ranking** |
|---|---|---|---|
| 7d | +1,13% | +0,50% | **+0,62 pp** |
| 30d | +3,24% | +1,88% | **+1,36 pp** |
| 60d | +7,81% | +2,39% | **+5,42 pp** |
| 90d | +18,37% | +4,25% | **+14,12 pp** |

**Conclusão:** o `composite_score` adiciona alpha consistente sobre random no mesmo universo. Edge cresce com o horizonte.

#### Random Full Universe
Random pick entre todos os ~3.700 tickers do universe (com survivorship bias).

| Janela | Screener excess | Random full excess | Diff vs full |
|---|---|---|---|
| 7d | +1,13% | +0,89% | +0,23 pp |
| 30d | +3,24% | +3,09% | +0,15 pp |
| 60d | +7,81% | +4,65% | **+3,16 pp** |
| 90d | +18,37% | +8,42% | **+9,94 pp** |

**Caveat:** universe.parquet tem apenas tickers que existem hoje (survivorship bias). Random full provavelmente está super-estimado em 2-4 pp. Comparação aplica-se com cautela.

---

## Como interpretar os achados

### O que está FUNCIONANDO ✅

1. **Filtros do screener identificam um universo de qualidade.** Mesmo random selection desse universo (vs SPY) gera alpha positivo, especialmente em 60d/90d.

2. **Composite_score adiciona valor sobre random qualifiers.** Não é só o universo — o ranking dentro do universo também tem informação.

3. **Edge cresce com horizonte temporal.** Faz sentido tecnicamente — momentum técnico se manifesta em períodos de 30-90 dias, não em uma semana.

4. **Estatisticamente significativo.** Todos os excess vs SPY têm p < 0.001 em 30d, 60d e 90d. Não é sorte.

### O que NÃO está claro ainda ⏳

1. **Persistência multi-regime.** Backtest atual é em período bullish (Nov 2025 - Fev 2026). Não sabemos se edge funciona em bear/recovery. Testando agora.

2. **Robustez paramétrica.** Mudar RSI thresholds, distance from high, etc. quebra o edge?

3. **Valor dos campos novos (enrichment).** Fundamentals + insider activity (Tier 1+2 do enrich) podem melhorar o composite_score?

### O que JÁ SABEMOS que precisa melhorar

1. **Hit rate em 30d é 49,1%** — abaixo do random de 50%. Estratégia é tipo "lose often, win big". Aceitável em momentum, mas problemático psicologicamente se for operar.

2. **Tail risk pesado.** -71% no pior caso em 90d. Stop loss e position sizing rigoroso seriam essenciais se virasse trade real.

3. **Sharpe baixo per-trade** (0,1 a 0,28). Edge existe mas é volátil. Annualization seria útil quando tivermos dados suficientes.

---

## Limitações honestas

1. **Sem fundamentals point-in-time.** yfinance retorna P/E e margens ATUAIS, não os de 6 meses atrás. Backtest histórico é puramente técnico — não testa o valor do enrichment.

2. **Universo é snapshot atual.** Tickers delistados durante o período não entram. Survivorship bias presente.

3. **Sem custos de transação.** Backtest assume preço de fechamento. Spread + slippage + impostos reduziriam edge na vida real.

4. **Período curto.** 74 dias úteis (~3,5 meses). Para um sistema momentum, isso é o mínimo aceitável. Multi-período em andamento.

5. **Random "full" tem survivorship bias.** Não é baseline neutro perfeito. Random "qualifiers" é a comparação mais limpa.

---

## Próximos passos (Roadmap de validação)

- [x] **Fase 1:** Estatística robusta (CI bootstrap, p-values, Sharpe, drawdown) — concluído
- [x] **Fase 1.5:** Random baseline em 3 modos (qualifiers, subset, full) — concluído
- [ ] **Fase 2:** Multi-período (2022 bear, 2023 recovery, 2024 mixed, 2025 partial) — em andamento via `validate_multiperiod.sh`
- [ ] **Fase 3:** Robustez paramétrica (variar RSI, distance, top-N) — `validate_robustness.sh`
- [ ] **Fase 4:** Composite score v2 com fundamentals + insider — depois das fases 2 e 3
- [ ] **Fase 5:** Comparação v1 vs v2 nos mesmos dados — depois da Fase 4

---

## Configurações testadas

| Run ID | Label | Período | Sinais | Resultado-chave |
|---|---|---|---|---|
| 3 | quick_test | 2026-01-01 a 2026-02-01 (sample 200) | 440 | Primeira execução bem-sucedida |
| 6 | 6m_full | 2025-11-18 a 2026-02-28 | 1480 | +3,27% excess 30d, +18,37% 90d |
| 7 | 6m_full_v2_qualifiers | 2025-11-18 a 2026-02-28 | 1480 | Igual ao 6 + qualifiers salvos |

---

## Notas técnicas

- DB: DuckDB em `data/research_lab.duckdb`
- Tabelas relevantes: `backtest_runs`, `backtest_signals`, `backtest_outcomes`, `backtest_qualifiers`
- Reports JSON: `reports/validation/`
- Scripts: `src/backtest.py`, `src/validate.py`, `src/validate_multiperiod.sh`, `src/validate_robustness.sh`
