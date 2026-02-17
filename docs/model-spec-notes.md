# Model Specification Notes — Stage 0.5

*2026-02-17. LLM acting as econometrician: read governance docs + data, proposed features.*

## Sources analyzed

1. **AGENTS.md** (21KB) — project structure, coding conventions, CI requirements, multi-agent safety, extension rules
2. **CONTRIBUTING.md** (5KB) — maintainer list (10 people), contribution workflow, AI PR policy
3. **all_historical_prs.json** — 3233 PRs, keys: number/title/state/merged_at/user/labels/created_at/additions/deletions/changed_files/draft/requested_reviewers/milestone
4. **enriched_full.jsonl** — 3233 PRs with comments/reviews/files detail
5. **D-01v3 enrichment analysis** — statistical findings from Stage 0

## Key governance insights for feature selection

### From AGENTS.md
- **Files under ~700 LoC** (guideline, not hard) → large diffs = signal
- **Colocated tests (*.test.ts)** mandatory → `has_tests` is viable
- **CI pipeline:** `pnpm build && pnpm check && pnpm test` → `ci_green` extractable
- **Multi-channel awareness:** "consider ALL channels when refactoring shared logic" → cross-channel PRs are risky → `touches_multiple_channels`
- **Extensions have own package.json**, specific dependency rules → `touches_extensions` as distinct signal
- **PR template exists** (.github/pull_request_template.md) → compliance could be feature in v2
- **Multi-agent safety rules** → PRs that touch git stash/worktree/branch logic may get scrutiny

### From CONTRIBUTING.md
- **Maintainer list explicit:** 10 people with named domains (steipete=general, Shadow=Discord, etc.)
- **AI PRs welcome** but must be marked → could extract `is_ai_pr` from body in v2
- **Current focus:** Stability, UX, Skills, Performance → alignment with focus areas may matter
- **steipete = "Benevolent Dictator"** → confirms why his comment is strongest signal

### From data
- **Label system is rich:** functional (agents/docs/gateway/cli/commands) + component (channel:*/app:*) + contributor status (maintainer/trusted/experienced) + size (XS-XL)
- **`maintainer` label = 90.7% merge rate** (183 PRs) — strongest individual signal found
- **`trusted-contributor` (96 PRs)** and **`experienced-contributor` (99 PRs)** — likely strong but not yet measured against merge
- **`requested_reviewers` almost never used** (5 PRs) → excluded
- **1318 unique authors, heavy long tail** → author history is sparse for most contributors
- **57 draft PRs** → `is_draft` available but low frequency

## Repo-specific features (beyond universals)

| Feature | Source | Rationale |
|---------|--------|-----------|
| `has_maintainer_label` | labels | 90.7% merge. Strongest label signal in dataset |
| `has_trusted_contributor_label` | labels | GitHub contributor status |
| `has_experienced_contributor_label` | labels | GitHub contributor status |
| `author_association` | enriched (comments authorAssociation) | MEMBER/CONTRIBUTOR/NONE from GitHub |
| `touches_multiple_channels` | labels + file paths | AGENTS.md explicitly warns about cross-channel refactoring |
| `touches_extensions` | file paths | Different dependency/packaging rules per AGENTS.md |
| `is_fork_pr` | PR metadata | Fork PRs often low quality (PR#279 = 39k additions from wrong branch) |
| `is_draft` | PR metadata | Signal of "not ready" |

## Excluded features (with reason)

| Feature | Reason |
|---------|--------|
| `requested_reviewers` | 5 PRs only. Zero discriminative power |
| `milestone` | Not explored. Deferred to v2 |
| `is_ai_generated` | Would need NLP on PR body. Compliance unknown. v2 |
| `pr_template_compliance` | Would need NLP comparison with template. v2 |
| `focus_area_alignment` | Would need NLP mapping of PR to "current focus" areas. v2 |

## Early vs Mature model split

- **Early:** features available at PR creation (no interaction needed)
  - 18 features: size, tests, CI, category, component, author history, labels, temporal controls
- **Mature:** adds interaction signals
  - 33 features: all early + comments, reviews, maintainer/contributor, greptile, engagement, age

**Rationale for split:** steipete's comment appears late in PR lifecycle. Including it in early model = look-ahead bias. The confidence interval narrows as PR matures.

## Viability check

| Feature | Data source | Missing % | Leakage risk | Status |
|---------|-------------|-----------|--------------|--------|
| loc_additions/deletions | all_historical_prs | 0% | none | ✅ keep |
| files_changed | all_historical_prs | 0% | none | ✅ keep |
| size_label | labels | ~60% unlabeled | none | ✅ keep (missing = feature) |
| has_tests | enriched (files) | ~53% not enriched | none | ✅ keep (enriched subset) |
| ci_green | needs GitHub checks API | not in current data | none | ⚠️ add to ingest |
| category | labels | ~30% no functional label | none | ✅ keep (deterministic + LLM fallback) |
| author_prior_prs | computable from data | 0% | time-leak if not careful | ✅ keep (use only prior PRs) |
| has_maintainer_label | labels | 0% | none | ✅ keep |
| weeks_since_open | created_at | 0% | none | ✅ keep (critical) |
| comment_count | enriched | ~53% | none | ✅ keep (mature only) |
| has_maintainer_comment | enriched | ~53% | timing | ✅ keep (mature only) |
| has_greptile_review | enriched | ~53% | none | ✅ keep (control only) |
| touches_multiple_channels | labels + files | 0% (labels) / ~53% (files) | none | ✅ keep |
| is_fork_pr | needs GitHub API | not in current data | none | ⚠️ add to ingest |

### Data gaps to address in Stage 1
1. `ci_green` — not in current dataset, needs GitHub checks API
2. `is_fork_pr` — not in current dataset, needs PR head/base repo comparison
3. Enrichment coverage is 100% coverage (3233/3233) — full coverage confirmed
