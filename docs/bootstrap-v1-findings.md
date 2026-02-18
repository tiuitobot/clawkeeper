# Bootstrap v1 — Findings Consolidados

*2026-02-17. Documento de referência para paper e pipeline industrial.*
*Fonte: bootstrap 5 rounds (Haiku), audit externo (Opus Chat), verificação nos dados.*

---

## 1. Taxonomia Emergente de Closure (R1)

O modelo (claude-haiku-4-5), ao analisar 50 PRs em modo supervisionado (R1), **taxonomizou espontaneamente** os motivos de closure em 7 categorias sem instrução prévia:

| # | Categoria | Descrição | Exemplos na amostra |
|---|-----------|-----------|---------------------|
| 1 | **Governance/hygiene** | Branch contaminada, scope explosion, violação de regras do repo | PR #4509 (100 files, 20+ labels, maintainer: "branch is too noisy") |
| 2 | **Duplicate/superseded** | PR fechada porque outra PR resolve o mesmo problema | PR #6461 (closed, #9595 é canônico), PR #14031 (closed in favor of #14068) |
| 3 | **Architectural veto** | Código funcional, review positivo, mas maintainer prefere abordagem diferente | PR #10652 (core vs channel-extension), PR #7358 (rclone > NATS JetStream), PR #18171 (redirected to plugin) |
| 4 | **Author block** | Autores com histórico de rejeição sistemática | shtse8 (94 PRs, 5.3% merge), WeatherPal-AI (32 PRs, 0%) |
| 5 | **Auto-closure** | Bot (CLAWDINATOR) fecha automaticamente por regras de triage | Padrão: "Closing as duplicate of #XXXX. If incorrect, comment and we can reopen." |
| 6 | **Author-initiated** | Autor fecha próprio PR (preferiu fork, mudou abordagem) | Detectável por: closer == author |
| 7 | **Stale/obsolete** | PR ultrapassada por mudanças no main, changelog incompatível | PR #13270 (changelog temporal) |

### Implicação para pipeline

Cada categoria mapeia para um **método de detecção diferente**, com custo e complexidade crescentes:

```
CUSTO ZERO (determinístico):
  ├─ Governance/hygiene  → Quality gate (>N files, >N labels, CI fail)
  ├─ Author block        → Lookup (is_low_merge_author: 5+ PRs, <5% merge)
  ├─ Auto-closure        → Lookup (is_bot_closed: commenter is bot)
  └─ Stale/obsolete      → Feature temporal (pr_age_hours > threshold)

CUSTO MÉDIO (cross-PR comparison):
  └─ Duplicate/superseded → Dedupe engine (file overlap + semantic similarity)

CUSTO ALTO (LLM required):
  ├─ Architectural veto   → Mature model (intent analysis + vision alignment)
  └─ Author-initiated     → Parcialmente LLM (comment sentiment + author patterns)
```

### Significância

Esta taxonomia não foi projetada — **emergiu dos dados**. O modelo recebeu a tarefa "extract governance signals" e organizou os closures em categorias funcionais que correspondem a decisões arquiteturais do pipeline. Isso sugere que LLMs podem servir como **ferramentas de design de pipeline**, não apenas como componentes.

---

## 2. Data Leakage no Bootstrap v1

### Descoberta

O bootstrap v1 sofre de **dois tipos de leakage** que invalidam as métricas de accuracy (R2=96%, R3-R5=94%):

#### Leakage Tipo 1: Outcomes nos comments

