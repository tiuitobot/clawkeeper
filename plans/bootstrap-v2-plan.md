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
│  └─ ~20 PRs de dedupe (6-8 clusters injetados)  │
│  Sem repetição entre rounds                      │
│  Preserva merge rate ~24%                        │
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
│          + extrair 33 features do model_spec      │
│                                                  │
│  Task B: Entre os PRs do batch, identificar      │
│          pares/trios que parecem duplicados       │
│          + confidence (0-1) + evidence            │
│          (file overlap, title similarity, etc.)   │
│                                                  │
│  Batches de 10 PRs (limite de output tokens)     │
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
│  ├─ Learning curve (accuracy por round)          │
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
| **Sem cross-round leakage** | Cada round recebe apenas: model_spec (features) + prompt. Sem signals de rounds anteriores. |
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
3. **Completar com normais:** estratificação por outcome × size × category até 100
4. **Preservar merge rate:** ~24 merged, ~76 closed (±3)
5. **Registrar** quais PRs e clusters foram usados

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
{feature_spec_from_model_spec.json — 33 features}

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

**Nota:** Batches de 10 PRs. Task B só pode detectar duplicados *dentro* do batch. Design deliberado — simula o cenário real onde o modelo vê um subset, não o dataset completo. Cross-batch dedup é tarefa do Stage 1 (embeddings + similarity search).

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
   a. Amostra 100 PRs (com injeção de dedupe)
   b. Sanitiza comments
   c. Chama Haiku em batches de 10
   d. Parseia resposta JSON
   e. Faz scoring contra ground truth
   f. Salva `data/bootstrap_v2/round_{N}_results.jsonl`
   g. Salva `data/bootstrap_v2/round_{N}_scores.json`
3. Consolida: learning curve, patterns, logit weights iniciais, dedupe F1
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
| G1 | Merge accuracy ≥ 70% no round 10 | Investigar — features insuficientes? |
| G2 | Learning curve não-flat (R10 > R1) | Se flat: prompt não aprende, redesenhar |
| G3 | Dedupe F1 ≥ 0.5 (dentro do batch) | Se <0.5: dedupe intra-batch não funciona, mudar pra embeddings |
| G4 | Calibration razoável (confidence ~= accuracy) | Se descalibrado: modelo confiante demais ou de menos |
| G5 | ≤10 erros persistentes políticos | Se >10: ceiling mais baixo que estimado |

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

## 14. Sequência de Implementação

```
1. [ ] build_split.py        — split global 70/30 + validação
2. [ ] sanitize.py           — sanitizador de comments + testes
3. [ ] sample_v2.py          — sampler com injeção de dedupe
4. [ ] bootstrap_v2.py       — orquestrador principal (rounds + batches)
5. [ ] score_round.py        — scoring merge + dedupe
6. [ ] consolidate_v2.py     — consolidação final
7. [ ] Audit manual          — 10 PRs sanitizados, conferir manualmente
8. [ ] Execução              — `python3 scripts/bootstrap_v2.py`
9. [ ] Report + PDF          — step report com resultados
```

**Estimativa:** Scripts 1-6 em ~2h (Codex). Audit + execução + report em ~2h. Total: ~4h.

---

*Este documento é o contrato de execução. Qualquer desvio deve ser justificado e registrado aqui como addendum datado.*
