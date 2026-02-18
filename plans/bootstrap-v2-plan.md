# Bootstrap v2 — Plano de Execução

*2026-02-17. Consolidação de todas as decisões de design antes da implementação.*
*Princípio: "attention is all you need, but retention is what you write"*

---

## 1. Por que v2?

O Bootstrap v1 (5 rounds × 50 PRs) **validou** que Haiku extrai sinais de governança a $0.004/PR (Gate G1 superado), mas sofre de:

| Problema | Impacto | Correção no v2 |
|----------|---------|----------------|
| Data leakage (outcomes nos comments) | Accuracy 94-96% inválida | Sanitização obrigatória |
| Leakage cruzado (R1 signals contêm outcomes) | Modelo faz lookup, não predição | Cold start — zero contexto v1 |
| Amostra pequena (50 PRs, 250 observações) | Logit instável (EPV marginal) | 100 PRs × 10 rounds = 1.000 obs |
| Tarefa única (merge/close) | Ignora 27% do dataset (dedupe) | Tarefa dual: merge + dedupe |
| Sem holdout formal | Não mede generalização | Split global 70/30 antes de tudo |

**Meta:** Dataset limpo de ~1.000 observações para treinar logit formal no Stage 1.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────┐
│                 SPLIT GLOBAL (uma vez)           │
│  3.233 PRs → 70% treino (2.263) / 30% hold (970)│
│  Clusters de dedupe: nunca dividir um cluster    │
│  Seed fixo, determinístico, registrado           │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│            SAMPLER (por round)                   │
│  100 PRs do pool de treino (2.263)               │
│  ├─ ~80 PRs normais (estratificados)             │
│  └─ ~20 PRs de dedupe (6-8 clusters)            │
│  Sem repetição entre rounds                      │
│  Preserva merge rate ~24%                        │
│  ⚠️ DEDUPE DIRIGIDO: membros do mesmo cluster   │
│     caem no mesmo batch (não distribuídos)       │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│           SANITIZADOR (antes de cada round)       │
│  Strip dos comments:                             │
│  ├─ Merge status ("Merged via squash", hashes)   │
│  ├─ Bot closures ("Closing as duplicate of #X")  │
│  ├─ Referências cruzadas a PRs vencedoras        │
│  ├─ Close/merge timestamps                       │
│  └─ Outcome field (merged: true/false)           │
│  Preserva: reviews, discussion, labels, CI       │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│           HAIKU — TAREFA DUAL                    │
│                                                  │
│  Task A: Para cada PR, predizer MERGE ou CLOSE   │
│          + confidence (0-1) + reasoning           │
│          + extrair 34 features do model_spec      │
│          (inclui is_low_merge_author)            │
│                                                  │
│  Task B: Entre os PRs do batch, identificar      │
│          pares/trios que parecem duplicados       │
│          + confidence (0-1) + evidence            │
│          (file overlap, title similarity, etc.)   │
│                                                  │
│  Batches de 10 PRs (limite de output tokens)     │
│  ⚠️ Clusters de dedupe agrupados no mesmo batch  │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│       PATTERN EXTRACTOR (pós-round, R1-R3+)     │
│                                                  │
│  Analisa erros do round:                         │
│  ├─ Extrai patterns ABSTRATOS (sem PR numbers)   │
│  ├─ Ex: "PRs com approval mas sem maintainer     │
│  │   label tendem a fechar quando duplicatas"    │
│  └─ Acumula para injetar em R4-R10              │
│                                                  │
│  R1-R3: sem patterns (baseline puro)             │
│  R4-R10: patterns abstratos de rounds anteriores │
│          injetados no prompt                     │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│           SCORER (pós-round)                     │
│                                                  │
│  Merge: compare prediction vs ground truth       │
│  Dedupe: compare pares identificados vs          │
│          ground truth (clusters conhecidos)       │
│                                                  │
│  Métricas por round:                             │
│  ├─ Merge accuracy, precision, recall, F1        │
│  ├─ Dedupe precision, recall, F1                 │
│  ├─ Calibration (confidence vs actual)           │
│  └─ Erros persistentes (acumulados cross-round)  │
└───────────────┬─────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────┐
│        CONSOLIDADOR (após 10 rounds)             │
│                                                  │
│  ├─ Learning curve: baseline (R1-3) vs           │
│  │   learning (R4-10). Delta = paper metric.    │
│  ├─ Patterns que sobrevivem 5+ rounds → promote  │
│  ├─ Erros persistentes → análise de categoria    │
│  ├─ Logit weights iniciais (features × outcome)  │
│  ├─ Dedupe pairs consolidados + F1 final         │
│  └─ Artefatos para Stage 1                       │
└─────────────────────────────────────────────────┘
```

---

## 3. Regras de Contaminação (CRÍTICO)

| Regra | Descrição |
|-------|-----------|
| **Cold start** | Zero sinais/patterns do v1 entram no v2. V1 informou o *design*, não o *modelo*. |
| **Cross-round: patterns abstratos only** | R1-R3: zero contexto (baseline puro). R4-R10: patterns abstratos extraídos dos erros de rounds anteriores. Patterns NÃO citam PR numbers nem outcomes específicos — são generalizações. |
| **Sanitização de outcomes** | Comments sanitizados. Sem merge status, sem timestamps de outcome, sem bot messages de triage com resultado. |
| **Dedupe closure stripped** | PRs de dedupe perdem: "closing as duplicate of #X", "superseded by", referências ao PR vencedor. |
| **Holdout intocável** | 30% do dataset (970 PRs + ~115 clusters) NUNCA é visto pelo modelo. Só abre no Stage 1 final. |
| **PRs únicos por round** | Nenhum PR aparece em mais de um round. 10 rounds × 100 = 1.000 PRs ⊂ 2.263 treino. |

---

## 4. Split Global

### Dataset
- **Total:** 3.233 PRs (OpenClaw historical)
- **Merge rate:** ~24% (776 merged, 2.457 closed)
- **Dedupe:** 872 PRs em 384 clusters

### Procedimento
1. Listar todos os 384 clusters de dedupe
2. Shuffle com seed fixo (seed=42)
3. Split clusters 70/30: ~269 treino / ~115 holdout
4. PRs não-dedupe: shuffle com mesmo seed, split 70/30
5. Resultado final: ~2.263 treino / ~970 holdout
6. **Constraint:** um cluster NUNCA é dividido entre partições
7. Salvar em `data/split.json` com seed + timestamp + PR lists

### Validação do split
- Merge rate similar em ambas partições (±3%)
- Distribuição de labels similar
- Nenhum cluster dividido

---

## 5. Sampler v2

### Diferenças do v1
| Aspecto | v1 | v2 |
|---------|----|----|
| Tamanho | 50 PRs | 100 PRs |
| Rounds | 5 | 10 |
| Dedupe | Nenhum | ~20 PRs/round (6-8 clusters) |
| Repetição | Mesmos 50 PRs todo round | PRs únicos por round |
| Pool | 3.233 (todo dataset) | 2.263 (só treino) |

### Lógica de amostragem por round
1. **Pool disponível** = treino (2.263) − já usados em rounds anteriores
2. **Injetar dedupe:** selecionar 6-8 clusters do pool de treino de dedupe (~269 clusters) → ~15-20 PRs
3. **Agrupar dedupe em batches:** membros do mesmo cluster vão pro mesmo batch de 10. Completar batch com PRs normais até 10.
4. **Completar com normais:** estratificação por outcome × size × category até 100 PRs
5. **Preservar merge rate:** ~24 merged, ~76 closed (±3)
6. **Registrar** quais PRs e clusters foram usados, e qual batch contém cada cluster

### Injeção dirigida de dedupe (CORREÇÃO v2.1)
**Problema:** Com distribuição aleatória, P(2 membros de cluster no mesmo batch de 10) ≈ 9%. O modelo veria ~0.63 pares/round — quase zero oportunidade de detectar dedupe.

**Solução:** Forçar membros do mesmo cluster no mesmo batch.
- Cluster de 2 PRs → ambos no batch X, completar com 8 normais
- Cluster de 3 PRs → todos no batch X, completar com 7 normais
- Cluster de 4+ PRs → todos no batch X (se cabe), senão dividir em sub-batches com overlap de 1 PR
- ~6-8 clusters/round → ~6-8 dos 10 batches contêm pelo menos 1 cluster
- Batches restantes (~2-4) são 100% PRs normais (controle)

### Capacidade
- 10 rounds × 100 PRs = 1.000 PRs
- Pool de treino: 2.263 → sobram ~1.263 não amostrados (reserva)
- Clusters de treino: ~269 → 10 rounds × 7 clusters = ~70 usados, sobram ~199

---

## 6. Sanitização

### O que remover dos comments
```python
STRIP_PATTERNS = [
    r"[Mm]erged? (?:via|by|in)\b.*",
    r"[Cc]losing as duplicate of #\d+",
    r"[Ss]uperseded by #\d+",
    r"[Cc]losed? in favor of #\d+",
    r"[Rr]eplaced by #\d+",
    r"[Dd]up(?:licate)? of #\d+",
    r"[Aa]ddressed in #\d+",
    r"[Ff]ixed in #\d+",
    r"[Rr]esolved in #\d+",
    r"CLAWDINATOR.*(?:closing|duplicate|merged).*",
    # Merge commit references
    r"[0-9a-f]{40}",  # full SHA
    r"merged commit [0-9a-f]+",
]
```

### O que preservar
- Code reviews (APPROVE, REQUEST_CHANGES, COMMENT)
- Discussion técnica nos comments
- CI/test results mencionados
- Labels (todas)
- Files changed
- Author, timestamps de criação (NÃO de merge/close)

### Campos removidos do PR
- `merged` (boolean)
- `merged_at` (timestamp)
- `closed_at` (timestamp)
- `state` (open/closed)

### Campos preservados
- `created_at`, `title`, `user`, `labels`, `additions`, `deletions`, `changed_files`, `draft`
- `comments` (sanitizados), `reviews`, `files`

---

## 7. Prompt — Tarefa Dual

### Fase Baseline (R1-R3) — sem patterns
```
You are analyzing pull requests from an open-source project.
Merge rate is approximately 24%. You do not know the outcome of any PR below.

