# Cowork Scheduled Task — Layer 3 (Análise LLM Diária)

Definição completa da tarefa agendada que roda Layer 3 do Research Lab via Cowork (Claude Enterprise), sem custo marginal por análise.

**Quando configurar:** Semana 2 do roadmap, depois que Layer 1 + 2 estiverem rodando localmente e gerando sinais.

**O que essa task faz:** lê os top 20 sinais do dia (10 do Universo A + 10 do Universo B, gerados pelo screener local), aplica o Prompt v3 em cada, e devolve análises estruturadas (Markdown + JSON) na pasta do projeto.

---

## Pré-requisitos antes de ativar

- [ ] Layer 1 + 2 rodando localmente todo dia útil às 6h
- [ ] OneDrive sincronizando a pasta `~/equity-research-lab/data/` para `~/OneDrive/research_lab/`
- [ ] Cowork tem permissão de leitura/escrita na pasta do OneDrive (via `request_cowork_directory`)
- [ ] Prompt v3 (`prompts/v3.0.md`) já está versionado no Git

---

## Configuração da scheduled task

### Como criar

No Cowork, abrir nova conversa e dizer:

> Use o skill `schedule` pra criar a tarefa abaixo.

Ou diretamente:

> Crie uma scheduled task que rode todo dia útil às 7h da manhã (horário de Brasília) com o seguinte prompt:

### O prompt da scheduled task

```
Você é o Layer 3 do Equity Research Lab. Sua função é análise LLM diária dos top 20 candidatos do screener (Layer 2).

PASSO 1 — Localizar os sinais do dia.

Leia o arquivo mais recente em `~/OneDrive/research_lab/signals/`:
- `YYYY-MM-DD_universe_A.parquet` (top 5 mid/large caps)
- `YYYY-MM-DD_universe_B.parquet` (top 5 small caps)

Se a data de hoje for um dia útil (segunda a sexta) e os arquivos existirem, prossiga. Se não existirem, registre em `~/OneDrive/research_lab/logs/llm_YYYY-MM-DD.log`:
"AVISO: arquivos de sinais não encontrados para {data}. Screener local pode não ter rodado. Abortando análise LLM."
E pare.

PASSO 2 — Para cada ticker (10 total: 5 do A + 5 do B), rodar o Prompt v3.

Para cada ticker em ordem (ranking decrescente do composite_score):

  a) Substituir as variáveis do Prompt v3 com os dados do parquet:
     - [TICKER] = ticker
     - [DATE_YYYY-MM-DD] = data atual
     - [SIGNAL_ID] = signal_id do banco (ou null se não conseguir ler do DB)
     - [RANK_POSITION] = rank dentro do universo
     - [TOP_N] = 5
     - [COMPOSITE_SCORE], [CLOSE], [RSI14], [MM50], [MM200], [PCT_BELOW_52W_HIGH], 
       [PCT_ABOVE_52W_LOW], [VOL_AVG_20] = valores do parquet

  b) Executar o prompt usando busca web (WebSearch + WebFetch) para dados em tempo real.

  c) Validar que o output:
     - Contém bloco markdown narrativo
     - Contém bloco JSON parseável (entre ```json e ```)
     - Bear case não está vazio (mínimo 2 riscos)
     - operational_plan tem stop < entry < target_1 < target_2
     - rr_target_1 >= 1.5

  d) Salvar outputs em:
     - `~/OneDrive/research_lab/analyses/YYYY-MM-DD/{TICKER}_universe_{A|B}.md` (markdown)
     - `~/OneDrive/research_lab/analyses/YYYY-MM-DD/{TICKER}_universe_{A|B}.json` (JSON)

  e) Se validação falhar, registrar warning no log e tentar 1 retry. Se falhar de novo, marcar 
     ticker como `analysis_failed: true` no log e prosseguir para o próximo.

PASSO 3 — Resumo executivo

Após processar todos os 20 tickers, criar um relatório consolidado em:
`~/OneDrive/research_lab/reports/YYYY-MM-DD_daily.md`

Conteúdo do relatório:
- Data e horário da geração
- Top 5 Universo A (com verdict + confidence + tese 1-frase de cada)
- Top 5 Universo B (com verdict + confidence + tese 1-frase de cada)
- Highlights: qual teve maior confidence_score? Qual teve verdict COMPRAR?
- Discrepâncias com o screener: tickers em que o LLM disse EVITAR (são red flags do filtro técnico)
- Catalisadores próximos 7 dias (compilados dos JSON de todas as análises)
- Watchlist manual analisada (se houver tickers ativos em `~/OneDrive/research_lab/watchlist_manual.csv`)

PASSO 4 — Log de execução

Registrar em `~/OneDrive/research_lab/logs/llm_YYYY-MM-DD.log`:
- Hora de início e fim
- Tickers analisados com sucesso
- Tickers com erro (com mensagem)
- Custo estimado de tokens (input + output)
- Eventuais avisos sobre dados conflitantes ou indisponíveis

REGRAS DURAS:
- NUNCA inventar dados quantitativos. Se não conseguir confirmar via busca web, marque como null e adicione warning.
- Bear case é OBRIGATÓRIO em toda análise (mesmo com bull case forte). Mínimo 2 riscos.
- Verificação cruzada: para preço atual, EPS, e target de analistas, confirmar com 2+ fontes.
- Quando duas fontes discordarem, investigar e documentar em `data_conflicts_resolved` do JSON.
- Termos técnicos: explicar em parênteses na primeira menção (linguagem simples).

FAILURE MODES:
- Se WebSearch não retornar resultados úteis para um ticker, registrar `data_source_failure` e prosseguir
- Se Cowork bater rate limit, pausar 60s e retomar
- Se OneDrive não estiver acessível, abortar e logar
- Se 3+ tickers falharem em sequência, abortar e logar erro de sistema

INTEGRAÇÃO COM PYTHON LOCAL:
A inserção dos JSONs no DuckDB acontece via script Python local (`src/insert_analyses.py`) que roda às 8h via cron. Sua função aqui é só gerar os arquivos. Não tente inserir no DB diretamente.
```

