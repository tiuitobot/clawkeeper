# Stage 0.7 — Bootstrap Sequencial (Cold-Start)

**Data:** 2026-02-17  
**Modelo:** claude-haiku-4-5 (conforme decisão: testar pelo piso)  
**Custo total:** ~$0.20 (5 rounds × ~$0.04/round)

---

## Resumo

5 rounds de treinamento sequencial em amostra estratificada de 50 PRs.
Sistema nasce quente — não começa do zero em produção.

---

## Amostra (Stage 0.7.1)

**50 PRs estratificados** por:
- Outcome: 12 merged (24%), 38 closed (76%) — espelha merge rate real
- Size: XS×27, M×7, S×6, L×5, XL×5
- Category: other×31, docs×16, infra×1, bug×2
- Enrichment: 42/50 com comments/reviews/files

Script: `scripts/stratified_sample.py` (seed=42, reproduzível)

---

## Rounds

| Round | Modo | Accuracy | Descrição |
|-------|------|----------|-----------|
| R1 | Supervisionado | — | Viu outcomes. 277 signals, 8 surpresas |
| R2 | Predição cega | 96% (48/50) | Primeiro teste sem ver resultado |
| R3 | Predição cega | 94% (47/50) | Com contexto de R1+R2 |
| R4 | Predição cega | 94% (47/50) | Estável |
| R5 | Predição cega | 94% (47/50) | Consolidação |

**Plateau em 94%** a partir de R3 — sinal de que o modelo convergiu com os dados disponíveis. Os 3 erros persistentes (6%) provavelmente são PRs com outcomes governados por contexto não-textual (ex: PR superseded por outro que chegou primeiro, decisão arquitetural informal).

---

## Curva de Aprendizado

```
R1 (supervised): baseline
R2: 96% ←── maior salto (R1 context helps significantly)
R3: 94% ←── leve queda (mais sinais, mais nuance = harder cases)
R4: 94% ←── estável
R5: 94% ←── plateau confirmado
```

O plateau a 94% é esperado para features textuais puras. Ceiling break virá com:
1. Features numéricas (semanas_desde_abertura, volume_semanal) no logit formal
2. Dados de CI (não capturados ainda)
3. is_fork_pr (requer ingest adicional)

---

## Patterns Promovidos (13/15)

Sinais que apareceram em ≥8 PRs com precision ≥70% nas predições corretas:

| Signal | Freq | Precision |
|--------|------|-----------|
| maintainer | 47 | 94% |
| ci | 47 | 94% |
| comment | 47 | 94% |
| age | 46 | 94% |
| scope | 43 | 93% |
| review | 43 | 93% |
| closure | 38 | 95% |
| size | 36 | 92% |
| label | 32 | 91% |
| engagement | 31 | 90% |
| approval | 27 | 93% |
| contributor | 11 | 100% |
| superseded | 9 | 100% |

**Candidatos** (alta precision, baixa freq): `test` (100%), `bot` (100%)

---

## Pesos Logit Iniciais (binários)

| Feature | Peso | Direção |
|---------|------|---------|
| has_maintainer_label | +3.71 | MERGE |
| ci_green | +2.31 | MERGE |
| has_top_contributor_comment | +2.31 | MERGE |
| has_approval | +2.05 | MERGE |
| high_engagement | +1.68 | MERGE |
| touches_extensions | -1.61 | CLOSE |
| has_experienced_contributor_label | +1.18 | MERGE |
| has_tests | +1.13 | MERGE |
| is_draft | +1.13 | MERGE* |
| has_trusted_contributor_label | +1.13 | MERGE |

*`is_draft` com peso positivo é contra-intuitivo. Hipótese: draft PRs raros na amostra (57/3233 = 1.8%) e os que existem no sample podem ter sido mergeados após sair de draft. **VIF check obrigatório no Stage 1.**

---

## Artefatos

- `data/bootstrap_sample.jsonl` — 50 PRs estratificados
- `data/bootstrap/round_{1-5}_signals.jsonl` — análises por round
- `data/bootstrap/bootstrap_patterns.jsonl` — 15 patterns
- `data/bootstrap/initial_logit.json` — pesos iniciais
- `data/bootstrap/learning_curve.json` — curva R2→R5

---

## Próximo: Stage 1 — Core Pipeline

Stage 0.7 ✅. Sistema não nasce frio.

Stage 1 implementa o pipeline determinístico completo:
- `signal_extractor.py`: extrai as 33 features de PRs reais
- `logit_estimator.py`: scikit-learn logit em dataset completo (1521 enriquecidos)
- `quality_gate.py`: gates determinísticos pré-LLM
- CI rodando testes

Gate G1: accuracy no holdout ≥70% → prosseguir. <70% → avaliar Ministral 14B (Ministral teste).