## Task A — Merge Prediction
For each PR, predict whether it will be MERGED or CLOSED.
Provide:
- prediction: "merged" or "closed"
- confidence: 0.0 to 1.0
- reasoning: key factors driving your prediction

Also extract these features from each PR:
{feature_spec_from_model_spec.json — 34 features, includes is_low_merge_author}

## Task B — Duplicate Detection
Among the PRs in this batch, identify any pairs or groups that appear
to address the same issue or feature (duplicates/superseded).
For each suspected pair:
- pr_a, pr_b (or pr_a, pr_b, pr_c for trios)
- confidence: 0.0 to 1.0
- evidence: what makes them similar (file overlap, title, intent)

## Output (JSON)
{
  "predictions": [
    {
      "pr_number": 123,
      "prediction": "merged",
      "confidence": 0.85,
      "reasoning": "...",
      "features": {"feature_name": "value", ...}
    }
  ],
  "duplicates": [
    {
      "prs": [123, 456],
      "confidence": 0.7,
      "evidence": "..."
    }
  ]
}

## PRs:
{batch_of_10_prs}
```

### Fase Learning (R4-R10) — com patterns abstratos
Mesmo prompt acima + seção adicional antes dos PRs:

```
## Learned Patterns (from prior rounds)
The following patterns were observed in previous rounds. They are
abstract generalizations — no specific PR numbers or outcomes.
Use them to inform your predictions, but do not assume they apply
to every case.

