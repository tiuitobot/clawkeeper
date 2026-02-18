# Ideas

## Clawkeeper como módulo de QA epistêmico do Tiuito (2026-02-18)

**Insight (via auditor):** Compliance sweeper = enforcement horizontal (comportamento segue padrão). Falta enforcement vertical: "o padrão merece existir?"

**Arquitetura:**
- Roda periodicamente (acoplado ao DREAM_CYCLE ou trigger por volume de patterns promoted)
- Lê promoted patterns + signals recentes
- Classifica: sustentado por evidência / inflado / ruído / contraditório
- Output alimenta DREAM_CYCLE com recomendações de prune/revise/promote

**Pipeline completo:**
```
Signals → Patterns → [SWEEPER: compliance] → Rules
                ↑
        [CLAWKEEPER: qualidade]
        "este pattern merece existir?"
```

**Separação epistêmica:** Modelo diferente do que produziu os patterns. No Tiuito: Opus produz patterns → Sonnet/Haiku audita. No Clawkeeper: Sonnet extrai → Opus pode auditar.

**Produto:** "Quality assurance do learning" — módulo embutido, não serviço de auditoria. API: patterns + signals → classificação de qualidade + recomendações de lifecycle. Tiuito = primeiro cliente (validação N=1). Depois oferece como módulo OpenClaw.

**Prova empírica:** Bootstrap v3 — 40 patterns proliferaram pra ~8 variáveis, overcorrection monotônica (FN 1→12). Módulo teria flaggado em R4.

**Status:** Ideia. Foco atual: terminar bootstrap v4 + Stage 1 primeiro.
