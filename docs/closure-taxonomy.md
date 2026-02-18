# Closure Taxonomy — Emergent from Bootstrap R1

*2026-02-17. Taxonomia emergente: Haiku classificou motivos de closure sem instrução.*
*Fonte: 50 PRs supervisionados, round 1 do bootstrap v1.*

---

## Taxonomia (7 categorias)

### 1. Governance/Hygiene
**Definição:** PR violou regras estruturais do repositório.
**Sinais determinísticos:** >N files changed, >N labels, CI fail, rebase failure.
**Exemplo:** PR #4509 — 100 files, 22 labels, maintainer: "branch is too noisy, recreate from clean branch."
**Detecção:** Quality gate mecânico. Custo zero.

### 2. Duplicate/Superseded
**Definição:** Outra PR resolve o mesmo problema. Mantém-se a canônica, fecham-se as demais.
**Sinais:** File overlap >60%, título similar, comment explícito ("closing as duplicate of #XXXX").
**Exemplo:** PR #6461 — conceito endossado pelo maintainer, mas #9595 é a PR canônica.
**Detecção:** Cross-PR comparison (file overlap + embedding semântico). Custo médio.
**Dados:** 519 pares explícitos, 384 clusters, 872 PRs (27% do dataset).

### 3. Architectural Veto
**Definição:** Código funcional e revisado positivamente, mas maintainer prefere abordagem arquitetural diferente.
**Sinais:** Review positivo + close. Comments com "vision", "prefer", "core vs extension", "plugin approach".
**Exemplos:**
- PR #10652 — elogiado, issues corrigidos, fechado: "prefer core integration over channel extension"
- PR #7358 — 157k eventos em produção, fechado: "prefer rclone over NATS JetStream"
- PR #18171 — redirected to plugin approach
**Detecção:** LLM mature model (intent analysis + vision document alignment). Custo alto.
**Nota:** Este é o **residual irredutível** — nenhuma feature quantitativa captura "a arquitetura não alinha com a visão do maintainer." Melhor candidato para active learning ("uncertain — needs human input").

### 4. Author Block
**Definição:** Autor com histórico de rejeição sistemática. PRs fechadas independente de conteúdo.
**Sinais:** author_prior_merge_rate < 5% com N >= 5 PRs.
**Dados:**
- 34 autores com 5+ PRs e 0% merge = 336 PRs (10.4% do dataset)
- 13 autores com 10+ PRs e 0% merge = 206 PRs (6.4%)
- Nota: shtse8 (94 PRs) tem 5.3% merge, não 0% — threshold importa
**Detecção:** Lookup determinístico. Custo zero.

### 5. Auto-Closure (Bot)
**Definição:** Bot de triage (CLAWDINATOR) fecha PR automaticamente por regras predefinidas.
**Sinais:** Comment de bot com template: "Closing as duplicate of #XXXX. If this is incorrect, comment and we can reopen."
**Detecção:** Lookup determinístico (commenter == bot). Custo zero.
**Nota:** Muitos auto-closures são dedupe (categoria 2). A distinção é o agente: bot vs maintainer.

### 6. Author-Initiated
**Definição:** Autor fecha próprio PR. Razões: preferiu fork, mudou abordagem, desistiu.
**Sinais:** closer == author, ou comment do autor indicando abandono.
**Detecção:** Parcialmente determinístico (closer == author), parcialmente LLM (intent do comment).
**Nota:** Menos previsível — depende de decisão unilateral do autor.

### 7. Stale/Obsolete
**Definição:** PR ultrapassada por mudanças no main. Changelog incompatível, conflitos de merge.
**Sinais:** pr_age_hours > threshold, merge conflicts, changelog drift.
**Exemplo:** PR #13270 — XS scope (1 file), fechado por contexto temporal de changelog.
**Detecção:** Feature temporal (pr_age_hours, weeks_since_open). Custo zero.

---

## Distribuição Estimada

| Categoria | % estimado dos closures | Detectável por |
|-----------|------------------------|----------------|
| Governance/hygiene | ~15% | Quality gate |
| Duplicate/superseded | ~16% | Dedupe engine |
| Architectural veto | ~10% | LLM only |
| Author block | ~10% | Lookup |
| Auto-closure (bot) | ~20% | Lookup |
| Author-initiated | ~10% | Parcial |
| Stale/obsolete | ~15% | Temporal features |
| **Desconhecido/misto** | **~4%** | — |

*Estimativas baseadas na amostra de 50 PRs. Validar contra dataset completo no Stage 1.*

---

## Implicação Arquitetural

A taxonomia define a **ordem do pipeline**:

```
PR chega
  │
  ├─ Quality Gate (governança, hygiene)     ← determinístico, custo $0
  ├─ Author Check (blocked, low-merge)      ← lookup, custo $0
  ├─ Bot Check (CLAWDINATOR auto-close)     ← lookup, custo $0
  ├─ Temporal Check (stale, obsolete)       ← feature, custo $0
  │
  ├─ Dedupe Engine (duplicate/superseded)   ← cross-PR, custo médio
  │
  ├─ Logit Model (P(merge) estimation)      ← scikit-learn, custo $0
  │     │
  │     ├─ High confidence → output direto
  │     └─ Low confidence → passa pro LLM
  │
  └─ LLM Mature Model                      ← Haiku/Sonnet, custo por PR
        ├─ Architectural veto detection
        ├─ Intent analysis
        └─ Active learning flag ("uncertain — needs human input")
```

**Princípio:** cada estágio filtra o que pode, passa o resto adiante. O LLM só vê os PRs que sobrevivem a todos os filtros determinísticos.

---

## Para o Paper

Esta taxonomia é evidência de que:
1. **LLMs podem servir como ferramentas de design de pipeline**, não apenas componentes
2. A maioria dos outcomes de PR (~65%) é previsível por features determinísticas de custo zero
3. O valor do LLM está concentrado em ~25% dos casos (architectural veto + edge cases)
4. A **recodificação progressiva** (LLM → logit → determinístico) é viável porque as categorias migram de LLM para regras à medida que patterns se estabilizam

---

*Documento de referência. Não editar retroativamente — adicionar addendums datados.*
