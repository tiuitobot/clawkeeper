#!/usr/bin/env python3
"""Extract lifecycle-managed learning patterns from round errors using Sonnet."""

from __future__ import annotations

import argparse
import json
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
]

SPECIFIC_LIB_PATTERNS = [
    r"\\breact\\b",
    r"\\bvue\\b",
    r"\\bangular\\b",
    r"\\bnext\\.js\\b",
    r"\\bexpress\\b",
    r"\\bdjango\\b",
    r"\\bflask\\b",
    r"\\bfastapi\\b",
    r"\\bnumpy\\b",
    r"\\bpandas\\b",
    r"\\btensorflow\\b",
    r"\\bpytorch\\b",
    r"\\bgrammy\\b",
    r"\\btelegraf\\b",
    r"\\bdiscord\\.js\\b",
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


def call_sonnet(prompt: str, max_tokens: int = 4096) -> str:
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
                print(f"extract_patterns_v3: rate limited, sleeping 30s (attempt {attempt+1}/3)")
                time.sleep(30)
                continue
            raise
    raise RuntimeError("extract_patterns_v3: call_sonnet failed after 3 attempts")


def format_pr_for_prompt(pr: dict) -> str:
    labels = ", ".join(pr.get("labels", [])) or "none"
    author = pr.get("user", "unknown")
    text = f"""## PR #{pr.get('number')}: {pr.get('title', '')}

- **Author:** {author}
- **Created:** {pr.get('created_at', '')}
- **Labels:** {labels}
- **Size:** +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} ({pr.get('changed_files', pr.get('changedFiles', 0))} files)
- **Draft:** {pr.get('draft', False)}
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
            body = (c.get("body", "") or "").replace("\n", " ")[:300]
            text += f"- **{user}** ({assoc}): {body}\n"

    reviews = pr.get("reviews", []) or []
    if reviews:
        text += f"\n### Reviews ({len(reviews)}):\n"
        for r in reviews[:10]:
            if not isinstance(r, dict):
                continue
            user = r.get("author", {}).get("login") if isinstance(r.get("author"), dict) else r.get("user")
            user = user or "?"
            state = r.get("state", "?")
            body = (r.get("body", "") or "").replace("\n", " ")[:300]
            text += f"- **{user}**: {state} — {body}\n"

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
        "version": 3,
        "patterns": [],
        "pruning_threshold": PRUNING_THRESHOLD_DEFAULT,
        "last_round": 0,
    }


def normalize_pattern(raw: Dict[str, Any], round_num: int, fallback_id: str) -> Dict[str, Any]:
    p = {k: raw.get(k) for k in REQUIRED_PATTERN_FIELDS}
    p["id"] = str(p.get("id") or fallback_id)
    p["pattern"] = str(p.get("pattern") or "").strip()
    p["evidence"] = str(p.get("evidence") or "").strip()
    p["mechanism"] = str(p.get("mechanism") or "").strip()
    p["anti_pattern"] = str(p.get("anti_pattern") or "").strip()
    try:
        p["confidence"] = max(0.0, min(1.0, float(p.get("confidence", 0.5))))
    except Exception:
        p["confidence"] = 0.5
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
    attrs = p.get("attributions", [])
    p["attributions"] = attrs if isinstance(attrs, list) else []
    return p


def load_patterns_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        state = default_state()
        json.dump(state, path.open("w"), indent=2)
        return state

    state = json.load(path.open())
    out = default_state()
    out["version"] = int(state.get("version", 3))
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
    user_word_res = [re.compile(rf"\\b{re.escape(u)}\\b", flags=re.I) for u in usernames if u]
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
        error_blocks.append(
            {
                "pr_number": n,
                "error_type": str(e.get("error_type", "")).lower(),
                "reasoning": e.get("reasoning", ""),
                "features": e.get("features", {}),
                "pr_content": format_pr_for_prompt(pr),
            }
        )

    return (
        "You analyze FP/FN model errors and maintain a lifecycle pattern catalog for future unseen PRs.\n"
        "Generalization directive: each pattern must apply to PRs you have not seen. "
        "No PR numbers, no author names, and no specific library names in pattern or anti_pattern.\n\n"
        "Input contains:\n"
        "1) ALL round errors with PR content (title/comments/reviews/files where available)\n"
        "2) inherited patterns (active/revised) in full structured form\n\n"
        "Tasks:\n"
        "- Create NEW patterns from this round.\n"
        "- For EACH error, attempt attribution to inherited pattern IDs:\n"
        "  - If unambiguous, set attribution to a single pattern ID.\n"
        "  - If ambiguous among 2+ causes, set attribution='ambiguous'.\n"
        "- Revise inherited patterns that caused errors by refining anti_pattern and set status='revised'.\n"
        "- Keep mechanism causal (not correlational).\n\n"
        "Required JSON output schema:\n"
        "{\n"
        "  \"new_patterns\": [\n"
        "    {\n"
        "      \"id\": \"P-{round}-1\",\n"
        "      \"pattern\": \"...\",\n"
        "      \"evidence\": \"...\",\n"
        "      \"mechanism\": \"...\",\n"
        "      \"anti_pattern\": \"...\",\n"
        "      \"confidence\": 0.85,\n"
        "      \"support\": 7,\n"
        "      \"status\": \"active\",\n"
        "      \"since_round\": {round},\n"
        "      \"last_validated\": {round},\n"
        "      \"attributions\": []\n"
        "    }\n"
        "  ],\n"
        "  \"revisions\": [\n"
        "    {\n"
        "      \"id\": \"existing-pattern-id\",\n"
        "      \"anti_pattern\": \"refined boundary\",\n"
        "      \"evidence\": \"updated evidence profile\",\n"
        "      \"mechanism\": \"updated causal explanation\",\n"
        "      \"confidence\": 0.75,\n"
        "      \"support\": 12,\n"
        "      \"status\": \"revised\"\n"
        "    }\n"
        "  ],\n"
        "  \"error_attributions\": [\n"
        "    {\"pr_number\": 1234, \"error_type\": \"fp\", \"attribution\": \"P-3-2\", \"reason\": \"...\"},\n"
        "    {\"pr_number\": 5678, \"error_type\": \"fn\", \"attribution\": \"ambiguous\", \"reason\": \"...\"}\n"
        "  ]\n"
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
                "anti_pattern": "Do not apply when a PR has high uncertainty and no clear implementation signal in either direction.",
                "confidence": 0.6,
                "support": len(errors),
                "status": "active",
                "since_round": round_num,
                "last_validated": round_num,
                "attributions": [],
            }
        ],
        "revisions": revisions,
        "error_attributions": attrs,
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
    raw = call_sonnet(prompt, max_tokens=4096)
    try:
        parsed = _extract_json(raw)
    except Exception:
        parsed = {"new_patterns": [], "revisions": [], "error_attributions": []}
    return {
        "new_patterns": parsed.get("new_patterns", []),
        "revisions": parsed.get("revisions", []),
        "error_attributions": parsed.get("error_attributions", []),
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
    for out in batch_outputs:
        raw_new_patterns.extend(out.get("new_patterns", []) or [])
        raw_revisions.extend(out.get("revisions", []) or [])
        raw_attributions.extend(out.get("error_attributions", []) or [])

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
        try:
            p["confidence"] = max(0.0, min(1.0, float(upd.get("confidence", p.get("confidence", 0.5)))))
        except Exception:
            pass
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

    active_or_revised = {p["id"] for p in patterns if p.get("status") in {"active", "revised"}}
    for p in patterns:
        has_unambiguous_this_round = any(
            int(a.get("round", -1)) == round_num and not a.get("ambiguous") for a in p.get("attributions", [])
        )
        if p["id"] in active_or_revised and not has_unambiguous_this_round:
            p["last_validated"] = round_num

    return {
        "version": 3,
        "patterns": patterns,
        "pruning_threshold": pruning_threshold,
        "last_round": max(int(state.get("last_round", 0)), round_num),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--errors", type=Path, required=True, help="round errors JSON")
    ap.add_argument("--all-prs", type=Path, required=True, help="all_historical_prs.json")
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
        print(f"extract_patterns_v3: context estimate exceeded 80k tokens; split into {len(batches)} batches")

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
    print(
        f"wrote {args.output} "
        f"(patterns={len(updated_state.get('patterns', []))}, "
        f"active_or_revised={sum(1 for p in updated_state.get('patterns', []) if p.get('status') in {'active','revised'})})"
    )


if __name__ == "__main__":
    main()
