# Bootstrap v3 — Relatório Analítico Completo

**Gerado:** 2026-02-18 15:20 BRT  
**Modelo:** Claude Haiku 4.5 (thinking: low)  
**Pattern Analyst:** Claude Sonnet 4.5 (per-round, 4-field format)  
**Rounds:** 10 (R1-R3 baseline puro, R4-R10 com lifecycle-managed patterns)  
**Ground Truth:** Enriched (comments, reviews, files — 3233 PRs)  
**Custo estimado:** ~$8.50 (Haiku ~$0.80 + Sonnet ~$7.70)  
**Dataset:** 100 PRs/round (50 merge, 50 amostra estratificada)

---

## 1. Definição das Métricas

### 1.1 Merge Prediction (tarefa principal)

| Métrica | Definição | Por que importa |
|---------|-----------|-----------------|
| **Accuracy** | (TP+TN) / Total | Métrica geral, enviesada se classes desbalanceadas |
| **Precision** | TP / (TP+FP) | "Dos que eu disse merge, quantos realmente foram?" |
| **Recall** | TP / (TP+FN) | "Dos que foram merge, quantos eu peguei?" |
| **F1** | 2×P×R / (P+R) | Média harmônica — métrica principal |

### 1.2 Dedupe Detection (tarefa secundária)

| Métrica | Definição |
|---------|-----------|
| **Dedupe F1** | Harmônica de precision/recall de pares duplicatas |

### 1.3 Calibration
Confiança do modelo vs accuracy real, em bins de 10%.  
- **Overconfident:** avg_confidence > accuracy (diz que tem certeza mas erra)

### 1.4 Gates de Sucesso (declarados pré-execução, PLAN-v7 §4)

| Gate | Critério | Base (v2.1) | Propósito |
|------|----------|-------------|-----------|
| **G-ACC** | Mean accuracy R4-R10 > 74.6% | 74.6% (v2.1 learning avg) | Superar v2.1 |
| **G-REG** | R10 accuracy > 69.3% | 69.3% (v2.1 baseline avg) | Sem regressão terminal |
| **G-CAL** | R10 ≤ 1 bin overconfident | R10 v2.1: 4 bins over | Calibração estável |

---

## 2. Mudanças v2.1 → v3

| Aspecto | v2.1 | v3 |
|---------|------|-----|
| **Dados** | Metadata only (title, labels, author stats) | Enriched (comments, reviews, files, author history) |
| **Pattern format** | 1 campo (pattern text) | 4 campos (pattern/evidence/mechanism/anti_pattern) |
| **Pattern lifecycle** | Acumulação flat (sem pruning) | Stateful: active → revised → retired |
| **Pattern analyst** | Haiku (self-generated) | Sonnet per-round (external analyst) |
| **Pruning** | Nenhum | Automático: 2+ rounds de erro sem revisão eficaz |
| **Error attribution** | Não existia | Unambiguous/ambiguous por pattern |
| **Anti-patterns** | Não existia | Campo obrigatório (boundary conditions) |
| **Generalization** | Patterns citavam nomes, PRs | Diretriz explícita: sem nomes, sem números de PR |

---

## 3. Resultados Gerais

| Round | Fase | Accuracy | Precision | Recall | F1 | FP | FN | Cal Over |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| R1 | baseline | 81.0% | 56.1% | 95.8% | 0.708 | 18 | 1 | 1 |
| R2 | baseline | 82.0% | 59.4% | 79.2% | 0.679 | 13 | 5 | 2 |
| R3 | baseline | 91.0% | 75.9% | 91.7% | 0.830 | 7 | 2 | 1 |
| R4 | learning | 88.0% | 75.0% | 75.0% | 0.750 | 6 | 6 | 1 |
| R5 | learning | 88.0% | 76.0% | 79.2% | 0.760 | 7 | 5 | 1 |
| R6 | learning | 88.0% | 75.0% | 75.0% | 0.750 | 6 | 6 | 3 |
| R7 | learning | 81.0% | 55.6% | 62.5% | 0.627 | 11 | 8 | 3 |
| R8 | learning | 90.0% | 85.7% | 75.0% | 0.783 | 4 | 6 | 3 |
| R9 | learning | 91.0% | 95.0% | 79.2% | 0.791 | 2 | 7 | 1 |
| R10 | learning | 88.0% | 100.0% | 50.0% | 0.667 | 0 | 12 | 1 |

### 3.1 Médias por fase

| Métrica | Baseline (R1-3) | Learning (R4-10) | Delta | v2.1 Learning |
|---------|:---:|:---:|:---:|:---:|
| **Accuracy** | 84.7% | **87.7%** | +3.0pp | 74.6% |
| **Precision** | 63.8% | **80.3%** | +16.5pp | 47.8% |
| **Recall** | 88.9% | **70.8%** | -18.1pp | 65.5% |
| **F1** | 0.739 | **0.732** | -0.006 | 0.552 |

