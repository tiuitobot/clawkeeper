#!/usr/bin/env python3
"""Extract lifecycle-managed learning patterns v4 — consolidation by latent variable.

Changes from v3:
- Prompt redesigned: each pattern = 1 latent variable, merge if same logit feature
- New fields: kind (deterministic|qualitative), strength (deterministic|strong|heuristic)
- CLT inline: confidence = strength_bucket × (1 - 1/√(n+1))
- Consolidation enforcement: warn if >15 active patterns
- Output includes consolidation_notes
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "claude-sonnet-4-5"
PRUNING_THRESHOLD_DEFAULT = 2

STRENGTH_BUCKETS = {
    "deterministic": 0.95,
    "strong": 0.75,
    "heuristic": 0.50,
}

REQUIRED_PATTERN_FIELDS = [
    "id",
    "pattern",
    "evidence",
    "mechanism",
    "anti_pattern",
    "confidence",
    "support",
    "status",
    "since_round",
    "last_validated",
    "attributions",
    "kind",
    "strength",
]

SPECIFIC_LIB_PATTERNS = [
    r"\breact\b",
    r"\bvue\b",
    r"\bangular\b",
    r"\bnext\.js\b",
    r"\bexpress\b",
    r"\bdjango\b",
    r"\bflask\b",
    r"\bfastapi\b",
    r"\bnumpy\b",
    r"\bpandas\b",
    r"\btensorflow\b",
    r"\bpytorch\b",
    r"\bgrammy\b",
    r"\btelegraf\b",
    r"\bdiscord\.js\b",
]


def _token_from_profiles() -> tuple[str, str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key, "api_key"
    auth_file = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_file.exists():
        profiles = json.load(auth_file.open())
        preferred = os.environ.get("ANTHROPIC_PROFILE")
        profile_order = ["anthropic:eva-new", "anthropic:bruno-new", "anthropic:openclaw"]
        if preferred:
            profile_order = [preferred] + [p for p in profile_order if p != preferred]
        for profile_name in profile_order:
            p = profiles.get("profiles", {}).get(profile_name, {})
            token = p.get("token") or p.get("access")
            if token:
                return token, "oauth"
    raise RuntimeError("No Anthropic token found")


def call_sonnet(prompt: str, max_tokens: int = 8192) -> str:
    token, auth_type = _token_from_profiles()
    payload = {
        "model": MODEL_ID,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if auth_type == "oauth":
        headers["Authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        headers["x-api-key"] = token

    for attempt in range(3):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                print(f"extract_patterns_v4: rate limited, sleeping 30s (attempt {attempt+1}/3)")
                time.sleep(30)
                continue
            raise
    raise RuntimeError("extract_patterns_v4: call_sonnet failed after 3 attempts")


def format_pr_for_prompt(pr: dict) -> str:
    labels = ", ".join(pr.get("labels", [])) or "none"
    author = pr.get("user", "unknown")
    body = (pr.get("body") or "")[:500]

    text = f"""## PR #{pr.get('number')}: {pr.get('title', '')}