Pelo menos 7/50 PRs (14%) contêm o outcome explicitamente nos dados passados ao modelo:
- "Merged via squash" (PRs #9973, #10776, #13500, #15343, #16697)
- "Merged by mistake" + revert (PR #18563)
- "Prior signal explicitly merged" (PR #4026 — contaminação do contexto R1)

O modelo escreve "CONFIRMED MERGE" citando merge commit hashes presentes nos comments.

#### Leakage Tipo 2: Contexto do R1

Rounds 2-5 recebem signals do R1 como contexto. R1 viu outcomes. Os signals mencionam "merged"/"closed" para os **mesmos PRs** que aparecem nos rounds de predição. O modelo faz lookup, não predição.

### Evidência

```
R2 PR#4026: "PR#4026 directly mirrors the prior successful signal: XL file count 
             that merged due to high engagement"
R3 PR#918:  "Prior signal PR#918 shows identical pattern: very short lifespan"
```

O modelo está **reconhecendo** PRs que viu no R1, não **predizendo** PRs novos.

### Impacto

- **Accuracy reportada (94-96%) é inválida** como métrica de predição
- **Accuracy real sobre PRs genuinamente ambíguas é desconhecida**
- **Os artefatos de R1 (signals, taxonomy, patterns) permanecem válidos** — R1 era supervisionado por design
- **Os pesos logit iniciais são indicativos** mas devem ser recalculados no Stage 1 com dataset completo

### Correções aplicadas no Bootstrap v2

1. Sanitização de comments: strip outcomes (merge status, commit hashes, bot merge messages)
2. PRs diferentes por fase: R1 supervisado usa subset A, predição usa subset B
3. Contexto cross-round sem outcomes: signals de rounds anteriores stripped de merge/close info

---

## 3. Ceiling do Modelo Individual (~85%)

### Dados

- **519 pares explícitos** de dedupe extraídos dos comments (regex: "closing as duplicate of #XXXX", "superseded by #XXXX", etc.)
- **384 clusters** de duplicação: 321 pares, 46 trios, 11 quartetos, 3 sextetos, 1 noneto, 1 cluster de 16
- **872 PRs** envolvidos = **27% do dataset**
- Na amostra de 50: ~8 PRs (16%) fechados por razões relacionais (dedupe/superseded)

### Implicação

Um modelo que avalia PRs **individualmente** não pode predizer closures que dependem de **relação entre PRs**. Ceiling teórico:
- ~85% accuracy com features individuais (merge/close baseado na PR sozinha)
- ~85-94% com cross-PR comparison (dedupe detection)
- ~94-97% com LLM mature model (architectural veto, intent analysis)
- ~97%+ provavelmente irrecuperável (contexto informal, decisões off-record)

### Validação pendente

O Stage 1 (logit formal em 3.233 PRs) vai medir o ceiling real do modelo individual. Se AUC-ROC > 0.85 com features determinísticas, confirma a estimativa.

---

## 4. Features Descobertas (não previstas no model_spec v0.5)

| Feature | Tipo | Poder preditivo | Custo |
|---------|------|-----------------|-------|
| `is_low_merge_author` | Binária (5+ PRs, <5% merge) | 34 autores, 336 PRs (10.4%), ~0% merge | Zero (lookup) |
| `is_bot_closed` | Binária (CLAWDINATOR como closer/commenter) | ~100% → closed | Zero (lookup) |
| `closure_taxonomy` | Categórica (7 classes) | Estratifica predição por método | LLM (R1 only) |
| `was_reverted` | Binária (merge seguido de revert PR) | Reclassifica Y (merged→closed) | Zero (title regex) |
| `has_revert_pr` | Binária (existe PR com "revert #N" no título) | Contamina Y se não tratado | Zero (title regex) |

### Verificações nos dados

| Claim (Opus Chat) | Verificação | Resultado |
|---|---|---|
| shtse8 = 94 PRs, 0% merge (blocked) | `all_historical_prs.json` | **5 merges (5.3%)** — não é 0% |
| Merge-then-revert contamina Y significativamente | Busca por "revert" em títulos | **6 PRs com "revert" no título, 3 merged** — impacto ~0.1% |
| 34 autores com 5+/0% merge = 10.4% do dataset | Confirmado | ✅ 336 PRs |
| 519 pares explícitos de dedupe | Confirmado | ✅ regex nos comments |

---

## 5. Métricas de Custo (Haiku Bootstrap)

| Métrica | Valor |
|---------|-------|
| Custo por round (10 PRs/batch × 5 batches) | ~$0.04 |
| Custo total v1 (5 rounds) | ~$0.20 |
| Custo projetado v2 (10 rounds × 100 PRs) | ~$0.80 |
| Tokens input/round | ~21-28k |
| Tokens output/round | ~26-29k |
| Tempo por round | ~3 min |
| Custo por PR analisado | ~$0.004 |

### Comparação com alternativas

- Reviewer humano: ~15-30 min/PR × $50/h = ~$12-25/PR
- Opus 4.6: ~$0.15/PR (estimado)
- Sonnet 4.5: ~$0.03/PR (estimado)
- **Haiku 4.5: ~$0.004/PR** ← 3.000x mais barato que humano

---

## 6. Meta-Insights (Sobre o Processo)

### 6.1 Confirmation bias do executor

O executor (Tiuito) construiu os scripts, rodou os rounds, e celebrou 96% accuracy. O auditor externo (Opus Chat) leu o report frio e identificou:
1. 96% é red flag, não sucesso
2. Flat line não demonstra learning
3. Data leakage nos comments

**Padrão:** quem constrói tem investment bias. Quem audita sem investimento vê mais claro.

### 6.2 Transfer manual como forcing function

Bruno copia o report pra Opus Chat manualmente. Esse ato força Bruno a **ler os dados** antes de enviar. Nesse processo, Bruno identificou:
- Falta de estudo de dedupe
- Dados dos rounds 2+ referenciando os mesmos PRs
- Proporção de clusters pra holdout

Automatizar o audit eliminaria esse efeito colateral valioso.

### 6.3 Dois estágios de audit

| Estágio | Escopo | Método | Quem |
|---------|--------|--------|------|
| 1 | Mecânico (crons, infra, sweeper) | Automático (agente interno) | Tiuito |
| 2 | Decisional (design, paper, stages) | Manual (transfer → instância externa) | Bruno + Opus Chat |

Critério de separação: **reversibilidade**. Erro mecânico = conserta sozinho. Erro decisional = consequência irreversível.

### 6.4 LLM como designer de pipeline

A taxonomia emergente de closure (Seção 1) sugere que LLMs podem servir como **ferramentas de design**, não apenas componentes. O modelo não apenas classificou PRs — organizou as classes de forma que mapeia diretamente para decisões arquiteturais do pipeline (quality gate → dedupe → LLM → active learning).

Implicação para pipeline industrial: uma etapa de "LLM analyzes sample → proposes pipeline architecture" pode ser formalizada como Step 0 do setup de qualquer repo.

### 6.5 Continuidade cria viés

O mesmo mecanismo que torna o agente contínuo valioso (investment, context, calibration) é o que cria blind spots (confirmation bias, attachment to results). A solução não é reduzir continuidade — é institucionalizar o check externo.

---

## 7. Ações Concretas para Stage 1

| # | Ação | Prioridade |
|---|------|-----------|
| 1 | Sanitizar comments: strip merge status, commit hashes, bot messages antes de passar ao modelo | **Crítica** (bloqueia v2) |
| 2 | Adicionar `is_low_merge_author` ao model_spec (34 autores, 336 PRs, threshold 5+/<5%) | Alta |
| 3 | Adicionar `is_bot_closed` ao model_spec (CLAWDINATOR auto-closures) | Alta |
| 4 | Implementar filtro `was_reverted` na variável dependente (3 casos, impacto marginal) | Baixa |
| 5 | Split global 70/30 com seed fixo antes de qualquer modelo tocar holdout | **Crítica** (bloqueia v2) |
| 6 | Logit formal em 3.233 PRs com scikit-learn, AUC-ROC como métrica primária | Alta |
| 7 | Documentar ceiling ~85% modelo individual como constraint arquitetural | Média |
| 8 | Audit externo pós-Stage via transfer manual obrigatório | Processo |

---

*Este documento é referência permanente. Não editar retroativamente — adicionar addendums datados.*
