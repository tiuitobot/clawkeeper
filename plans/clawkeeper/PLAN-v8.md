# Plano: Clawkeeper — Governance Engine para OpenClaw PRs

*v8 — 2026-02-18. Incorpora: Bootstrap v4 redesign (prompt separation deterministic/qualitative, Sonnet consolidation by latent variable, Haiku 3-task separation, Greptile as semantic feature, CLT inline, population filter).*

*Changelog v7→v8: Prompt Sonnet redesenhado (consolidação por variável latente, investigação causal de bimodais, strength inline, kind field). Prompt Haiku redesenhado (3 tasks: feature extraction + qualitative judgment + dedupe via Greptile). Population filtrada para PRs com Greptile review completo (~76% do dataset, ~2.400 PRs). CLT integrado ao run (não pós-treino separado). Enrichment v2 antes do bootstrap. Chamado v4 (não v3.1) — mudança arquitetural, não paramétrica.*

---

## 0) Contexto Estratégico

*(Mantido de v7 — ver PLAN-v7.md)*

**Diferencial Clawkeeper:** governance engine com pipeline completo + modelo logit calibrado por dados históricos + learning loop duplo + recodificação progressiva. Local-first, custo zero.

**Distinção:** Tiuito = agente autônomo (Opus). Clawkeeper = pipeline mecânico (Haiku/Sonnet). Sem meta-cognição, journals, auto-expressão.

---

## 1) O que o Bootstrap v3 Demonstrou

### 1.1 Resultados

| Métrica | v2.1 Baseline | v2.1 Learning | v3 Baseline (R1-R3) | v3 Learning (R4-R10) |
|---------|:---:|:---:|:---:|:---:|
| Accuracy | 69.3% | 74.6% | 84.7% | 87.7% |
| F1 | 49.4% | 55.2% | 73.9% | 73.2% |
| FP (mean/round) | — | — | 12.7 | 5.1 |
| FN (mean/round) | — | — | 2.7 | 7.1 |

### 1.2 Diagnóstico (via Auditor)

1. **Enrichment > Patterns**: +15pp accuracy vem dos dados enriquecidos (comments/reviews/files), não dos patterns. R3 sem patterns = 91%/0.830 F1 — melhor round do bootstrap inteiro.
2. **F1 learning < F1 baseline** (0.732 < 0.739): Patterns trocaram FP por FN. Net effect no F1: zero ou negativo.
3. **Overcorrection monotônica**: FN cresce 1→2→5→6→5→6→8→6→7→12. Cada round empurra Haiku pro conservadorismo.
4. **40 patterns → 8 variáveis**: Sonnet proliferou variantes em vez de consolidar. 6 clusters determinísticos + 6 edge cases qualitativos.
5. **Bot contamination**: ~15-30% dos closes podem ser bots automáticos. Ground truth contaminado distorce calibração.
6. **submission_mechanism ausente**: Feature mais importante que não existe.

### 1.3 Reframe

O pipeline v3 **especifica**, não **aprende**. O valor está na identificação de variáveis pro logit, não na melhoria iterativa de predição. O framing correto: "enriched features + LLM baseline produziu 85% accuracy. Pattern extraction identificou variáveis qualitativas que colapsam em features pro logit."

---

## 2) Mudanças v3 → v4

### 2.1 Prompt Sonnet — Consolidação por Variável Latente

**Problema v3:** Sonnet gerava 1 pattern por erro, convergindo pra regras determinísticas (if/else). 40 patterns = 8 variáveis reais.

**Solução v4:**
- **Consolidação obrigatória**: cada pattern = 1 variável latente. Se dois patterns colapsariam na mesma feature logit, são o mesmo pattern.
- **Separação kind**: `deterministic` (mecanicamente verificável) vs `qualitative` (requer julgamento).
- **Investigação causal de bimodais**: quando feature numérica tem distribuição bimodal (e.g., merge_rate 0% vs >20%), investigar causa da separação (bot vs humano).
- **Strength inline**: `deterministic/strong/heuristic` — substitui CLT pós-treino.
- **consolidation_notes** no output: explicar merges de patterns.

### 2.2 Prompt Haiku — 3 Tasks Separadas

**Problema v3:** merge prediction + dedupe + feature extraction misturados. Patterns determinísticos injetados como "judgment hints."

**Solução v4:**
- **Task A — Feature Extraction (Deterministic)**: Extrair features binárias/numéricas mecanicamente. Lista fixa. Sem julgamento.
- **Task B — Merge Prediction (Qualitative)**: Predição baseada em julgamento que features não capturam. Só patterns qualitativos injetados.
- **Task C — Dedupe Detection**: Comparar PRs usando Greptile review summary como representação semântica.

### 2.3 Population Filter

- **Critério**: PRs com Greptile review completo no body.
- **Estimativa**: ~76% do dataset (~2.400 de 3.233 PRs).
- **Merge rate preservada**: 26.3% no subset (vs 24% geral).
- **Implementação**: Enrichment v2 puxa body para todos, depois filtra quem tem Greptile.