{accumulated_abstract_patterns}
```

### Extração de Patterns Abstratos (pós-round)
Após scoring de cada round (R1+), analisar os erros e extrair generalizações:
- **Input:** lista de erros (PR features que o modelo viu + se errou pra merge ou close)
- **Output:** patterns abstratos como:
  - "PRs with maintainer approval but no maintainer label tend to close when they overlap with existing PRs"
  - "Large PRs (>500 LOC) with no CI green signal have <10% merge rate"
  - "Authors with 5+ PRs and <5% merge rate are effectively blocked"
- **Constraints:** NÃO citar PR numbers. NÃO mencionar outcomes específicos. Só generalizações.
- R1-R3: patterns extraídos mas NÃO injetados (acumulam pra R4)
- R4-R10: patterns de R1 até R(N-1) injetados no prompt

### Medição de Learning
- **Baseline:** média accuracy R1-R3 (sem patterns)
- **Learning:** média accuracy R4-R10 (com patterns)
- **Delta = learning effect.** Se positivo e significativo → patterns abstratos têm valor. Essa é a métrica do paper.
- **Teste estatístico:** t-test R1-R3 vs R4-R10 (7 observações cada lado — poder estatístico baixo, mas direcional)

**Nota:** Batches de 10 PRs. Task B detecta duplicados *dentro* do batch. Clusters de dedupe são dirigidos ao mesmo batch pelo sampler. Cross-batch dedup é tarefa do Stage 1 (embeddings + similarity search).

### Feature `is_low_merge_author` (CORREÇÃO v2.1)
**Problema:** O R1 do v1 revelou que certos autores (e.g. shtse8: 94 PRs, 5.3% merge) são quase determinísticos. Sem essa informação, Haiku erra sistematicamente ~3-5% das predições.

**Solução:** Adicionar ao model_spec como feature #34:
- **Nome:** `is_low_merge_author`
- **Definição:** autor com 5+ PRs no histórico e <5% merge rate
- **Tipo:** binária
- **Custo:** zero (lookup no dataset)
- **Impacto estimado:** 34 autores, 336 PRs (10.4% do dataset)

O Haiku recebe a informação do author name + número de PRs anteriores. A feature é computável deterministicamente mas o modelo precisa *saber que é relevante*. Alternativa: incluir no prompt como contexto ("Author X has submitted N PRs, M merged (P%)") e deixar o modelo inferir.

---

## 8. Scoring

### Merge prediction
| Métrica | Cálculo |
|---------|---------|
| Accuracy | correct / total |
| Precision (merge) | TP / (TP + FP) |
| Recall (merge) | TP / (TP + FN) |
| F1 (merge) | harmonic mean |
| Calibration | bins de confidence vs accuracy real |

### Dedupe detection
| Métrica | Cálculo |
|---------|---------|
| Precision | pares corretos / pares identificados |
| Recall | pares corretos / pares reais no batch |
| F1 | harmonic mean |
| Cluster accuracy | clusters corretamente agrupados / clusters reais |

### Erros persistentes
PR que erra em 3+ rounds → marca como "persistente" com categoria:
- Political/contextual (irreducível)
- Temporal (precisa de info externa)
- Ground truth ambíguo (e.g. merged then reverted)
- Dedupe missed (cluster não detectado)

---

## 9. Execução

### Modo: Script autônomo (NÃO Opus polling)
**Lição do v1:** Opus polling Haiku desperdiça $5-8 em contexto para $0.20 de trabalho Haiku. 3 compactações durante v1.

```bash
# Execução completa — roda sozinho, salva tudo em disco
python3 scripts/bootstrap_v2.py --rounds 10 --prs-per-round 100 --seed 42
```

O script:
1. Carrega/cria split global (`data/split.json`)
2. Para cada round 1-10:
   a. Amostra 100 PRs (com injeção dirigida de dedupe — clusters no mesmo batch)
   b. Sanitiza comments
   c. Se round ≥ 4: carrega patterns abstratos acumulados dos rounds anteriores
   d. Chama Haiku em batches de 10 (dedupe clusters co-located)
   e. Parseia resposta JSON
   f. Faz scoring contra ground truth (merge + dedupe)
   g. Extrai patterns abstratos dos erros (sem PR numbers, sem outcomes)
   h. Salva `data/bootstrap_v2/round_{N}_results.jsonl`
   i. Salva `data/bootstrap_v2/round_{N}_scores.json`
   j. Salva `data/bootstrap_v2/round_{N}_patterns.json`
3. Consolida: learning curve (baseline R1-3 vs learning R4-10), patterns, logit weights, dedupe F1
4. Salva `data/bootstrap_v2/consolidated.json`

### Custo estimado
| Item | Cálculo | Custo |
|------|---------|-------|
| Input tokens/batch | ~3k (10 PRs sanitizados) | |
| Output tokens/batch | ~2k (predictions + features) | |
| Batches/round | 10 | |
| Rounds | 10 | |
| Total batches | 100 | |
| **Total estimado** | ~500k input + ~200k output | **~$0.80** |

### Tempo estimado
- ~20-30s por batch (Haiku)
- 100 batches × 25s = ~42 min
- Com rate limiting + overhead: **~1h**

---

## 10. Artefatos de saída

```
data/
  split.json                          # Split global (seed, PR lists, cluster lists)
  bootstrap_v2/
    round_{1-10}_results.jsonl        # Predictions + features + dedupe per PR
    round_{1-10}_scores.json          # Accuracy, F1, calibration per round
    round_{1-10}_sample.json          # Which PRs were in this round
    consolidated.json                 # Learning curve, promoted patterns, logit weights
    errors_persistent.json            # PRs errados em 3+ rounds
    dedupe_consolidated.json          # Dedupe pairs detected, F1 por round
    execution_log.txt                 # Timestamps, costs, errors
