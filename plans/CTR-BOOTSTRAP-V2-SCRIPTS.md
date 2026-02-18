# CONTRATO DE DELEGAÇÃO: CTR-CLAWKEEPER-BSV2-SCRIPTS
**Tipo:** Implementação (coding)
**Modelo:** codex (thinking:low)
**Data:** 2026-02-17

---

## Contexto

Clawkeeper é um sistema de governança para PRs de projetos open-source. Prevê merge/close de PRs usando features extraídas + regressão logística + LLM. O Bootstrap v2 é a fase de calibração: Haiku analisa 1.000 PRs em 10 rounds para gerar dataset de treino limpo.

O plano completo está em `~/repos/clawkeeper/plans/bootstrap-v2-plan.md` (v2.1). Leia INTEGRALMENTE antes de codificar qualquer coisa.

## Problema

Implementar os 7 scripts Python do Bootstrap v2 conforme especificado no plano. Os scripts devem rodar de forma autônoma (sem intervenção humana) e produzir todos os artefatos listados na seção 10 do plano.

## Plano de Execução

1. Ler `~/repos/clawkeeper/plans/bootstrap-v2-plan.md` — INTEIRO. Este é o contrato de design.
2. Ler `~/repos/clawkeeper/model_spec.json` — features atuais (33). Adicionar `is_low_merge_author` como feature #34.
3. Ler `~/repos/clawkeeper/scripts/bootstrap_round.py` — referência do v1 (NÃO copiar lógica, só entender API calls / auth)
4. Ler `~/repos/clawkeeper/scripts/stratified_sample.py` — referência para estratificação
5. Ler `~/repos/clawkeeper/docs/dedupe-ground-truth.md` — clusters de dedupe, formato dos dados

Implementar na ordem:

### Script 1: `scripts/build_split.py`
- Carrega `data/all_historical_prs.json` e clusters de dedupe (extrair de `data/enriched_full.jsonl` com mesmo regex do doc)
- Split 70/30 com seed=42
- Cluster constraint: cluster NUNCA dividido entre partições
- Valida: merge rate ±3% entre partições, nenhum cluster dividido
- Output: `data/split.json` com `{seed, timestamp, train_prs: [...], holdout_prs: [...], train_clusters: [...], holdout_clusters: [...]}`

### Script 2: `scripts/sanitize.py`
- Recebe PR dict, retorna PR dict sanitizado
- Strip patterns conforme seção 6 do plano (STRIP_PATTERNS)
- Remove campos: `merged`, `merged_at`, `closed_at`, `state`
- Preserva: `created_at`, `title`, `user`, `labels`, `additions`, `deletions`, `changed_files`, `draft`, `comments` (sanitizados), `reviews`, `files`
- Função exportável: `sanitize_pr(pr: dict) -> dict`
- Inclui testes inline (`if __name__ == "__main__"`: testar com exemplos hardcoded)

### Script 3: `scripts/sample_v2.py`
- Carrega `data/split.json`
- Para cada round (1-10), amostra 100 PRs do pool de treino
- Injeção DIRIGIDA de dedupe: 6-8 clusters por round, membros do mesmo cluster no MESMO batch de 10
- Sem repetição entre rounds
- Preserva merge rate ~24% (±3)
- Output: `data/bootstrap_v2/round_{N}_sample.json` com `{prs: [...], clusters_used: [...], batch_assignments: {batch_idx: [pr_numbers]}}`
- Gera todos os 10 samples de uma vez (determinístico, seed=42)

### Script 4: `scripts/extract_patterns.py`
- Input: round scores (erros) + features dos PRs errados
- Chama Haiku com prompt: "Given these prediction errors, extract abstract patterns. Do NOT cite PR numbers or specific outcomes."
- Output: lista de pattern strings
- Função: `extract_patterns(errors: list[dict]) -> list[str]`

### Script 5: `scripts/bootstrap_v2.py` (ORQUESTRADOR)
- Carrega/valida split
- Para cada round 1-10:
  a. Carrega sample do round (de `round_{N}_sample.json`)
  b. Carrega PRs do `data/enriched_full.jsonl`, sanitiza com `sanitize.py`
  c. Se round >= 4: carrega patterns abstratos acumulados (R1 a R(N-1))
  d. Para cada batch (10 batches de 10 PRs):
     - Monta prompt (seção 7 do plano — fase baseline ou learning)
     - Chama Haiku (usar auth de `~/repos/clawkeeper/scripts/bootstrap_round.py` como referência — OAuth via auth-profiles.json)
     - Parseia JSON (com fallback para regex se parse falhar)
     - Sleep 1s entre batches
  e. Salva `round_{N}_results.jsonl`
  f. Chama `score_round.py`
  g. Se round >= 1: chama `extract_patterns.py` nos erros
  h. Salva `round_{N}_patterns.json`
  i. Log em `execution_log.txt` (timestamp, custo, erros)
