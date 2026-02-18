# Dedupe Ground Truth — Extraction & Statistics

*2026-02-17. Dataset de pares/clusters de duplicação extraído dos comments históricos.*

---

## Método de Extração

**Fonte:** `data/enriched_full.jsonl` (3.233 PRs com comments completos)

**Regex aplicado nos comments de PRs fechadas (não merged):**
```
(?:superseded|duplicate|replaced|favor|instead|closing in favor|
superceded|dupe|dup of|same as|covered by|addressed in|fixed in|
resolved in|merged in)\s*(?:by|of|in)?\s*#(\d+)
```

**Resultado:** 519 pares explícitos (closed_pr → winner_pr)

**Agrupamento:** Union-find para construir clusters transitivos.
- Se A → B e C → B, então {A, B, C} formam um cluster.

---

## Estatísticas

| Métrica | Valor |
|---------|-------|
| Total de PRs envolvidos em dedupe | 872 (27% de 3.233) |
| Total de clusters | 384 |
| Pares explícitos (edges) | 519 |

### Distribuição de tamanho dos clusters

| Tamanho | Clusters | PRs envolvidos |
|---------|----------|----------------|
| 2 (pares) | 321 | 642 |
| 3 (trios) | 46 | 138 |
| 4 (quartetos) | 11 | 44 |
| 5 | 1 | 5 |
| 6 | 3 | 18 |
| 9 | 1 | 9 |
| 16 | 1 | 16 |
| **Total** | **384** | **872** |

### Clusters maiores (≥5 PRs)

| Cluster | PRs | Provável causa |
|---------|-----|---------------|
| n=16 | #3407, #6782, #7337, #7431, #7921, #8087, #9441, #10111, ... | Bug/feature popular, múltiplas tentativas |
| n=9 | #3760, #4798, #6699, #6771, #7311, #7355, #7425, #10290, ... | Idem |
| n=6 | #14371, #17284, #17333, #17427, #17428, #17429 | Burst de PRs similares (temporalmente próximas) |
| n=6 | #14232, #14379, #14424, #14465, #14778, #14786 | Idem |
| n=6 | #12916, #12937, #13177, #13179, #13181, #13201 | Idem |
| n=5 | #7567, #10620, #10760, #12726, #12988 | Idem |

---

## Split para Bootstrap v2

**Regra:** split por cluster (nunca dividir um cluster entre treino e holdout).

| Partition | Clusters | PRs (estimado) | Uso |
|-----------|----------|----------------|-----|
| **Treino (70%)** | ~269 | ~610 | Bootstrap v2, logit training |
| **Holdout (30%)** | ~115 | ~262 | Teste final, nunca tocado |

**Seed:** fixo, registrado no commit do split. Determinístico e reproduzível.

**Nota:** O split de dedupe deve ser compatível com o split geral do dataset (70/30 dos 3.233 PRs). PRs que não estão em nenhum cluster de dedupe são splittados independentemente.

---

## Critérios de Dedupe para o Modelo

O modelo no bootstrap v2 deve identificar duplicados **sem** ver a informação de closure por dedupe. Critérios disponíveis:

| Critério | Tipo | Disponível nos dados? |
|----------|------|----------------------|
| File overlap (>60% interseção) | Determinístico | ✅ (enriched: files) |
| Title similarity | Semântico | ✅ (title) |
| Component overlap (mesmas labels funcionais) | Determinístico | ✅ (labels) |
| Temporal proximity (<7 dias entre criação) | Determinístico | ✅ (created_at) |
| Intent overlap (mesmo problema, arquivos diferentes) | Semântico (LLM) | ✅ (title + comments sanitizados) |

### Sanitização obrigatória

Antes de apresentar PRs ao modelo para detecção de dedupe, **remover:**
- Comments contendo "duplicate", "superseded", "closing in favor of"
- Bot messages de triage (CLAWDINATOR templates)
- Qualquer referência cruzada a PR "vencedora" (#XXXX)
- Status de merge/close

---

## Métricas de Avaliação (Bootstrap v2)

| Métrica | Definição |
|---------|-----------|
| **Precision** | Dos pares que o modelo identificou como duplicados, quantos são reais? |
| **Recall** | Dos pares reais de duplicados, quantos o modelo identificou? |
| **F1** | Harmonic mean de precision e recall |
| **Cluster accuracy** | O modelo agrupou corretamente em clusters (não só pares)? |

**Target (paper):** F1 > 0.7 para pares, cluster accuracy > 0.6

---

*Documento de referência. Não editar retroativamente — adicionar addendums datados.*
