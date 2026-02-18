#!/usr/bin/env python3
"""Extract abstract learning patterns from round errors using Haiku."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "bootstrap_v2"
MODEL_ID = "claude-haiku-4-5"


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


def call_haiku(prompt: str, max_tokens: int = 1200) -> str:
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
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                import time
                print(f"extract_patterns: rate limited, sleeping 30s (attempt {attempt+1}/3)")
                time.sleep(30)
                continue
            raise
    raise RuntimeError("extract_patterns: call_haiku failed after 3 attempts")


def _sanitize_pattern_text(text: str) -> str:
    # enforce no PR numbers and no direct outcomes
    text = re.sub(r"#\d+", "", text)
    text = re.sub(r"\b(PR|pull request)\s*\d+\b", "", text, flags=re.I)
    text = re.sub(r"\b(merged|closed|rejected|accepted)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_prompt(errors: List[Dict[str, Any]]) -> str:
    compact = []
    for e in errors:
        compact.append(
            {
                "features": e.get("features", {}),
                "reasoning": e.get("reasoning", ""),
                "error_type": e.get("error_type", ""),
            }
        )
    return (
        "You are given model errors from PR governance prediction.\n"
        "Extract abstract, reusable patterns only.\n"
        "STRICT RULES:\n"
        "- No PR numbers\n"
        "- No specific outcomes/events from individual cases\n"
        "- Only generalized statements that could transfer\n"
        "Return JSON: {\"patterns\": [{\"pattern\": str, \"confidence\": 0-1, \"support\": int}]}\n\n"
        f"Errors:\n{json.dumps(compact, ensure_ascii=False)}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--errors", type=Path, required=True, help="round_X_errors.json")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = json.load(args.errors.open())
    errors = payload.get("errors", payload if isinstance(payload, list) else [])

    if args.dry_run:
        out = {
            "patterns": [
                {"pattern": "Large changes without strong maintainer alignment tend to underperform.", "confidence": 0.5, "support": len(errors)}
            ]
        }
    else:
        raw = call_haiku(build_prompt(errors))
        try:
            if "```json" in raw:
                raw = raw.split("```json", 1)[1].split("```", 1)[0]
            out = json.loads(raw)
        except Exception:
            out = {"patterns": []}

    clean = []
    for p in out.get("patterns", []):
        pat = _sanitize_pattern_text(str(p.get("pattern", "")))
        if pat:
            clean.append(
                {
                    "pattern": pat,
                    "confidence": float(p.get("confidence", 0.5)),
                    "support": int(p.get("support", 0)),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"patterns": clean}, args.output.open("w"), indent=2)
    print(f"wrote {args.output} ({len(clean)} patterns)")


if __name__ == "__main__":
    main()