### 3.2 Gates

| Gate | Resultado | Valor | Target | Veredito |
|------|-----------|-------|--------|----------|
| **G-ACC** | 87.7% | > 74.6% | **✅ PASS** | +13.1pp acima do target |
| **G-REG** | 88.0% | > 69.3% | **✅ PASS** | +18.7pp acima do target |
| **G-CAL** | 1 bin | ≤ 1 | **✅ PASS** | Exatamente no limite |

### 3.3 Comparação v2.1 → v3

| Métrica | v2.1 (10 rounds) | v3 (10 rounds) | Delta |
|---------|:---:|:---:|:---:|
| Mean Accuracy | 73.0% | **85.2%** | **+12.2pp** |
| Mean F1 | 0.534 | **0.735** | **+0.201** |
| Total Errors | 270 | **132** | **-51%** |
| Total FP | 185 | **74** | **-60%** |
| Total FN | 85 | **58** | **-32%** |
| R10 Accuracy | 70.0% | **88.0%** | **+18.0pp** |
| R10 Cal Over | 4 bins | **1 bin** | Resolvido |

---

## 4. Análise de Erros (n=132)

### 4.1 Distribuição geral
- **FP (falsos positivos):** 74 (56%) — modelo diz merge mas não foi
- **FN (falsos negativos):** 58 (44%) — modelo diz no-merge mas foi merged

**v2.1 era 69% FP / 31% FN (sobre-otimista). v3 equilibrou para 56/44.** 

### 4.2 Evolução FP vs FN

| Tendência | R1 | R3 | R5 | R7 | R10 |
|-----------|:---:|:---:|:---:|:---:|:---:|
| **FP** | 18 | 7 | 7 | 11 | **0** |
| **FN** | 1 | 2 | 5 | 8 | **12** |

**FP eliminou completamente até R10 (0!)** — patterns anti-merge funcionaram. Mas **FN explodiu de 1 para 12** — overcorrection. Haiku ficou conservador demais, rejeitando PRs que deviam ser merged.

Este é o tradeoff central do v3: patterns de Sonnet são eficazes demais como anti-merge, sem contrapeso equivalente pró-merge.

### 4.3 Perfil dos erros

| | FP | FN |
|---|---|---|
| **Avg author merge rate** | 152% (inflated) | 7.5% |
| **First-time contributors** | 36/74 (49%) | 35/58 (60%) |
| **Top category** | agents (23) | agents (15) |

**FN típico:** first-time contributor com histórico baixo (~7.5% merge rate) que desta vez teve um PR aceito. O modelo descarta baseado no histórico, mas o PR era bom.

**FP típico:** autor com histórico inflado que desta vez foi rejeitado. Patterns anti-merge eliminaram a maioria ao longo dos rounds.

### 4.4 Erros por categoria

| Categoria | FP | FN | Total | Viés |
|-----------|:---:|:---:|:---:|:---:|
| agents | 23 | 15 | 38 | levemente otimista |
| docs | 5 | 9 | 14 | pessimista |
| commands | 6 | 7 | 13 | equilibrado |
| gateway | 11 | 0 | 11 | forte otimista |
| cli | 2 | 4 | 6 | pessimista |

**`agents` continua dominando** (29% dos erros), consistente com v2.1. `gateway` é 100% FP — modelo sempre prevê merge em gateway mas frequentemente erra.

---

## 5. Calibração

| Round | Bins | Overconfident | Underconfident |
|:---:|:---:|:---:|:---:|
| R1-R3 | 5 | 1-2 | 0 |
| R4-R6 | 5-6 | 1-3 | 0 |
| R7 | - | 3 | 0 |
| R8 | - | 3 | 0 |
| R9 | - | 1 | 0 |
| **R10** | **4** | **1** | **0** |

**R10 calibração detalhada:**

| Bin | N | Confidence | Accuracy | Status |
|:---:|:---:|:---:|:---:|:---:|
| 0.6 | 8 | 67.4% | 75.0% | ✅ underconfident |
| 0.7 | 17 | 74.8% | 88.2% | ✅ underconfident |
| 0.8 | 28 | 85.2% | 85.7% | ✅ calibrado |
| 0.9 | 47 | 95.6% | 91.5% | ⚠️ overconfident |

**v2.1 R10 tinha 4 bins overconfident. v3 R10 tem 1.** Gate G-CAL resolvido.

A calibração geral melhorou, mas os rounds intermediários (R6-R8) tiveram picos de overconfidence (3 bins). Padrão sugere que patterns novos causam overconfidence temporária até serem calibrados pelo modelo.

