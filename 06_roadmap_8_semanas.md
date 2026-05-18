# Roadmap 8 Semanas — Equity Research Lab

Cronograma realista para construir o sistema do zero até operação plena. Cada semana tem entregáveis verificáveis e um milestone de decisão.

**Hipótese de tempo:** 15h/semana (você confirmou). Total: ~120h ao longo de 8 semanas. Coerente com a estimativa da arquitetura.

---

## Semana 1 — Foundation (8-12 horas)

**Objetivo:** Layer 1 + Layer 2 rodando localmente com cobertura total NYSE+NASDAQ.

### Tarefas
- [ ] Configurar ambiente (Python 3.11, virtualenv, dependências) — 1h
- [ ] Rodar `database_init.py` e validar schema — 30min
- [ ] Rodar `screener.py --sample 100` em modo teste — 30min
- [ ] Rodar `screener.py --refresh-universe` cobertura total — 1h (com tempo de espera)
- [ ] Investigar output: o top 20 faz sentido? — 2h
- [ ] Ajustar filtros se necessário (`FilterConfig` no screener) — 2h
- [ ] Configurar cron para rodar todo dia útil às 6h — 30min
- [ ] Documentar primeiros achados (planilha: que setores aparecem? sanidade?) — 1h

### Milestone Semana 1
✅ **5 dias úteis consecutivos com screener rodando e top 20 salvo em DuckDB**

---

## Semana 2 — LLM Layer (12-15 horas)

**Objetivo:** Layer 3 funcionando. Top 10 analisados via Claude todo dia.

### Tarefas
- [ ] Criar conta Anthropic + configurar API key — 30min
- [ ] Adaptar `llm_analyzer.py` ao seu fluxo — 2h
- [ ] Habilitar web search no Claude (Tool Use) ou via WebFetch — 2h
- [ ] Testar com 1 ticker manualmente — 1h
- [ ] Validar que o output JSON é parseable — 1h
- [ ] Implementar `validate_analysis()` — 1h
- [ ] Rodar pipeline completo (screener + LLM) por 5 dias — 0h (cron)
- [ ] Revisar 50 análises manualmente: qualidade ok? — 5h
- [ ] Iterar no prompt v3 → v3.1 com base nos achados — 2h
- [ ] Commit do v3.1 com changelog — 30min

### Milestone Semana 2
✅ **Top 10 análises LLM/dia funcionando. Custo conhecido ($X/mês).**

---

## Semana 3 — Outcome Tracking (8-10 horas)

**Objetivo:** Layer 4 funcionando. Sinais sendo tracked com R-multiple medido.

### Tarefas
- [ ] Adaptar `outcome_tracker.py` — 2h
- [ ] Testar com sinais antigos manualmente (caso especial: até pode usar dados históricos retroativos pra alguns sinais ANTES de ter 7d completos) — 1h
- [ ] Configurar cron de outcome tracking (20h da noite) — 30min
- [ ] Verificar que após 7 dias, os primeiros sinais têm outcomes — 0h (esperar)
- [ ] Criar queries básicas no DuckDB para validar — 2h
- [ ] Documentar: setup do query playbook em `queries.sql` — 2h

### Milestone Semana 3
✅ **Primeiros 100 sinais com outcomes de 7 dias medidos.**

---

## Semana 4 — Fundamentals (12-15 horas)

**Objetivo:** Adicionar quality_score e value_score (atualmente skipped no MVP).

### Tarefas
- [ ] Adaptar screener para baixar fundamentals via yfinance (`Ticker.info`) — 4h
- [ ] Criar tabela `fundamentals_quarterly` e backfill (50% dos tickers) — 3h
- [ ] Calcular: gross_margin, op_margin, ROIC, debt_to_ebitda — 2h
- [ ] Adicionar quality_score (margens estáveis ou expandindo) — 2h
- [ ] Adicionar value_score (P/E forward < setor median) — 2h
- [ ] Re-ranquear: o top 20 muda significativamente? — 1h
- [ ] Documentar mudanças na arquitetura — 1h

