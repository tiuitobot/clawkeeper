#!/usr/bin/env python3
"""Bootstrap v3 orchestrator.

R1-R3 baseline (no learned patterns), R4+ with lifecycle-managed abstract patterns.
Do not require human intervention; can resume via --start-round.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "bootstrap_v3"
SCRIPTS = ROOT / "scripts"
MODEL_SPEC = ROOT / "model_spec.json"
MODEL_ID = "claude-haiku-4-5"

sys.path.insert(0, str(SCRIPTS))
from sanitize import sanitize_pr


def log_line(path: Path, msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")


def run_py(script: str, args: List[str]) -> None:
    cmd = [sys.executable, str(SCRIPTS / script)] + args
    subprocess.run(cmd, check=True)


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


def call_haiku(prompt: str, max_tokens: int = 8000) -> dict:
    token, auth_type = get_token()
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
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                wait = 60 * (attempt + 1)  # 60s, 120s
                print(f"rate limited; sleeping {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            raise

        text = data["content"][0]["text"]
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            if attempt < 2:
                print(f"JSON parse error at char {e.pos}/{len(text)}; retrying (attempt {attempt+1}/3)")
                payload["max_tokens"] = min(payload["max_tokens"] + 2000, 8192)
                time.sleep(2)
                continue
            print(f"JSON parse FATAL: {e}. Last 200 chars: {text[-200:]}")
            raise
    raise RuntimeError("call_haiku failed after 3 attempts")


def sanitize_batch(batch: List[dict]) -> List[dict]:
    return [sanitize_pr(pr) for pr in batch]


def compute_author_stats(all_prs: Dict[int, dict]) -> Dict[int, Dict[str, Any]]:
    authored: Dict[str, List[dict]] = {}
    for pr in all_prs.values():
        authored.setdefault(pr.get("user", ""), []).append(pr)

    stats_by_pr: Dict[int, Dict[str, Any]] = {}
    for author, prs in authored.items():
        ordered = sorted(prs, key=lambda p: (p.get("created_at") or "", int(p.get("number", 0))))
        prior_prs = 0
        prior_merged = 0
        for pr in ordered:
            pr_num = int(pr["number"])
            merge_rate = (prior_merged / prior_prs) if prior_prs else 0.0
            stats_by_pr[pr_num] = {
                "prior_prs": prior_prs,
                "prior_merged": prior_merged,
                "merge_rate": merge_rate,
            }
            prior_prs += 1
            if bool(pr.get("merged_at") or pr.get("merged")):
                prior_merged += 1
    return stats_by_pr


def format_pr_for_prompt(pr: dict) -> str:
    labels = ", ".join(pr.get("labels", [])) or "none"
    author = pr.get("user", "unknown")
    prior_prs = int(pr.get("prior_prs", 0))
    prior_merged = int(pr.get("prior_merged", 0))
    merge_rate = float(pr.get("merge_rate", 0.0))

    text = f"""## PR #{pr['number']}: {pr.get('title', '')}

- **Author:** {author} ({prior_prs} prior PRs, {prior_merged} merged, {merge_rate * 100:.1f}% merge rate)
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