---

## 6. Dedupe

| Round | F1 | TP | FP | FN |
|:---:|:---:|:---:|:---:|:---:|
| R1 | 0.615 | 12 | 15 | 0 |
| R2 | 0.696 | 16 | 13 | 1 |
| R3 | 0.528 | 14 | 22 | 3 |
| R4 | 0.491 | 13 | 19 | 8 |
| R5 | **0.868** | 23 | 7 | 0 |
| R6 | 0.593 | 8 | 8 | 3 |
| R7 | 0.390 | 8 | 19 | 6 |
| R8 | 0.467 | 7 | 14 | 2 |
| R9 | 0.560 | 7 | 9 | 2 |
| R10 | 0.727 | 16 | 10 | 2 |

**Média:** 0.594 F1. **Alta variância** (0.390-0.868) — consistente com v2.1. Dedupe continua instável por sample pequeno.

---

## 7. Pattern Lifecycle

### 7.1 Números

| Status | Count | % |
|--------|:---:|:---:|
| **Active** | 35 | 73% |
| **Revised** | 3 | 6% |
| **Retired** | 10 | 21% |
| **Total** | 48 | 100% |

**10 patterns retired (21%)** — pruning automático funciona. Patterns que geraram erros em 2+ rounds sem revisão eficaz foram removidos.

### 7.2 Evolução

| Round | Patterns total | Active | Retired |
|:---:|:---:|:---:|:---:|
| R1 | 12 | 12 | 0 |
| R2 | 16 | 15 | 1 |
| R3 | 22 | 21 | 1 |
| R4 | 24 | 20 | 4 |
| R5 | 28 | 24 | 4 |
| R6 | 38 | 28 | 5 |
| R7 | 39 | 28 | 8 |
| R8 | 43 | 34 | 6 |
| R9 | 48 | 38 | 7 |
| R10 | 48 | 35 | 10 |

**v2.1 não tinha lifecycle** — patterns acumulavam sem controle, contribuindo para R10 regression (70% accuracy). v3 manteve R10 em 88% com pruning ativo.

---

## 8. Diagnóstico

### 8.1 Problema principal: overcorrection (FP→FN shift)

O v3 resolveu o viés FP do v2.1 (185→74, -60%) mas criou um problema novo: **FN crescente** (1→12 no R10). Haiku ficou conservador demais.

**Causa raiz:** patterns anti-merge são eficazes e específicos ("first-time contributor without maintainer engagement → no merge"). Mas faltam patterns pró-merge equivalentes ("PR with specific positive signals → likely merge despite low author history").

**R3 (91% accuracy, FP=7, FN=2) foi o ponto ótimo** — antes que padrões demais acumulassem.

### 8.2 Pós-treino CLT como mitigação

O pós-treino desenhado (PLAN-v7) endereça exatamente este problema:
- Patterns com baixa evidência (n=1-2 rounds de match) recebem confidence < 0.3 → instrução "tiebreaker only"
- Haiku não aplica patterns fracos com mesma força que patterns fortes
- Esperado: reduzir overcorrection em R4-R10 sem perder o ganho de FP

### 8.3 Features ausentes que causam FN

60% dos FNs são first-time contributors. O modelo não tem como saber que "este first-timer é bom" porque faltam:
- **Body da PR** (descrição qualitativa)
- **Greptile confidence score** (avaliação de código)
- **Issue linkada self-filed** (ownership)
- **Review comments qualitativos** (o que os reviewers dizem)

O enrichment v2 (em andamento) adiciona body + issue features. Próximo bootstrap (v3.1) testará se esses dados reduzem FN.

### 8.4 Dedupe instável

F1 varia 0.390-0.868. Sample de 100 PRs gera poucos pares reais. Não é prioridade — merge prediction é a tarefa principal.

---

## 9. Conclusão

Bootstrap v3 é um salto significativo sobre v2.1:
- **Accuracy +12.2pp** (73.0% → 85.2% média)
- **Erros -51%** (270 → 132)
- **FP -60%** (185 → 74)
- **Calibração resolvida** (4 bins over → 1)
- **Sem regressão terminal** (R10=88% vs v2.1 R10=70%)
- **Lifecycle funciona** (10 patterns retired, 3 revised)

O problema residual é **overcorrection** (FN=12 no R10). Duas mitigações planejadas:
1. **Pós-treino CLT** — calibrar confidence por √n, reduzir peso de patterns fracos
2. **Enrichment v2** — body, issues, account metadata para features mais ricas

Os dados do v3 alimentam o **Stage 1 logit** — patterns confirmados viram features, coeficientes substituem heurísticas.