```

---

## 11. Gates de Validação

| Gate | Critério | Ação se falhar |
|------|----------|---------------|
| G1 | Merge accuracy ≥ 70% na média geral (R1-R10) | Investigar — features insuficientes? |
| G2 | Learning effect: média(R4-R10) > média(R1-R3) | Se flat/negativo: patterns abstratos não ajudam, reportar como finding |
| G3 | Dedupe F1 ≥ 0.5 nos batches com clusters dirigidos | Se <0.5: dedupe intra-batch com co-location não funciona, mudar pra embeddings |
| G4 | Calibration razoável (confidence ~= accuracy) | Se descalibrado: modelo confiante demais ou de menos |
| G5 | ≤10 erros persistentes políticos | Se >10: ceiling mais baixo que estimado |
| G6 | `is_low_merge_author` erros ≤ 2% | Se >2%: feature não está sendo usada pelo modelo |

---

## 12. O que NÃO entra no v2

| Item | Motivo |
|------|--------|
| Signals do v1 | Contaminação |
| Patterns do v1 | Contaminação |
| initial_logit.json do v1 | Baseado em dados leaked |
| PRs do holdout (30%) | Reservados para Stage 1 |
| Cross-batch dedup | Escopo do Stage 1 (embeddings) |
| Active learning | Escopo do Stage 2 |

---

## 13. Risco e Mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Sanitização incompleta (leakage residual) | Média | Alto | Audit manual em 10 PRs antes de rodar |
| Haiku não detecta dedupe intra-batch | Alta | Médio | Gate G3 + fallback pra embeddings |
| Rate limit Anthropic | Baixa | Baixo | Sleep entre batches, retry logic |
| JSON parse error | Média | Baixo | Fallback regex, save raw |
| Merge rate distorcida na amostra | Baixa | Médio | Validação pós-sampling |

---

## 14. Stage 0.8 — Ground Truth Enrichment (pré-bootstrap)

### Problema
Regex encontrou 133 clusters (193 edges) dos ~384 esperados. Os 326 edges dropados referenciavam Issues (não PRs). Além disso, ~425 relações implícitas (superseded sem referência explícita) não são capturáveis por regex. Ground truth incompleto → recall do Haiku medido contra piso, não teto.

### Princípio
"Deterministic first" ≠ "deterministic only". Regex dá o piso. LLM expande o teto. Quem prepara ground truth deve ser **estritamente mais capaz** que quem é testado — senão é Haiku avaliado contra Haiku.

### Solução
**Sonnet (sem thinking)** com acesso completo (comments não sanitizados, outcomes visíveis, cross-PR comparison) faz enrichment do ground truth UMA VEZ.

| Aspecto | Ground Truth (Sonnet) | Bootstrap (Haiku) |
|---------|----------------------|-------------------|
| Modelo | Sonnet 4.5 (no think) | Haiku 4.5 |
| Acesso | Completo (outcomes, comments, cross-PR) | Restrito (sanitizado, batch de 10) |
| Objetivo | Encontrar TODOS os pares | Testar detecção com info limitada |
| Frequência | Uma vez | 10 rounds |

### Execução
1. Pegar os 2.263 PRs de treino
2. Agrupar em batches de ~20-30 PRs (por proximidade temporal ou file overlap pré-computado)
3. Sonnet (no think) analisa cada batch: "quais destes PRs são duplicados/superseded?"
4. Union-find nos pares detectados → clusters enriquecidos
5. Merge com clusters do regex (133 + novos)
6. Validação manual: sample de 20 pares novos, conferir se são reais
7. Salvar `data/dedupe_ground_truth_enriched.json`

### Custo estimado
- ~80-100 batches de 25 PRs
- Sonnet: ~$0.03/batch (input) + ~$0.01/batch (output) = ~$3-4 total
- One-shot, não recorrente

### Gate
Se enrichment encontrar <30 clusters novos: regex já cobria a maioria. Se >100: LLM layer é essencial pro pipeline.

---

## 15. Sequência de Implementação

```
1. [x] build_split.py         — split global 70/30 com cluster constraint + validação ✅
2. [x] sanitize.py            — sanitizador de comments + testes ✅
3. [x] sample_v2.py           — sampler com injeção DIRIGIDA de dedupe ✅
4. [x] extract_patterns.py    — extrai patterns abstratos dos erros ✅
5. [x] bootstrap_v2.py        — orquestrador: rounds + batches + fases ✅
6. [x] score_round.py         — scoring merge + dedupe por round ✅
7. [x] consolidate_v2.py      — consolidação: learning curve, delta ✅
8. [x] Atualizar model_spec   — is_low_merge_author como feature #34 ✅
9. [ ] Fix: PR formatting     — markdown legível em vez de JSON bruto no prompt
10. [ ] Fix: author stats     — pré-computar author history e injetar no contexto do PR
11. [ ] Fix: import paths     — resolver `from sanitize import` para ser path-agnostic
12. [ ] Stage 0.8             — ground truth enrichment (Sonnet no-think, ~$3-4, one-shot)
13. [ ] Re-gerar samples      — com ground truth enriquecido (mais clusters disponíveis)
14. [ ] Audit manual          — 10 PRs sanitizados, conferir leakage residual
15. [ ] Execução              — `python3 scripts/bootstrap_v2.py --rounds 10 --seed 42`
16. [ ] Report + PDF          — step report com resultados + learning curve
```

**Status:** Scripts 1-8 ✅ (Codex, 5min, $0.02). Fixes 9-11 pendentes (~30min). Stage 0.8 (~1h). Execução (~1h). Total restante: ~3h.

---

## 15. Addendum — Review Bruno (2026-02-17, 8.5/10)

Três correções aplicadas ao plano original:

### 15.1 Learning phased (ESTRUTURAL)
**Problema:** Sem cross-round learning, cada round é independente. G2 (learning curve) era impossível de satisfazer — R10 identicamente informado que R1. Diferença entre rounds = variância amostral.

**Correção:** R1-R3 baseline puro (sem patterns). R4-R10 com patterns abstratos extraídos dos erros de rounds anteriores. Patterns não citam PR numbers nem outcomes — são generalizações. Delta (média R4-R10 − média R1-R3) é a métrica real de learning.

**Nota:** v1 tentava fazer learning mas implementou errado (signals com outcomes = lookup). v2 original corrigiu jogando fora o mecanismo. v2.1 reimplementa o mecanismo limpo.

### 15.2 Dedupe dirigido (PROBABILÍSTICO)
**Problema:** Com distribuição aleatória de clusters em batches de 10, P(par no mesmo batch) ≈ 9%. Com 7 clusters/round, ~0.63 pares aterrisam no mesmo batch. Modelo vê quase zero oportunidade de dedupe.

**Correção:** Sampler garante que membros do mesmo cluster caem no mesmo batch. ~6-8 batches contêm pelo menos 1 cluster, 2-4 batches são controle puro.

### 15.3 `is_low_merge_author` (FEATURE GAP)
**Problema:** Feature descoberta no v1 R1 (34 autores, 336 PRs, ~0% merge) não estava no model_spec. Sem ela, Haiku erra sistematicamente ~3-5%.

**Correção:** Adicionar como feature #34 ao model_spec antes da execução. Incluir author merge history no contexto do PR.

---

*Este documento é o contrato de execução. Qualquer desvio deve ser justificado e registrado aqui como addendum datado.*
*v2.1 — incorpora review Bruno 2026-02-17 (learning phased, dedupe dirigido, is_low_merge_author).*