### 2.4 CLT Inline

Strength classification pelo Sonnet dentro do pattern extraction (não pós-treino separado).
Formula mantida: `confidence = strength_bucket × (1 - 1/√(n+1))`.
Buckets: Deterministic (0.95), Strong (0.75), Heuristic (0.50).

---

## 3) Pipeline v4

### 3.1 Sequência de Execução

```
1. Enrichment v2 (todos os 3.233 PRs)
   → Adiciona: body, issue features, author velocity/spread, account metadata
   → Output: data/all_historical_prs_enriched_v2.json

2. Population filter
   → Critério: body contém "greptile" (case-insensitive)
   → Output: data/bootstrap_v4/population.json (~2.400 PRs)

3. Sampling
   → 10 rounds × 100 PRs, stratified by merge rate
   → Seed fixa para reprodutibilidade

4. Bootstrap v4 (10 rounds)
   → R1-R3: baseline (Haiku sem patterns, com features determinísticas)
   → R4-R10: learning (Haiku com patterns qualitativos do Sonnet)
   → Sonnet roda após cada round R4+ para pattern extraction
   → Monitoramento a cada 10min

5. Análise
   → Comparar v4 vs v3 nas mesmas métricas
   → Avaliar se patterns qualitativos melhoram F1 (não só accuracy)
   → Medir consolidação (target: ≤15 patterns ativos, não 40)
```

### 3.2 Scripts

| Script | Função | Status |
|--------|--------|--------|
| `scripts/enrichment_v2.py` | Enriquecer PRs via GitHub API | ✅ Existe (347 linhas), nunca executado |
| `scripts/bootstrap_v4.py` | Orquestrador bootstrap | 🔨 Novo (baseado em v3, prompt redesenhado) |
| `scripts/extract_patterns_v4.py` | Pattern extraction Sonnet | 🔨 Novo (consolidação, kind, strength) |
| `scripts/filter_population.py` | Filtrar PRs com Greptile | 🔨 Novo (simples) |

### 3.3 Feature Matrix (Stage 1 Logit Target)

Determinísticas (do bootstrap + auditor):
| Feature | Tipo | Origem |
|---------|------|--------|
| `has_merge_receipt` | bool | comments/reviews |
| `has_closure_signal` | bool | comments (duplicate/superseded) |
| `has_revert_signal` | bool | comments (accidental merge) |
| `has_human_review` | bool | reviews |
| `human_review_type` | categorical | reviews (maintainer/contributor/none) |
| `author_merge_rate` | continuous | author history |
| `author_prior_prs` | continuous (log) | author history |
| `is_triage_rejected` | bool | files_changed (0 or 270+) |
| `submission_mechanism` | categorical | velocity/spread (human/bot/semi) |
| `category_merge_rate` | continuous | PR category (agent/docs/core/etc) |
| `greptile_score` | ordinal (0-5) | body |
| `has_linked_issue` | bool | body/refs |
| `issue_is_self_filed` | bool | issue author = PR author |

---

## 4) Gates de Sucesso v4

| Gate | Critério | Justificativa |
|------|----------|---------------|
| G-ACC | Mean R4-R10 accuracy > 87.7% (v3) | Improvement over v3 |
| G-F1 | Mean R4-R10 F1 > 0.739 (v3 baseline) | Patterns must improve F1, not just accuracy |
| G-REG | R10 accuracy > 88% (v3 R10) | No late regression |
| G-FN | R10 FN ≤ 6 (half of v3's 12) | Overcorrection resolved |
| G-CON | Active patterns ≤ 15 | Consolidation working |

**Novo gate G-F1**: v3 falhou aqui (learning F1 < baseline F1). V4 deve resolver.
**Novo gate G-FN**: overcorrection era o problema principal de v3.
**Novo gate G-CON**: consolidação por variável latente deve produzir poucos patterns.

---

## 5) Riscos

| Risco | Mitigação |
|-------|-----------|
| Sonnet com prompt qualitativo converge pra heurísticas disfarçadas | Revisar patterns após R4 — se 80%+ são determinísticos, parar e ajustar |
| Reviews no dataset são superficiais ("LGTM") → sem substrato qualitativo | Greptile summary como proxy. Se insuficiente, qualitative patterns terão baixo support |
| Enrichment v2 rate-limited pelo GitHub | GraphQL batched, retry com backoff, ~5k requests budget |
| Process morre durante Sonnet API call (P089) | setsid desde o início, monitor 10min, recovery manual documentado |
| Population filter muito restritivo | 76% é saudável. Se cair pra <60%, relaxar critério |

---

## 6) Decisões Pendentes

- [ ] Dedupe pipeline dedicado (pós-v4, quando tiver massa crítica)
- [ ] Bot decontamination do ground truth (pós-enrichment, quando tiver velocity/spread)
- [ ] Stage 1 logit estimation (pós-v4, com feature matrix completa)

---

*Próximo passo: Implementar scripts v4 (bootstrap_v4.py + extract_patterns_v4.py + filter_population.py), rodar enrichment v2, executar bootstrap v4.*