def build_prompt(batch: List[dict], feature_spec: List[dict], patterns: List[Dict[str, str]]) -> str:
    ftxt = "\n".join(f"- {f['name']} ({f['type']}/{f['phase']}): {f.get('notes', '')}" for f in feature_spec)
    ptxt = ""
    if patterns:
        ptxt = "\n## Learned Patterns (abstract)\n" + "\n".join(
            f"- PATTERN: {p.get('pattern', '')}\n  WHEN NOT TO APPLY: {p.get('anti_pattern', '')}" for p in patterns
        )
    batch_md = "\n---\n".join(format_pr_for_prompt(pr) for pr in batch)

    return f"""You are analyzing pull requests from an open-source project.
Merge rate is approximately 24%. You do not know outcomes.

## Task A — Merge Prediction
For each PR, predict merged/closed, confidence [0,1], reasoning, and extract all features below.

## Task B — Duplicate Detection
Among PRs in this batch, identify possible duplicate/superseded groups.

## Features ({len(feature_spec)})
{ftxt}
{ptxt}

Output JSON:
{{
  "predictions": [{{"pr_number": 123, "prediction": "merged", "confidence": 0.7, "reasoning": "...", "features": {{}}}}],
  "duplicates": [{{"prs": [123,456], "confidence": 0.6, "evidence": "..."}}]
}}

## PRs
{batch_md}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--prs-per-round", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--sleep-seconds", type=float, default=1.0)
    ap.add_argument("--max-batches", type=int, default=0, help="for test runs")
    ap.add_argument("--dry-run", action="store_true", help="no remote API calls")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    logf = OUT / "execution_log.txt"

    # bootstrap prerequisites
    split = DATA / "split.json"
    if not split.exists():
        run_py("build_split.py", ["--seed", str(args.seed)])

    sample1 = OUT / "round_1_sample.json"
    if not sample1.exists():
        run_py(
            "sample_v2.py",
            ["--seed", str(args.seed), "--rounds", str(args.rounds), "--prs-per-round", str(args.prs_per_round)],
        )
        src_dir = DATA / "bootstrap_v2"
        for r in range(1, args.rounds + 1):
            src = src_dir / f"round_{r}_sample.json"
            dst = OUT / f"round_{r}_sample.json"
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)

    # Prefer enriched (with comments/reviews/files) if available
    enriched_path = DATA / "all_historical_prs_enriched.json"
    base_path = DATA / "all_historical_prs.json"
    prs_path = enriched_path if enriched_path.exists() else base_path
    all_prs = {int(p["number"]): p for p in json.load(prs_path.open())}
    author_stats = compute_author_stats(all_prs)
    feature_spec = json.load(MODEL_SPEC.open())["features"]

    for r in range(args.start_round, args.rounds + 1):
        log_line(logf, f"round {r} start")
        sample = json.load((OUT / f"round_{r}_sample.json").open())

        patterns: List[Dict[str, str]] = []
        if r >= 4:
            ps_path = OUT / "patterns_state.json"
            if ps_path.exists():
                ps = json.load(ps_path.open())
                patterns = [
                    {
                        "pattern": str(p.get("pattern", "")),
                        "anti_pattern": str(p.get("anti_pattern", "")),
                    }
                    for p in ps.get("patterns", [])
                    if p.get("status") in {"active", "revised"} and p.get("pattern")
                ]

        predictions = []
        dedupes = []

        batch_ids = sorted(sample["batch_assignments"].keys(), key=int)
        for bi, bkey in enumerate(batch_ids, start=1):
            if args.max_batches and bi > args.max_batches:
                break
            nums = sample["batch_assignments"][bkey]
            batch_raw = [dict(all_prs[n]) for n in nums]
            for pr in batch_raw:
                st = author_stats.get(int(pr["number"]), {"prior_prs": 0, "prior_merged": 0, "merge_rate": 0.0})
                pr.update(st)
            batch = sanitize_batch(batch_raw)
            prompt = build_prompt(batch, feature_spec, patterns)

            if args.dry_run:
                out = {
                    "predictions": [
                        {
                            "pr_number": n,
                            "prediction": "closed",
                            "confidence": 0.5,
                            "reasoning": "dry-run",
                            "features": {},
                        }
                        for n in nums
                    ],
                    "duplicates": [],
                }
            else:
                out = call_haiku(prompt)

            predictions.extend(out.get("predictions", []))
            dedupes.extend(out.get("duplicates", []))
            log_line(logf, f"round {r} batch {bkey} done predictions={len(out.get('predictions', []))}")
            time.sleep(args.sleep_seconds)

        round_results = {"round": r, "predictions": predictions, "duplicates": dedupes}
        rr_path = OUT / f"round_{r}_results.json"
        json.dump(round_results, rr_path.open("w"), indent=2)

        score_path = OUT / f"round_{r}_scores.json"
        run_py(
            "score_round.py",
            [
                "--results",
                str(rr_path),
                "--sample",
                str(OUT / f"round_{r}_sample.json"),
                "--all-prs",
                str(prs_path),
                "--split",
                str(DATA / "split.json"),
                "--output",
                str(score_path),
            ],
        )

        # extract abstract patterns from errors
        errors_path = OUT / f"round_{r}_errors.json"
        score_payload = json.load(score_path.open())
        json.dump({"errors": score_payload.get("errors", [])}, errors_path.open("w"), indent=2)
        run_py(
            "extract_patterns_v3.py",
            [
                "--errors",
                str(errors_path),
                "--all-prs",
                str(prs_path),
                "--patterns-state",
                str(OUT / "patterns_state.json"),
                "--round",
                str(r),
                "--output",
                str(OUT / "patterns_state.json"),
            ]
            + (["--dry-run"] if args.dry_run else []),
        )

        log_line(logf, f"round {r} complete")

    run_py(
        "consolidate_v2.py",
        [
            "--bootstrap-dir",
            str(OUT),
            "--all-prs",
            str(prs_path),
            "--output",
            str(OUT / "consolidated.json"),
            "--errors-output",
            str(OUT / "errors_persistent.json"),
            "--dedupe-output",
            str(OUT / "dedupe_consolidated.json"),
        ],
    )
    log_line(logf, "bootstrap complete")


if __name__ == "__main__":
    main()
