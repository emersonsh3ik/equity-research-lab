# Decisões Finais de Arquitetura — Equity Research Lab

Documento canônico das decisões fechadas após debate. Source of truth do projeto. Qualquer dúvida posterior consulta aqui primeiro.

**Data das decisões:** 17 de maio de 2026
**Status:** APROVADO PRA IMPLEMENTAÇÃO

---

## Deployment: Híbrido (Python local + Cowork scheduled tasks)

| Layer | Onde roda | Por quê |
|---|---|---|
| 1 — Data ingestion | Python local (laptop) | Precisa yfinance/Polars/Parquet, não tem como rodar fora |
| 2 — Quant screener + ranking | Python local (laptop) | Mesma razão |
| 3 — LLM deep analysis | **Cowork scheduled task** | Captura o benefício do Claude Enterprise (sem custo marginal) |
| 4 — Outcome tracking | Python local (laptop) | yfinance + DuckDB |
| 5 — Delivery (relatórios/snapshots) | Python local + OneDrive sync | Storage e portabilidade |

**Portabilidade garantida via:**
- Repositório Git privado no GitHub (código + prompts + configs + schema)
- Snapshot semanal do DuckDB pro OneDrive 1TB (domingos via cron)
- `.env.example` versionado, `.env` real fora do Git
- Definição da scheduled task do Cowork em arquivo Markdown versionado em `tasks/`

**Se laptop morrer:** clone repo → instala Python → restaura DB do OneDrive → reconfigura scheduled task no Cowork (~30 minutos)

---

## Universo: Dual desde o dia 1

Dois pipelines paralelos, mesmo screener, dados separados:

**Universo A (estável):**
- Mid + large caps
- Market cap entre US$2B e US$500B
- ~1.500 nomes
- Validação de que o sistema funciona

**Universo B (alpha hunting):**
- Small caps
- Market cap entre US$300M e US$2B
- ~2.500 nomes
- Hipótese: mais ineficiência precificada, mais espaço pra descoberta

**Total: ~4.000 ativos cobertos diariamente.**

Sinais e análises armazenados com flag `universe` no DuckDB para comparação direta de hit rate entre os dois universos.

---

## Top N para análise LLM: 20 ações diárias

10 do Universo A + 10 do Universo B (top ranqueado em cada).

Custo via Cowork Enterprise: $0 marginal.
Backup (se um dia migrar pra API): ~$50-60/mês com Sonnet.

**Por que 20 em vez de 10:** acumula 2x mais learning ao longo do tempo
(mais sinais com outcomes), o que acelera a iteração do composite score
e das versões de prompt. Custo marginal zero via Cowork compensa folgado.

---

## Composite Score: Iterativo (cresce de 2 para 6 fatores)

| Fase | Fatores ativos | Pesos | Quando |
|---|---|---|---|
| MVP (v1) | Momentum + Setup | 60 / 40 | Semanas 1-3 |
| v2 | + Quality + Value | 40 / 25 / 20 / 15 | Semana 4 |
| v3 | + Catalyst + Insider | 30 / 25 / 20 / 15 / 10 (Insider absorvido em Catalyst) | Semana 5+ |
| v4 | Pesos refinados via correlação real com outcomes | TBD | Semana 8+ |

Cada versão é A/B testada contra a anterior antes de promover. DuckDB grava `screener_version` em cada sinal.

---

## Stack Tecnológico

- **Python 3.11+**
- **Polars** (DataFrames) + **DuckDB** (analytics SQL)
- **yfinance** (preços e fundamentos básicos — free)
- **Anthropic Claude** via Cowork Enterprise (Layer 3)
- **OneDrive** (1TB) para snapshots e portabilidade
- **GitHub privado** para versionamento
- **cron** local no Mac/Linux para automação
- **Streamlit** (futuro, semana 6) para dashboard

---

## Storage

- DuckDB local em `~/equity-research-lab/data/research_lab.duckdb`
- Parquet diário em `~/equity-research-lab/data/{prices,signals,analyses}/`
- Sync para `~/OneDrive/research_lab/` continuamente (auto via OneDrive client)
- Snapshot manual do DuckDB todo domingo às 22h (cron + cp + timestamp)

Tamanho estimado:
- Mês 1: ~500MB
- Mês 6: ~3GB
- Mês 12: ~7-10GB
- OneDrive 1TB cobre folgado por 5+ anos

---

## Watchlist Manual

