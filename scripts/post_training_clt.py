#!/usr/bin/env python3
"""Calibrate Bootstrap v3 pattern confidence using CLT confidence scaling."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATTERNS_IN = ROOT / "data" / "bootstrap_v3" / "patterns_state.json"
PATTERNS_OUT = ROOT / "data" / "bootstrap_v3" / "patterns_state_calibrated.json"
ERRORS_GLOB_DIR = ROOT / "data" / "bootstrap_v3"
MODEL_ID = "claude-sonnet-4-5"
ROUND_RE = re.compile(r"round_(\d+)_errors\.json$")
PATTERN_ID_RE = re.compile(r"\bP-\d+-\d+\b")

STRENGTH_VALUES = {
    "deterministic": 0.95,
    "strong": 0.75,
    "heuristic": 0.50,
}


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

    raise RuntimeError(
        "No Anthropic token found. Set ANTHROPIC_API_KEY or ANTHROPIC_PROFILE with a valid profile."
    )


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
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                print(f"post_training_clt: rate limited, sleeping 30s (attempt {attempt + 1}/3)")
                time.sleep(30)
                continue
            raise
    raise RuntimeError("post_training_clt: call_sonnet failed after 3 attempts")


def extract_json_payload(raw_text: str) -> Any:
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.split("```", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def classify_strengths(patterns: list[dict[str, Any]], dry_run: bool) -> dict[str, tuple[str, float]]:
    if dry_run:
        return {str(p.get("id")): ("Strong", 0.75) for p in patterns}

    payload_patterns = []
    for p in patterns:
        payload_patterns.append(
            {
                "id": p.get("id"),
                "pattern": p.get("pattern"),
                "evidence": p.get("evidence"),
                "mechanism": p.get("mechanism"),
                "anti_pattern": p.get("anti_pattern"),
            }
        )

    instruction = (
        "Classify each pattern mechanism into Deterministic/Strong/Heuristic. "
        "Do not modify pattern text, evidence, mechanism, or anti_pattern fields. "
        "Return JSON array of {id, strength_bucket, strength_value, justification}."
    )
    prompt = (
        f"{instruction}\n\n"
        "Strength definitions:\n"
        "- Deterministic (0.95): binary signal, verifiable mechanism, exception requires data error\n"
        "- Strong (0.75): clear mechanism, rare exceptions\n"
        "- Heuristic (0.50): observed pattern, plausible mechanism, common exceptions\n\n"
        "Patterns:\n"
        f"{json.dumps(payload_patterns, indent=2)}"
    )

    response = call_sonnet(prompt)
    parsed = extract_json_payload(response)
    if not isinstance(parsed, list):
        raise RuntimeError("Sonnet response is not a JSON array")

    out: dict[str, tuple[str, float]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id", "")).strip()
        if not pid:
            continue

        bucket_raw = str(item.get("strength_bucket", "")).strip()
        bucket_key = bucket_raw.lower()
        if bucket_key not in STRENGTH_VALUES:
            continue

        value = item.get("strength_value")
        if isinstance(value, (float, int)):
            strength_value = float(value)
            if abs(strength_value - STRENGTH_VALUES[bucket_key]) > 1e-6:
                strength_value = STRENGTH_VALUES[bucket_key]
        else:
            strength_value = STRENGTH_VALUES[bucket_key]

        pretty_bucket = bucket_key.capitalize()
        out[pid] = (pretty_bucket, strength_value)

    # Default missing/invalid ids to Strong.
    for p in patterns:
        pid = str(p.get("id"))
        if pid not in out:
            out[pid] = ("Strong", 0.75)

    return out


def parse_round_num(path: Path) -> int | None:
    m = ROUND_RE.search(path.name)
    if not m:
        return None
    return int(m.group(1))


def extract_pattern_ids_from_attributions(attributions: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(attributions, list):
        for item in attributions:
            if isinstance(item, str):
                ids.update(PATTERN_ID_RE.findall(item))
            elif isinstance(item, dict):
                for key in ("id", "pattern_id", "attribution", "pattern"):
                    val = item.get(key)
                    if isinstance(val, str):
                        ids.update(PATTERN_ID_RE.findall(val))
    elif isinstance(attributions, dict):
        for key, val in attributions.items():
            if isinstance(key, str):
                ids.update(PATTERN_ID_RE.findall(key))
            if isinstance(val, str):
                ids.update(PATTERN_ID_RE.findall(val))
            elif isinstance(val, dict):
                for k2 in ("id", "pattern_id", "attribution", "pattern"):
                    v2 = val.get(k2)
                    if isinstance(v2, str):
                        ids.update(PATTERN_ID_RE.findall(v2))
            elif isinstance(val, list):
                ids.update(extract_pattern_ids_from_attributions(val))
    elif isinstance(attributions, str):
        ids.update(PATTERN_ID_RE.findall(attributions))
    return ids


def count_round_matches(patterns: list[dict[str, Any]], last_round: int) -> tuple[dict[str, int], bool]:
    round_matches: dict[str, set[int]] = {str(p.get("id")): set() for p in patterns}
    attribution_data_found = False

    error_files = sorted(ERRORS_GLOB_DIR.glob("round_*_errors.json"), key=lambda p: p.name)
    for path in error_files:
        round_num = parse_round_num(path)
        if round_num is None:
            continue

        try:
            data = json.load(path.open())
        except Exception:
            continue

        errors = data.get("errors", []) if isinstance(data, dict) else data
        if not isinstance(errors, list):
            continue

        for err in errors:
            if not isinstance(err, dict):
                continue
            if "attributions" not in err:
                continue
            attributions = err.get("attributions")
            ids = extract_pattern_ids_from_attributions(attributions)
            if not ids:
                continue
            attribution_data_found = True
            for pid in ids:
                if pid in round_matches:
                    round_matches[pid].add(round_num)

    if attribution_data_found:
        return ({pid: len(rounds) for pid, rounds in round_matches.items()}, True)

    fallback: dict[str, int] = {}
    for p in patterns:
        pid = str(p.get("id"))
        since_round = p.get("since_round", 1)
        try:
            since = int(since_round)
        except Exception:
            since = 1
        since = max(1, since)
        n = max(0, last_round - since + 1)
        fallback[pid] = n
    return fallback, False


def instruction_tier(conf: float) -> str:
    if conf > 0.5:
        return "Apply"
    if conf >= 0.3:
        return "Consider"
    return "Tiebreaker"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply CLT confidence calibration to bootstrap v3 patterns")
    parser.add_argument("--dry-run", action="store_true", help="Skip Sonnet classification and use Strong (0.75) for all")
    parser.add_argument("--input", type=Path, default=PATTERNS_IN, help="Input patterns_state.json path")
    parser.add_argument("--output", type=Path, default=PATTERNS_OUT, help="Output calibrated state path")
    args = parser.parse_args()

    state = json.load(args.input.open())
    patterns = state.get("patterns", []) if isinstance(state, dict) else []
    if not isinstance(patterns, list):
        raise RuntimeError("Invalid patterns_state: patterns must be a list")

    if not patterns:
        raise RuntimeError("No patterns found in input state")

    last_round = state.get("last_round", 10)
    try:
        last_round = int(last_round)
    except Exception:
        last_round = 10

    strengths = classify_strengths(patterns, dry_run=args.dry_run)
    rounds_with_match, used_attribution_data = count_round_matches(patterns, last_round)

    rows: list[tuple[str, float, str, int, float, str]] = []

    for p in patterns:
        pid = str(p.get("id"))
        old_conf_raw = p.get("confidence", 0.0)
        try:
            old_conf = float(old_conf_raw)
        except Exception:
            old_conf = 0.0

        strength_bucket, strength_value = strengths.get(pid, ("Strong", 0.75))
        n = int(rounds_with_match.get(pid, 0))
        new_conf = strength_value * (1.0 - 1.0 / math.sqrt(n + 1.0))
        new_conf = max(0.0, min(1.0, new_conf))

        p["confidence"] = round(new_conf, 4)

        support_raw = p.get("support", 0)
        try:
            support = int(support_raw)
        except Exception:
            support = 0

        if n >= 3 and support <= 0 and str(p.get("status", "")).lower() != "retired":
            p["status"] = "retired"

        rows.append((pid, old_conf, f"{strength_bucket}({strength_value:.2f})", n, p["confidence"], instruction_tier(p["confidence"])))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")

    print(f"Loaded {len(patterns)} patterns from {args.input}")
    print(f"Round counting mode: {'attributions in round errors' if used_attribution_data else 'fallback since_round..last_round'}")
    print(f"Wrote calibrated state to {args.output}\n")

    header = ["pattern_id", "old_confidence", "strength", "n", "new_confidence", "instruction_tier"]
    widths = [12, 14, 20, 4, 14, 16]
    print(" ".join(h.ljust(w) for h, w in zip(header, widths)))
    print(" ".join("-" * w for w in widths))
    for pid, old_c, strength, n, new_c, tier in rows:
        print(
            f"{pid:<12} {old_c:<14.4f} {strength:<20} {n:<4d} {new_c:<14.4f} {tier:<16}"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"post_training_clt: error: {exc}", file=sys.stderr)
        raise