### Milestone Semana 4
✅ **Ranking multi-fator (5 fatores) operacional. Comparar top 20 antigo vs novo.**

---

## Semana 5 — Insider Activity + Eventos (10-12 horas)

**Objetivo:** Adicionar dados que profissionais usam — insider transactions + earnings calendar.

### Tarefas
- [ ] Implementar scraping OpenInsider para Form 4 — 4h
- [ ] Popular `insider_transactions` table — 1h (script de batch)
- [ ] Adicionar `insider_score` ao composite — 1h
  - Net buying último 90d → score +5 a +10
  - Net selling pesado → score -5 a -10
- [ ] Adicionar earnings calendar (yfinance ou Finnhub free tier) — 3h
- [ ] Popular `events` table com earnings dos próximos 30 dias — 30min
- [ ] Adicionar `catalyst_score` (earnings nos próximos 14d com beat history) — 2h

### Milestone Semana 5
✅ **Composite score com 7 fatores (momentum, quality, value, setup, catalyst, insider).**

---

## Semana 6 — Dashboard + Análise (10-12 horas)

**Objetivo:** Visualizar o que o banco acumulou. Decidir se o sistema tem edge.

### Tarefas
- [ ] Instalar Streamlit (`pip install streamlit altair`) — 30min
- [ ] Criar dashboard com 5 abas:
  1. Top sinais de hoje (com link para análise LLM) — 2h
  2. Hit rate por janela (7d/30d/60d/90d) — 1h
  3. Performance por setor — 1h
  4. Distribuição de R-multiple — 1h
  5. Comparação vs SPY benchmark — 1h
- [ ] Rodar `streamlit run dashboard.py` localmente — 30min
- [ ] **Análise honesta dos primeiros 30-45 dias de dados:**
  - Hit rate target_1 30d: ___% (esperado: 30-50%)
  - R-multiple médio 30d: ___ (esperado: 1.0-2.0)
  - Excess return vs SPY 30d: ___ % (esperado: -5% a +5%)
  - Top setor: ___
  - Bottom setor: ___
  - Conclusão preliminar: o sistema tem alguma edge identificável?

### Milestone Semana 6 (CRÍTICO)
✅ **Primeira análise quantitativa do edge real do sistema.**

**Decisão:** continuar e aprimorar OU pivotar approach.

---

## Semana 7 — Backtest Framework (15-20 horas)

**Objetivo:** Poder testar mudanças sem esperar 30 dias ao vivo.

### Tarefas
- [ ] Criar `backtester.py`:
  - Input: data range (ex: 2025-01-01 a 2025-12-31)
  - Para cada dia útil, recriar o estado do banco usando apenas dados disponíveis ATÉ aquele dia
  - Rodar screener nesse estado
  - Comparar top 20 com performance realizada
- [ ] Setup: criar pasta `backtest/` com snapshots semanais do DuckDB — 2h
- [ ] Implementar walk-forward: train em jan-jun, validate em jul-dez — 4h
- [ ] Métricas: Sharpe ratio, max drawdown, profit factor — 3h
- [ ] Rodar primeiro backtest completo: o sistema teria funcionado? — 2h
- [ ] Identificar 3 mudanças no screener para testar via backtest — 2h
- [ ] Testar cada mudança vs baseline — 4h
- [ ] Promover ou descartar cada mudança baseado em significância — 2h

### Milestone Semana 7
✅ **Backtest framework funcional. Tem capacidade de iterar com rigor.**

---

## Semana 8 — Polimento + Telegram (8-10 horas)

**Objetivo:** Conveniência operacional + integração com vida diária.

### Tarefas
- [ ] Criar bot Telegram (`python-telegram-bot`) — 3h
- [ ] Comandos:
  - `/top5` — top 5 do dia com tese resumida
  - `/ticker SYMBOL` — análise on-demand
  - `/edge` — métricas atuais do sistema
  - `/changes` — sinais novos vs ontem
