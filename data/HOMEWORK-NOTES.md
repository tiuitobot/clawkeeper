# Lição de Casa — Clawkeeper D-1

## Data Snapshot (2026-02-17)
- **300 PRs abertas** (4 draft)
- **500 PRs históricas** (194 merged, 306 closed = **39% merge rate**)
- **44 issues abertas**

## Sinais Determinísticos Já Visíveis

### Size → Merge Rate (FORTE)
| Size | Merge Rate | N |
|------|-----------|---|
| XS | 33% | 180 |
| S | **50%** | 139 |
| M | **55%** | 56 |
| L | **55%** | 38 |
| XL | **16%** | 62 |

**Insight:** Curva não-linear. XS e XL têm taxas baixas. Sweet spot é S-M-L. XS provavelmente são PRs triviais/incompletas. XL são too-big-to-review.

### trusted-contributor NÃO é preditor
- Trusted: 34% merge rate
- Non-trusted: 39% merge rate
- **Counter-intuitive.** Label não prediz merge. Bom sinal pro logit — variáveis óbvias nem sempre funcionam.

### Labels mais frequentes em PRs merged
- size: S (70), size: XS (61), agents (60), maintainer (31), size: M (31), gateway (25)

### Top merged authors
- arosstale (16), mbelinky (11), JayMishra-source (7), Clawborn (6), Operative-001 (6)
- Nota: Clawborn tem muitas PRs closed (não merged) — alto volume, taxa mista

### Observação sobre additions/deletions
- API de listagem NÃO retorna additions/deletions (vem null). Precisa de chamada individual `/pulls/{number}`.
- Size labels servem como proxy razoável pro logit inicial.

## Governance Docs
- **CLAUDE.md**: redireciona pra AGENTS.md
- **AGENTS.md**: 21KB — regras detalhadas de repo structure, modules, docs, i18n, VM ops
- **CONTRIBUTING.md**: 5KB — standard contribution guide

### Key Rules do AGENTS.md (relevantes pro Clawkeeper)
- 1000 LoC CI limit (PRs grandes bloqueadas)
- Colocated tests (`*.test.ts`)
- Plugin deps em package.json próprio
- Labels padronizados por channel/component
- Size labels automáticos (XS/S/M/L/XL)
- Trusted-contributor label existe mas não é gate

## Concorrência
- **openclawoverview.com (Claw Trends)**: site não renderiza conteúdo útil no fetch (SPA). Dashboard de clustering por similaridade semântica (Voyage AI). Passivo — não age.
- **Greptile**: code review LLM. Score por PR, sem memória cross-PR, sem learning.

## Issue #14165 (citada no plano)
- Auth refactoring sync — 8 PRs conflitantes sobre mesma área. Caso real de "which is the based." Closed.

## Dados Salvos
- `data/open_prs.json` — 300 PRs abertas
- `data/historical_prs.json` — 500 PRs closed/merged
- `data/open_issues.json` — 44 issues
- `data/AGENTS.md` — 21KB
- `data/CONTRIBUTING.md` — 5KB
