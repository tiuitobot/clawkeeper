#!/usr/bin/env python3
"""Discover new feature candidates from round errors (v4B).

Reads round errors + PR context and asks Sonnet to propose up to N new features.
Persists results in feature_registry.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

MODEL_ID = "claude-sonnet-4-5"


def get_token() -> tuple[str, str]:
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


def call_sonnet(prompt: str) -> Dict[str, Any]:
    token, auth_type = get_token()
    payload = {
        "model": MODEL_ID,
        "max_tokens": 3200,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if auth_type == "oauth":
        headers["Authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        headers["x-api-key"] = token

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = ""
    for c in data.get("content", []):
        if c.get("type") == "text":
            text += c.get("text", "")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"proposals": []}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"proposals": []}


def load_registry(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"features": [], "history": []}
    try:
        d = json.load(path.open())
        if isinstance(d, dict):
            d.setdefault("features", [])
            d.setdefault("history", [])
            return d
    except Exception:
        pass
    return {"features": [], "history": []}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--errors", type=Path, required=True)
    ap.add_argument("--all-prs", type=Path, required=True)
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--max-new", type=int, default=2)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    reg = load_registry(args.registry)
    existing_names = {str(f.get("name", "")).strip().lower() for f in reg.get("features", []) if isinstance(f, dict)}

    if not args.errors.exists() or args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        json.dump(reg, args.output.open("w"), indent=2)
        print(f"wrote {args.output} (features={len(reg.get('features', []))})")
        return

    payload = json.load(args.errors.open())
    errors = payload.get("errors", []) if isinstance(payload, dict) else payload
    errors = [e for e in errors if isinstance(e, dict)]
    if not errors:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        json.dump(reg, args.output.open("w"), indent=2)
        print(f"wrote {args.output} (features={len(reg.get('features', []))})")
        return

    all_prs = {int(p["number"]): p for p in json.loads(args.all_prs.read_text()) if isinstance(p, dict) and p.get("number") is not None}

    blocks = []
    for e in errors[:30]:
        n = int(e.get("pr_number", -1))
        pr = all_prs.get(n, {})
        title = pr.get("title", "") if isinstance(pr, dict) else ""
        body = (pr.get("body", "") if isinstance(pr, dict) else "") or ""
        body = body.replace("\n", " ")[:400]
        et = str(e.get("error_type", "?")).lower()
        reason = str(e.get("reflection", "") or e.get("reasoning", ""))[:320]
        blocks.append(f"PR #{n} [{et}]\nTitle: {title}\nBody: {body}\nWhy wrong: {reason}")

    prompt = f"""You are proposing NEW predictive features for merge/close classification from model errors.

Constraints:
- Propose at most {args.max_new} features.
- Features must be generic, reusable, and extractable from PR data.
- Do not duplicate existing feature names: {sorted(existing_names)}
- Prefer deterministic or strongly operationalized features.
- Return ONLY JSON.

For each feature include:
- name (snake_case)
- value_type (bool|numeric|categorical)
- definition (1-2 lines)
- extraction_hint (how to compute/extract)
- expected_direction (merge|close|nonlinear)
- confidence (0-1)

Error set:
{chr(10).join(blocks)}

Output format:
{{
  "proposals": [
    {{
      "name": "example_feature",
      "value_type": "bool",
      "definition": "...",
      "extraction_hint": "...",
      "expected_direction": "close",
      "confidence": 0.62
    }}
  ]
}}
"""

    out = call_sonnet(prompt)
    proposals = out.get("proposals", []) if isinstance(out, dict) else []
    proposals = [p for p in proposals if isinstance(p, dict)]

    seq = 1 + sum(1 for f in reg.get("features", []) if isinstance(f, dict) and str(f.get("id", "")).startswith("F-"))
    added = 0
    for p in proposals:
        if added >= args.max_new:
            break
        name = str(p.get("name", "")).strip().lower()
        if not name or name in existing_names:
            continue
        feat = {
            "id": f"F-{args.round}-{seq}",
            "name": name,
            "value_type": str(p.get("value_type", "bool")).strip().lower(),
            "definition": str(p.get("definition", "")).strip(),
            "extraction_hint": str(p.get("extraction_hint", "")).strip(),
            "expected_direction": str(p.get("expected_direction", "nonlinear")).strip().lower(),
            "confidence": float(p.get("confidence", 0.5) or 0.5),
            "introduced_round": args.round,
            "status": "candidate",
            "source": "sonnet_error_analysis",
        }
        reg.setdefault("features", []).append(feat)
        reg.setdefault("history", []).append({"round": args.round, "event": "added", "feature": name})
        existing_names.add(name)
        seq += 1
        added += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    json.dump(reg, args.output.open("w"), indent=2)
    print(f"wrote {args.output} (features={len(reg.get('features', []))}, added={added})")


if __name__ == "__main__":
    main()