Tabela `watchlist_manual` no DuckDB. Tickers nessa tabela são SEMPRE analisados pelo LLM no Layer 3, mesmo se não passarem no screener. Permite combinar disciplina sistemática com convicções pontuais (ex: VST, NEE, CCJ continuam em radar mesmo se filtrarem fora).

Schema simples:
```sql
CREATE TABLE watchlist_manual (
    ticker VARCHAR PRIMARY KEY,
    added_date DATE,
    reason TEXT,
    active BOOLEAN DEFAULT TRUE,
    added_by VARCHAR
);
```

Limite sugerido: até 20 tickers ativos. Acima disso vira ruído.

---

## Bear Case: Obrigatório

Toda análise LLM precisa ter `bear_arguments_json` não-vazio. Validador rejeita análise sem bear case e força re-run.

Exceção rara: se bear case for explicitamente justificado como vazio ("ativo passou em todos os filtros sem red flag identificado"), aceita com `warning` no metadata.

---

## Edge Metrics (3 categorias)

Sistema não opera dinheiro, então "edge" é medido por:

**1. Discovery Edge**
- % dos top 5 do dia que subiram após 7d / 30d / 60d / 90d
- Meta: > 50% após 30 dias

**2. Process Edge**
- R-multiple médio se tivessem operado os signals com verdict COMPRAR
- Meta: > 1.5 após 60-90 dias

**3. Filter Edge**
- Comparar performance dos signals filtrados vs random selection do mesmo universo
- Meta: signals > random em hit rate e R-multiple

Query views já criadas no schema do DuckDB cobrem essas métricas.

---

## Roadmap 8 Semanas (refinado)

| Semana | Foco | Entregável | Milestone de decisão |
|---|---|---|---|
| 1 | Layer 1+2 funcionando | Screener dual universe rodando local todo dia | 5 dias consecutivos com top 10 no DB |
| 2 | Layer 3 via Cowork | Scheduled task analisando top 10 diários | Análises LLM batem expectativa de qualidade |
| 3 | Layer 4 (tracking) | Outcomes de 7d sendo medidos | Primeiros R-multiples calculados |
| 4 | Quality + Value | Composite v2 com 4 fatores | Comparar v2 vs v1 |
| 5 | Catalyst + Insider | Composite v3 com 6 fatores | Comparar v3 vs v2 |
| 6 | Dashboard + análise honesta | Streamlit local, primeira leitura de edge | **DECISÃO:** continuar ou pivotar |
| 7 | Backtest framework | Replay histórico 12m | Validar mudanças propostas |
| 8 | Telegram + polimento | Bot com /top10, /edge, /ticker | Sistema completo operacional |

---

## Compromissos do usuário

- [ ] Dedicar 15h/semana pelas próximas 8 semanas
- [ ] Manter cron rodando (laptop ligado pela manhã)
- [ ] Revisar diariamente o relatório do screener (~10 min)
- [ ] Revisar SEMANALMENTE as análises LLM (1-2h aos sábados)
- [ ] Versionar tudo no GitHub
- [ ] Fazer snapshot semanal do DuckDB pro OneDrive
- [ ] NÃO operar dinheiro real até semana 8 (mínimo)

---

## O que NÃO está no escopo nesta fase

- Trading real (zero dinheiro envolvido)
- Day trading / scalping
- Opções e derivativos
- Mercado brasileiro (B3 entra no mês 6+)
- Newsletter / produto comercial
- Real-time data intraday

---

## Próximos passos concretos

### HOJE (após esta mensagem)
1. Criar repositório no GitHub: `equity-research-lab` (privado)
2. Clonar localmente: `~/equity-research-lab/`
3. Copiar os 7 arquivos do projeto (00 a 06) pra dentro do repo
4. Commit inicial: "feat: initial project structure and architecture"

### AMANHÃ
5. Instalar Python 3.11+ se ainda não tem
6. Criar virtualenv e instalar dependências (instruções no `05_README_setup.md`)
7. Rodar `database_init.py` pra criar o schema do DuckDB

### ESTA SEMANA
8. Adaptar `02_screener.py` para dual universe (será entregue como `02_screener_v2.py` atualizado)
9. Rodar primeiro screener completo (modo sample primeiro, depois full)
10. Configurar cron pra rodar todo dia útil às 6h

### SEMANA 2
11. Configurar scheduled task no Cowork para Layer 3
12. Conectar com pasta do OneDrive
13. Primeiros 5 dias de análises LLM acumuladas no DB