- Ao final: chama `consolidate_v2.py`
- CLI: `python3 scripts/bootstrap_v2.py [--rounds 10] [--seed 42] [--start-round 1]`
- `--start-round` permite retomar se interrompido

### Script 6: `scripts/score_round.py`
- Input: `round_{N}_results.jsonl` + ground truth (do enriched_full.jsonl)
- Merge scoring: accuracy, precision, recall, F1, calibration (bins de 0.1)
- Dedupe scoring: precision, recall, F1 contra clusters reais no batch
- Erros persistentes: tracking cross-round
- Output: `round_{N}_scores.json`

### Script 7: `scripts/consolidate_v2.py`
- Carrega todos os 10 round scores
- Learning curve: accuracy por round, baseline (R1-3) vs learning (R4-10), delta com significância
- Patterns: quais sobrevivem 5+ rounds
- Logit weights iniciais: scikit-learn LogisticRegression nos features extraídos (todas as 1000 observações)
- Dedupe: F1 consolidado por round
- Erros persistentes: lista com categorização
- Output: `data/bootstrap_v2/consolidated.json`

### Atualização model_spec
- Adicionar feature #34 `is_low_merge_author` ao `model_spec.json`:
  ```json
  {"name": "is_low_merge_author", "type": "binary", "phase": "early", "notes": "Author with 5+ PRs and <5% merge rate. 34 authors, 336 PRs (10.4% of dataset). Deterministic lookup."}
  ```

## Arquivos de Referência
- `~/repos/clawkeeper/plans/bootstrap-v2-plan.md` — PLANO COMPLETO (ler primeiro)
- `~/repos/clawkeeper/model_spec.json` — feature spec
- `~/repos/clawkeeper/features/feature_map.json` — feature details
- `~/repos/clawkeeper/scripts/bootstrap_round.py` — v1 reference (auth, API calls)
- `~/repos/clawkeeper/scripts/stratified_sample.py` — v1 sampler reference
- `~/repos/clawkeeper/docs/dedupe-ground-truth.md` — dedupe clusters
- `~/repos/clawkeeper/docs/bootstrap-v1-findings.md` — context (do NOT use v1 data)
- `~/repos/clawkeeper/data/all_historical_prs.json` — full dataset
- `~/repos/clawkeeper/data/enriched_full.jsonl` — enriched with comments/reviews/files

## Expansão Permitida
Pode explorar `~/repos/clawkeeper/` livremente. NÃO ler arquivos fora deste repo. NÃO ler arquivos de memória do workspace.

## Output Esperado
- 7 scripts Python em `~/repos/clawkeeper/scripts/`
- `model_spec.json` atualizado com feature #34
- Cada script executável standalone (`python3 scripts/X.py --help`)
- Testes inline em cada script (`if __name__ == "__main__"`)
- NÃO fazer commit — orquestrador faz review antes

## Critérios de Aceitação
- [ ] AC1: `build_split.py` gera `data/split.json` com cluster constraint validado
- [ ] AC2: `sanitize.py` remove todos os STRIP_PATTERNS e campos proibidos
- [ ] AC3: `sample_v2.py` gera 10 samples com dedupe dirigido (clusters no mesmo batch)
- [ ] AC4: `bootstrap_v2.py` roda end-to-end com `--rounds 1 --start-round 1` sem erro
- [ ] AC5: `score_round.py` calcula accuracy + F1 + calibration corretamente
- [ ] AC6: `extract_patterns.py` gera patterns abstratos sem PR numbers
- [ ] AC7: `consolidate_v2.py` gera learning curve com baseline vs learning delta
- [ ] AC8: `model_spec.json` contém `is_low_merge_author` como feature #34
- [ ] AC9: Todos os scripts usam auth via `auth-profiles.json` (OAuth Bearer, NÃO x-api-key)
- [ ] AC10: Nenhum dado do v1 (signals, patterns, logit) é usado nos scripts

## Instruções de Execução
- Não narrar passos intermediários. Executar silenciosamente.
- Ler o plano INTEIRO antes de começar a codificar.
- Se o plano for ambíguo em algum ponto, escolher a interpretação mais conservadora.
- Testar cada script individualmente após criá-lo.
- NÃO executar o bootstrap completo (10 rounds) — só testar que roda sem erro.
- NÃO fazer git commit. Deixar staged para review.