- **Author:** {author}
- **Created:** {pr.get('created_at', '')}
- **Labels:** {labels}
- **Size:** +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} ({pr.get('changed_files', pr.get('changedFiles', 0))} files)
- **Draft:** {pr.get('draft', False)}
- **Body (truncated):** {body}
"""

    comments = pr.get("comments", []) or []
    if comments:
        text += f"\n### Comments ({len(comments)}):\n"
        for c in comments[:10]:
            if not isinstance(c, dict):
                continue
            user = c.get("author", {}).get("login") if isinstance(c.get("author"), dict) else c.get("user")
            user = user or "?"
            assoc = c.get("authorAssociation") or c.get("author_association") or ""
            cbody = (c.get("body", "") or "").replace("\n", " ")[:300]
            text += f"- **{user}** ({assoc}): {cbody}\n"

    reviews = pr.get("reviews", []) or []
    if reviews:
        text += f"\n### Reviews ({len(reviews)}):\n"
        for r in reviews[:10]:
            if not isinstance(r, dict):
                continue
            user = r.get("author", {}).get("login") if isinstance(r.get("author"), dict) else r.get("user")
            user = user or "?"
            state = r.get("state", "?")
            rbody = (r.get("body", "") or "").replace("\n", " ")[:300]
            text += f"- **{user}**: {state} — {rbody}\n"

    files = pr.get("files", []) or []
    if files:
        text += f"\n### Files changed ({len(files)}):\n"
        for f in files[:25]:
            if isinstance(f, dict):
                path = f.get("path") or f.get("filename") or "?"
            else:
                path = str(f)
            text += f"- {path}\n"

    return text


def default_state() -> Dict[str, Any]:
    return {
        "version": 4,
        "patterns": [],
        "pruning_threshold": PRUNING_THRESHOLD_DEFAULT,
        "last_round": 0,
    }


def clt_confidence(strength: str, rounds_with_match: int) -> float:
    """CLT inline: confidence = strength_bucket × (1 - 1/√(n+1))."""
    bucket = STRENGTH_BUCKETS.get(strength, 0.50)
    n = max(0, rounds_with_match)
    return bucket * (1.0 - 1.0 / math.sqrt(n + 1))


def normalize_pattern(raw: Dict[str, Any], round_num: int, fallback_id: str) -> Dict[str, Any]:
    p = {k: raw.get(k) for k in REQUIRED_PATTERN_FIELDS}
    p["id"] = str(p.get("id") or fallback_id)
    p["pattern"] = str(p.get("pattern") or "").strip()
    p["evidence"] = str(p.get("evidence") or "").strip()
    p["mechanism"] = str(p.get("mechanism") or "").strip()
    p["anti_pattern"] = str(p.get("anti_pattern") or "").strip()

    # kind: deterministic or qualitative
    kind = str(p.get("kind") or "qualitative").strip().lower()
    p["kind"] = kind if kind in {"deterministic", "qualitative"} else "qualitative"

    # strength: deterministic, strong, heuristic
    strength = str(p.get("strength") or "heuristic").strip().lower()
    p["strength"] = strength if strength in {"deterministic", "strong", "heuristic"} else "heuristic"

    # Compute confidence from CLT
    attrs = p.get("attributions", [])
    attrs = attrs if isinstance(attrs, list) else []
    rounds_with_match = len({
        int(a.get("round", 0)) for a in attrs
        if isinstance(a, dict) and not a.get("ambiguous")
    })
    p["confidence"] = round(clt_confidence(p["strength"], rounds_with_match), 4)

    try:
        p["support"] = max(0, int(p.get("support", 0)))
    except Exception:
        p["support"] = 0

    status = str(p.get("status") or "active").strip().lower()
    p["status"] = status if status in {"active", "revised", "retired"} else "active"

    try:
        p["since_round"] = int(p.get("since_round", round_num))
    except Exception:
        p["since_round"] = round_num

    try:
        p["last_validated"] = int(p.get("last_validated", round_num))
    except Exception:
        p["last_validated"] = round_num

    p["attributions"] = attrs
    return p


def load_patterns_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        state = default_state()
        json.dump(state, path.open("w"), indent=2)
        return state

    state = json.load(path.open())
    out = default_state()
    out["version"] = int(state.get("version", 4))
    out["pruning_threshold"] = int(state.get("pruning_threshold", PRUNING_THRESHOLD_DEFAULT))
    out["last_round"] = int(state.get("last_round", 0))

    normalized = []
    for i, p in enumerate(state.get("patterns", []) or [], start=1):
        normalized.append(normalize_pattern(p if isinstance(p, dict) else {}, max(1, out["last_round"]), f"LEGACY-{i}"))
    out["patterns"] = normalized
    return out


def _extract_json(raw_text: str) -> Dict[str, Any]:
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.split("```", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def common_usernames(all_prs: Dict[int, Dict[str, Any]], top_n: int = 30) -> List[str]:
    c = Counter(str(pr.get("user") or "").strip().lower() for pr in all_prs.values() if pr.get("user"))
    return [u for u, _ in c.most_common(top_n)]


def regex_safety_warnings(patterns: List[Dict[str, Any]], usernames: List[str]) -> List[str]:
    warns: List[str] = []
    username_re = re.compile(r"@[A-Za-z0-9][A-Za-z0-9-]{1,38}")
    pr_re = re.compile(r"#\d{3,5}")
    user_word_res = [re.compile(rf"\b{re.escape(u)}\b", flags=re.I) for u in usernames if u]
    lib_res = [re.compile(pat, flags=re.I) for pat in SPECIFIC_LIB_PATTERNS]

    for p in patterns:
        pid = p.get("id", "?")
        for field in ("pattern", "anti_pattern"):
            txt = str(p.get(field, ""))
            if pr_re.search(txt):
                warns.append(f"warning: {pid}.{field} contains PR reference (#NNN)")
            if username_re.search(txt):
                warns.append(f"warning: {pid}.{field} contains GitHub @username")
            if any(rx.search(txt) for rx in user_word_res):
                warns.append(f"warning: {pid}.{field} contains common repository username")
            if any(rx.search(txt) for rx in lib_res):
                warns.append(f"warning: {pid}.{field} contains specific library name")
    return warns


def _build_prompt(
    round_num: int,
    errors: List[Dict[str, Any]],
    all_prs: Dict[int, Dict[str, Any]],
    inherited_patterns: List[Dict[str, Any]],
) -> str:
    inherited_payload = [
        {
            "id": p["id"],
            "pattern": p.get("pattern", ""),
            "evidence": p.get("evidence", ""),
            "mechanism": p.get("mechanism", ""),
            "anti_pattern": p.get("anti_pattern", ""),
            "confidence": p.get("confidence", 0.5),
            "support": p.get("support", 0),
            "status": p.get("status", "active"),
            "kind": p.get("kind", "qualitative"),
            "strength": p.get("strength", "heuristic"),
            "since_round": p.get("since_round", round_num),
            "last_validated": p.get("last_validated", round_num),
            "attributions": p.get("attributions", []),
        }
        for p in inherited_patterns
    ]

    error_blocks = []
    for e in errors:
        n = int(e.get("pr_number", -1))
        pr = all_prs.get(n, {"number": n, "title": "", "comments": [], "reviews": [], "files": []})
        etype = str(e.get("error_type", "")).lower()
        # Prefer reflection (model's self-critique of why it erred) over reasoning (original prediction logic)
        # Reasoning describes the WRONG thinking; reflection describes what SHOULD change.
        reflection = e.get("reflection", "") or ""
        reasoning = e.get("reasoning", "") or e.get("qualitative_signals", "") or ""
        if etype == "fp":
            polarity_note = (
                "⚠️ FALSE POSITIVE: Model predicted MERGE but this PR was CLOSED. "
                "The reasoning below explains WHY the model incorrectly predicted merge. "
                "These reasons FAILED — extract patterns that CORRECT this mistake, not patterns that repeat it."
            )
        elif etype == "fn":
            polarity_note = (
                "⚠️ FALSE NEGATIVE: Model predicted CLOSED but this PR was MERGED. "
                "The reasoning below explains WHY the model incorrectly predicted close. "
                "These reasons FAILED — extract patterns that CORRECT this mistake, not patterns that repeat it."
            )
        else:
            polarity_note = ""
        error_blocks.append(
            {
                "pr_number": n,
                "error_type": etype,
                "polarity_note": polarity_note,
                "original_reasoning": reasoning,
                "reflection": reflection if reflection else "(no reflection available)",
                "features": e.get("features", {}),
                "pr_content": format_pr_for_prompt(pr),
            }
        )

    return (
        "You analyze FP/FN model errors and maintain a lifecycle pattern catalog for future unseen PRs.\n"
        "CRITICAL: Output ONLY the JSON object. No analysis, no explanations, no markdown before/after the JSON.\n\n"
        "## CRITICAL: ERROR SIGNAL POLARITY\n"
        "Each error contains TWO fields:\n"
        "- `original_reasoning`: what the model THOUGHT when it made the prediction (this thinking was WRONG)\n"
        "- `reflection`: the model's SELF-CRITIQUE after learning it was wrong (this is more reliable)\n"
        "Use the REFLECTION to understand what went wrong. The original_reasoning shows the FLAWED logic.\n"
        "You must extract CORRECTIVE patterns — rules that PREVENT the error from recurring.\n\n"
        "- FALSE POSITIVE (FP): model predicted MERGE but ground truth was CLOSED.\n"
        "  The original_reasoning describes features it INCORRECTLY trusted as merge signals.\n"
        "  → Corrective pattern: 'When [feature], do NOT assume merge because [why it fails].'\n"
        "  → WRONG pattern: 'When [feature], predict merge.' (This REINFORCES the error!)\n\n"
        "- FALSE NEGATIVE (FN): model predicted CLOSED but ground truth was MERGED.\n"
        "  The model's reasoning describes features it INCORRECTLY trusted as close signals.\n"
        "  → Corrective pattern: 'When [feature], do NOT assume close because [why it fails].'\n"
        "  → WRONG pattern: 'When [feature], predict close.' (This REINFORCES the error!)\n\n"
        "SELF-CHECK (mandatory before finalizing EACH pattern):\n"
        "  Ask: 'If the model had followed this pattern in the round that generated these errors,\n"
        "  would it have made MORE of the same errors, or FEWER?'\n"
        "  If MORE → you have inverted the signal. FLIP the pattern.\n"
        "  If the pattern prescribes the same behavior that caused the errors it was derived from → REJECT it.\n\n"
        "## CONSOLIDATION BY LATENT VARIABLE\n"
        "Each pattern MUST represent exactly ONE latent variable — a distinct causal factor that influences merge decisions.\n"
        "If two patterns would collapse to the same logit feature in a regression model, they ARE the same pattern — MERGE them.\n"
        "Target: ≤15 active patterns. If you would exceed this, consolidate aggressively.\n\n"
        "## KIND CLASSIFICATION\n"
        "- `deterministic`: mechanically verifiable from PR metadata (e.g., 'has human review approval')\n"
        "- `qualitative`: requires judgment to assess (e.g., 'review tone suggests maintainer hesitation')\n\n"
        "## STRENGTH CLASSIFICATION\n"
        "- `deterministic`: always holds when conditions met (≥95% reliability)\n"
        "- `strong`: holds in most cases with clear exceptions (≥75% reliability)\n"
        "- `heuristic`: useful signal but frequently overridden (≥50% reliability)\n\n"
        "## BIMODAL INVESTIGATION\n"
        "When a feature shows bimodal distribution (e.g., merge_rate is 0% for some authors, >20% for others),\n"
        "investigate the CAUSE of the separation. Don't just describe the split — explain what creates two populations.\n\n"
        "Generalization directive: each pattern must apply to PRs you have not seen. "
        "No PR numbers, no author names, and no specific library names in pattern or anti_pattern.\n\n"
        "Input contains:\n"
        "1) ALL round errors with PR content, each marked with ERROR TYPE and POLARITY REMINDER\n"
        "2) inherited patterns in full structured form\n\n"
        "Tasks:\n"
        "- Create NEW corrective patterns from this round's errors. Each = 1 latent variable.\n"
        "- For EACH error, attempt attribution to inherited pattern IDs.\n"
        "- Revise inherited patterns that caused errors (especially if an inherited pattern REINFORCED an error).\n"
        "- If any inherited patterns map to the same latent variable, merge them (set one to 'retired', update the other).\n"
        "- Keep mechanism causal (not correlational).\n\n"
        "Required JSON output schema:\n"
        "{\n"
        '  "new_patterns": [\n'
        "    {\n"
        f'      "id": "P-{round_num}-1",\n'
        '      "pattern": "...",\n'
        '      "evidence": "...",\n'
        '      "mechanism": "...",\n'
        '      "anti_pattern": "...",\n'
        '      "kind": "qualitative",\n'
        '      "strength": "strong",\n'
        '      "confidence": 0.0,\n'
        '      "support": 7,\n'
        '      "status": "active",\n'
        f'      "since_round": {round_num},\n'
        f'      "last_validated": {round_num},\n'
        '      "attributions": []\n'
        "    }\n"
        "  ],\n"
        '  "revisions": [\n'
        "    {\n"
        '      "id": "existing-pattern-id",\n'
        '      "anti_pattern": "refined boundary",\n'
        '      "evidence": "updated evidence",\n'
        '      "mechanism": "updated causal explanation",\n'
        '      "kind": "qualitative",\n'
        '      "strength": "strong",\n'
        '      "confidence": 0.75,\n'
        '      "support": 12,\n'
        '      "status": "revised"\n'
        "    }\n"
        "  ],\n"
        '  "error_attributions": [\n'
        '    {"pr_number": 1234, "error_type": "fp", "attribution": "P-3-2", "reason": "..."},\n'
        '    {"pr_number": 5678, "error_type": "fn", "attribution": "ambiguous", "reason": "..."}\n'
        "  ],\n"
        '  "consolidation_notes": "Explanation of any merges performed and why patterns map to same latent variable."\n'
        "}\n\n"
        f"Current round: {round_num}\n"
        f"Inherited patterns: {json.dumps(inherited_payload, ensure_ascii=False)}\n\n"
        f"Round errors with content: {json.dumps(error_blocks, ensure_ascii=False)}\n"
    )


def _dry_run_batch(round_num: int, errors: List[Dict[str, Any]], inherited_patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    attrs = []
    first_inherited = inherited_patterns[0]["id"] if inherited_patterns else None
    for e in errors:
        attrs.append(
            {
                "pr_number": int(e.get("pr_number", -1)),
                "error_type": str(e.get("error_type", "")).lower(),
                "attribution": first_inherited if first_inherited else "ambiguous",
                "reason": "dry-run placeholder attribution",
            }
        )
    revisions = []
    if first_inherited:
        revisions.append(
            {
                "id": first_inherited,
                "anti_pattern": "Do not apply when per-PR scope and maintainer alignment contradict the broader profile.",
                "evidence": "Dry-run revision from observed mixed FP/FN profiles.",
                "mechanism": "Boundary conditions reduce overgeneralization from coarse historical priors.",
                "kind": inherited_patterns[0].get("kind", "qualitative"),
                "strength": inherited_patterns[0].get("strength", "heuristic"),
                "confidence": 0.55,
                "support": len(errors),
                "status": "revised",
            }
        )

    return {
        "new_patterns": [
            {
                "id": f"P-{round_num}-1",
                "pattern": "When signals are sparse, prioritize concrete scope and maintainability cues over contributor-level priors.",
                "evidence": "Errors cluster where coarse priors conflict with PR-level scope and reviewability profiles.",
                "mechanism": "PR-level execution signals are closer to merge decisions than historical contributor aggregates.",
                "anti_pattern": "Do not apply when a PR has high uncertainty and no clear implementation signal.",
                "kind": "qualitative",
                "strength": "heuristic",
                "confidence": 0.0,
                "support": len(errors),
                "status": "active",
                "since_round": round_num,
                "last_validated": round_num,
                "attributions": [],
            }
        ],
        "revisions": revisions,
        "error_attributions": attrs,
        "consolidation_notes": "dry-run: no consolidation performed",
    }


def _run_batch(
    round_num: int,
    errors: List[Dict[str, Any]],
    all_prs: Dict[int, Dict[str, Any]],
    inherited_patterns: List[Dict[str, Any]],
    dry_run: bool,
) -> Dict[str, Any]:
    if dry_run:
        return _dry_run_batch(round_num, errors, inherited_patterns)

    prompt = _build_prompt(round_num, errors, all_prs, inherited_patterns)
    raw = call_sonnet(prompt, max_tokens=8192)
    try:
        parsed = _extract_json(raw)
    except Exception:
        parsed = {"new_patterns": [], "revisions": [], "error_attributions": [], "consolidation_notes": ""}
    return {
        "new_patterns": parsed.get("new_patterns", []),
        "revisions": parsed.get("revisions", []),
        "error_attributions": parsed.get("error_attributions", []),
        "consolidation_notes": parsed.get("consolidation_notes", ""),
    }


def _error_batches_for_context_cap(
    round_num: int,
    errors: List[Dict[str, Any]],
    all_prs: Dict[int, Dict[str, Any]],
    inherited_patterns: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    full_prompt = _build_prompt(round_num, errors, all_prs, inherited_patterns)
    estimated_tokens = len(full_prompt) / 4
    if estimated_tokens <= 80000 or len(errors) <= 1:
        return [errors]
    mid = len(errors) // 2
    return [errors[:mid], errors[mid:]]


def apply_updates(
    state: Dict[str, Any],
    round_num: int,
    errors: List[Dict[str, Any]],
    batch_outputs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    patterns = [normalize_pattern(p, round_num, f"LEGACY-{i}") for i, p in enumerate(state.get("patterns", []), start=1)]
    by_id = {p["id"]: p for p in patterns}

    raw_new_patterns: List[Dict[str, Any]] = []
    raw_revisions: List[Dict[str, Any]] = []
    raw_attributions: List[Dict[str, Any]] = []
    consolidation_notes: List[str] = []
    for out in batch_outputs:
        raw_new_patterns.extend(out.get("new_patterns", []) or [])
        raw_revisions.extend(out.get("revisions", []) or [])
        raw_attributions.extend(out.get("error_attributions", []) or [])
        note = out.get("consolidation_notes", "")
        if note:
            consolidation_notes.append(note)

    seq = 1
    for raw in raw_new_patterns:
        fallback_id = f"P-{round_num}-{seq}"
        p = normalize_pattern(raw if isinstance(raw, dict) else {}, round_num, fallback_id)
        p["id"] = f"P-{round_num}-{seq}"
        p["since_round"] = round_num
        p["last_validated"] = round_num
        p["status"] = "active"
        patterns.append(p)
        by_id[p["id"]] = p
        seq += 1

    revised_ids = set()
    for upd in raw_revisions:
        if not isinstance(upd, dict):
            continue
        pid = str(upd.get("id") or "").strip()
        if not pid or pid not in by_id:
            continue
        p = by_id[pid]
        anti = str(upd.get("anti_pattern") or "").strip()
        if anti:
            p["anti_pattern"] = anti
        for k in ("evidence", "mechanism"):
            val = str(upd.get(k) or "").strip()
            if val:
                p[k] = val
        # Update kind and strength if provided
        kind = str(upd.get("kind") or "").strip().lower()
        if kind in {"deterministic", "qualitative"}:
            p["kind"] = kind
        strength = str(upd.get("strength") or "").strip().lower()
        if strength in {"deterministic", "strong", "heuristic"}:
            p["strength"] = strength
        try:
            p["support"] = max(0, int(upd.get("support", p.get("support", 0))))
        except Exception:
            pass
        p["status"] = "revised"
        p["last_validated"] = round_num
        revised_ids.add(pid)

    known_error_keys = {
        (int(e.get("pr_number", -1)), str(e.get("error_type", "")).lower())
        for e in errors
    }

    for raw in raw_attributions:
        if not isinstance(raw, dict):
            continue
        try:
            prn = int(raw.get("pr_number", -1))
        except Exception:
            continue
        et = str(raw.get("error_type", "")).lower()
        if (prn, et) not in known_error_keys:
            continue
        attr = str(raw.get("attribution") or "").strip()
        is_ambiguous = attr.lower() == "ambiguous"
        if not is_ambiguous and attr not in by_id:
            attr = "ambiguous"
            is_ambiguous = True

        event = {
            "round": round_num,
            "pr_number": prn,
            "error_type": et,
            "attribution": attr,
            "ambiguous": is_ambiguous,
            "reason": str(raw.get("reason") or ""),
        }
        if not is_ambiguous:
            by_id[attr].setdefault("attributions", []).append(event)

    # Recalculate confidence via CLT for all patterns
    for p in patterns:
        rounds_with_match = len({
            int(a.get("round", 0)) for a in p.get("attributions", [])
            if isinstance(a, dict) and not a.get("ambiguous")
        })
        p["confidence"] = round(clt_confidence(p.get("strength", "heuristic"), rounds_with_match), 4)

    # Pruning
    pruning_threshold = int(state.get("pruning_threshold", PRUNING_THRESHOLD_DEFAULT))
    for p in patterns:
        if p.get("status") == "retired":
            continue
        rounds = sorted({int(a.get("round")) for a in p.get("attributions", []) if not a.get("ambiguous")})
        streak = 1
        max_streak = 1 if rounds else 0
        for i in range(1, len(rounds)):
            if rounds[i] == rounds[i - 1] + 1:
                streak += 1
            else:
                streak = 1
            if streak > max_streak:
                max_streak = streak
        if max_streak >= pruning_threshold and p["id"] not in revised_ids:
            p["status"] = "retired"

    # Consolidation warning
    active_count = sum(1 for p in patterns if p.get("status") in {"active", "revised"})
    if active_count > 15:
        print(f"WARNING: {active_count} active patterns exceeds target of 15. Consider consolidation.")

    active_or_revised = {p["id"] for p in patterns if p.get("status") in {"active", "revised"}}
    for p in patterns:
        has_unambiguous_this_round = any(
            int(a.get("round", -1)) == round_num and not a.get("ambiguous") for a in p.get("attributions", [])
        )
        if p["id"] in active_or_revised and not has_unambiguous_this_round:
            p["last_validated"] = round_num

    return {
        "version": 4,
        "patterns": patterns,
        "pruning_threshold": pruning_threshold,
        "last_round": max(int(state.get("last_round", 0)), round_num),
        "consolidation_notes": consolidation_notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--errors", type=Path, required=True, help="round errors JSON")
    ap.add_argument("--all-prs", type=Path, required=True, help="all_historical_prs.json or enriched")
    ap.add_argument("--patterns-state", type=Path, required=True, help="patterns_state.json")
    ap.add_argument("--round", type=int, required=True, help="current round number")
    ap.add_argument("--output", type=Path, required=True, help="updated patterns_state.json")
    ap.add_argument("--dry-run", action="store_true", help="generate placeholder patterns")
    args = ap.parse_args()

    errors_payload = json.load(args.errors.open())
    errors = errors_payload.get("errors", errors_payload if isinstance(errors_payload, list) else [])
    errors = [e for e in errors if isinstance(e, dict)]

    all_prs = {int(p["number"]): p for p in json.load(args.all_prs.open()) if isinstance(p, dict) and "number" in p}

    state = load_patterns_state(args.patterns_state)
    inherited = [
        p for p in state.get("patterns", [])
        if p.get("status") in {"active", "revised"}
    ]

    batches = _error_batches_for_context_cap(args.round, errors, all_prs, inherited)
    if len(batches) > 1:
        print(f"extract_patterns_v4: context estimate exceeded 80k tokens; split into {len(batches)} batches")

    batch_outputs = [
        _run_batch(args.round, b, all_prs, inherited, args.dry_run)
        for b in batches
    ]

    updated_state = apply_updates(state, args.round, errors, batch_outputs)

    warnings = regex_safety_warnings(
        [p for p in updated_state.get("patterns", []) if p.get("status") in {"active", "revised"}],
        common_usernames(all_prs),
    )
    for w in warnings:
        print(w)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    json.dump(updated_state, args.output.open("w"), indent=2)
    active_revised = sum(1 for p in updated_state.get("patterns", []) if p.get("status") in {"active", "revised"})
    print(
        f"wrote {args.output} "
        f"(patterns={len(updated_state.get('patterns', []))}, "
        f"active_or_revised={active_revised})"
    )


if __name__ == "__main__":
    main()