### Schedule sugerido

| Horário | Tarefa | Origem |
|---|---|---|
| 6h | Layer 1 + 2 (screener) | cron local |
| 6h45 | Verifica que sinais foram gerados | cron local |
| 7h | **Layer 3 (LLM)** | **Scheduled task Cowork** |
| 8h | Layer 3.5 (insere JSONs no DuckDB) | cron local |
| 20h | Layer 4 (outcome tracker) | cron local |

Brasília está UTC-3, NY abre 9h30 = 10h30 Brasília. Análise está pronta antes do mercado abrir.

---

## Limitações conhecidas (e quando migrar para Anthropic API)

Cowork scheduled tasks têm limitações:
- Throughput controlado (não dá pra rodar 50 análises em paralelo)
- Não permite controle programático fino sobre retries
- Output é texto livre — validação JSON precisa ser tolerante a malformações
- Sem garantia formal de SLA

**Migrar para Anthropic API se:**
- Você quer subir de 10 para 30+ análises/dia
- Validação de JSON está falhando consistentemente
- Precisa de modelo específico (ex: Sonnet vs Opus)
- Quer A/B testing automatizado de prompts

Custo da migração: troca o Layer 3 de Cowork pra Python local com `anthropic` SDK. Estimativa: 2-4h de dev. Custo operacional: $20-40/mês.

---

## Watchlist Manual — Como adicionar ativos

Crie/edite o arquivo `~/OneDrive/research_lab/watchlist_manual.csv` com este formato:

```csv
ticker,added_date,reason,active
VST,2026-05-17,Acompanhar reversão técnica após capitulação,true
NEE,2026-05-17,Pós deal Dominion - watchful waiting,true
CCJ,2026-05-17,Tese estrutural nuclear; entrada em pullback,true
```

A scheduled task vai sempre analisar esses ativos adicionalmente aos top 20 (com flag `from_watchlist: true` no JSON).

Sugestão: manter no máximo 20 ativos ativos. Acima disso vira ruído operacional.

---

## Como ajustar/refinar a task posteriormente

Toda mudança no prompt vai por aqui:
1. Editar `prompts/v3.X.md` no Git (incrementar versão)
2. Atualizar a scheduled task copiando o novo prompt
3. No DuckDB, a coluna `prompt_version` vai diferenciar análises da versão antiga vs nova
4. Após ~30 dias com a nova versão, comparar hit rate / R-multiple entre versões via SQL

Exemplo de query para comparar versões:

```sql
SELECT 
  prompt_version,
  COUNT(*) AS n_analyses,
  AVG(CASE WHEN o.hit_target_1 THEN 1.0 ELSE 0.0 END) * 100 AS hit_rate_pct,
  AVG(o.r_multiple) AS avg_r_multiple
FROM analyses_llm a
JOIN signal_outcomes o ON o.ticker = a.ticker AND o.signal_date = a.analysis_date
WHERE o.days_elapsed = 30 AND NOT o.is_open
GROUP BY prompt_version
ORDER BY avg_r_multiple DESC;
```

---

## Checklist de ativação (quando chegar na Semana 2)

- [ ] Confirmar que screener local rodou nas últimas 5 sessões
- [ ] Confirmar que OneDrive sincroniza pasta `data/signals/` corretamente
- [ ] Testar manualmente o prompt v3 em 1 ticker (rodar como conversa normal, ver se output bate o esperado)
- [ ] Criar a scheduled task no Cowork com o prompt acima
- [ ] Ativar e aguardar primeira execução (no dia seguinte 7h)
- [ ] Verificar `~/OneDrive/research_lab/analyses/YYYY-MM-DD/` contém os arquivos esperados
- [ ] Verificar o relatório consolidado em `~/OneDrive/research_lab/reports/YYYY-MM-DD_daily.md`
- [ ] Escrever `src/insert_analyses.py` para inserir os JSONs no DuckDB (cron das 8h)
- [ ] Acompanhar por 5 dias úteis sem ajustes pra estabilidade
- [ ] Iterar no prompt se necessário (v3.1, v3.2, etc.)