- [ ] Configurar webhook ou polling — 1h
- [ ] Mensagens diárias automáticas (cron + bot) — 2h
- [ ] Criar pasta `prompts/archive/` para versões antigas — 30min
- [ ] Documentar lessons learned das 8 semanas — 2h

### Milestone Semana 8 — FINAL
✅ **Sistema completo operando. Você sabe o edge do seu processo.**

---

## Após semana 8 — Iteração contínua

### Mês 3-6 (manutenção e melhoria)
- Revisão semanal das análises com verdict COMPRAR vs outcome real
- Iteração no prompt (v3.2, v3.3) com mudanças A/B testadas
- Adicionar fontes de dados conforme necessário (Polygon, NewsAPI)
- Refinar filtros do screener baseado em hit rate por setup

### Mês 6 — Decisão estratégica
Com 5.000-7.000 sinais acumulados + ~1.500 análises LLM:

**Cenário A — Sistema mostra edge real (hit rate > 50%, R-multiple > 1.5):**
- Considerar começar a operar com tamanho pequeno (1-2% do capital por trade)
- Manter o lab rodando em paralelo

**Cenário B — Sistema mostra resultado próximo do SPY (sem edge):**
- Sistema continua valioso como ferramenta de pesquisa
- NÃO arriscar dinheiro
- Considerar refinamentos mais agressivos OU aceitar como "sistema de descoberta"

**Cenário C — Sistema tem performance ruim consistente:**
- Algo está estruturalmente errado
- Backtest mostraria isto antes (semana 7) → não deveríamos chegar aqui
- Reavaliar arquitetura, possivelmente quebrar e refazer

---

## Riscos do projeto e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| yfinance rate-limit machuca produção | Alta | Mover para Polygon ($30/mês) na semana 4-5 se necessário |
| Custo LLM explode | Média | Limitar top 10 (não 50), usar Haiku para coleta, monitorar token spend semanalmente |
| Backtest mostra zero edge | Média | Plano B: usar lab como ferramenta de pesquisa, não trade |
| Sistema fica desatualizado | Alta | Cron + monitoring com alerta se screener não rodar 2 dias seguidos |
| Você perde tempo iterando ao invés de operar | Alta | Decisão crítica na semana 6 — disciplina pra ir/não ir |
| Hallucination causa decisão errada | Média | validate_analysis() + bear case obrigatório + verificação cruzada |

---

## Resumo: o que você terá em 8 semanas

- **~5.000 ações cobertas todos os dias úteis**
- **400-500 análises LLM aprofundadas** (top 10 × 40-50 dias)
- **DuckDB com ~3GB de dados estruturados**
- **Hit rate medido em pelo menos 1 janela (7d, talvez 30d)**
- **Backtest framework operacional**
- **Dashboard Streamlit local**
- **Bot Telegram com notificações**
- **Conhecimento real sobre o que funciona e o que não funciona no seu processo**

Tudo isso por:
- ~120 horas do seu tempo
- $20-200/mês operacional
- $0 risco financeiro (sem trade real)

---

## O que você precisa decidir AGORA (antes da semana 1)

1. **Opção A (100% local) ou Opção B (híbrido Cowork)?**  
   Recomendação: **A** se você tem ambiente Python configurado; **B** se quer evitar gerenciar chaves API agora

2. **Vai me pedir para configurar a scheduled task do Cowork pra rodar uma versão inicial enquanto monta o local?**  
   Se sim, posso fazer isso na próxima mensagem.

3. **Vai versionar o projeto em Git/GitHub?**  
   Forte recomendação: SIM. Repositório privado. Permite rollback, A/B, e histórico de iteração de prompts.

4. **Você tem laptop sempre ligado ou prefere VPS?**  
   Para semana 1-2 laptop está bom. Para mês 3+, VPS facilita.
