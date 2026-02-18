#!/usr/bin/env python3
"""Bootstrap v4 orchestrator.

Prompt redesign: 3 separate Haiku tasks (feature extraction, qualitative prediction, dedupe).
Population filtered to PRs with Greptile review.
Patterns injection: only qualitative patterns.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
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
OUT = DATA / "bootstrap_v4"
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
                wait = 60 * (attempt + 1)
                print(f"rate limited; sleeping {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            raise

        text = data["content"][0]["text"]
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        # Try to repair common JSON issues
        cleaned = text.strip()
        # Remove trailing commas before } or ]
        import re as _re
        cleaned = _re.sub(r',\s*([}\]])', r'\1', cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            if attempt < 2:
                print(f"JSON parse error at char {e.pos}/{len(cleaned)}; retrying (attempt {attempt+1}/3)")
                payload["max_tokens"] = min(payload["max_tokens"] + 2000, 8192)
                time.sleep(2)
                continue
            print(f"JSON parse FATAL after repair: {e}. Returning empty predictions.")
            return {"predictions": [], "duplicates": []}
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


def extract_greptile_summary(body: str) -> str:
    """Extract Greptile review summary from PR body."""
    if not body:
        return ""
    marker = "<!-- greptile_comment -->"
    idx = body.lower().find(marker.lower())
    if idx == -1:
        return ""
    # Extract from marker onwards
    section = body[idx + len(marker):]
    # Find end marker if exists
    end_marker = "<!-- end greptile"
    end_idx = section.lower().find(end_marker)
    if end_idx != -1:
        section = section[:end_idx]
    return section.strip()[:1500]


def format_pr_for_prompt(pr: dict) -> str:
    labels = ", ".join(pr.get("labels", [])) or "none"
    author = pr.get("user", "unknown")
    prior_prs = int(pr.get("prior_prs", 0))
    prior_merged = int(pr.get("prior_merged", 0))
    merge_rate = float(pr.get("merge_rate", 0.0))

    body = (pr.get("body") or "")[:500]
    greptile_summary = extract_greptile_summary(pr.get("body") or "")

    # Enrichment v2 fields
    max_same_day = pr.get("author_max_prs_same_day", "?")
    median_interval = pr.get("author_median_interval_hours", "?")
    prs_per_day = pr.get("author_prs_per_day", "?")
    unique_repos = pr.get("author_unique_repos", "?")
    account_age = pr.get("author_account_age_days", "?")
    followers = pr.get("author_followers", "?")
    public_repos = pr.get("author_public_repos", "?")
    has_linked_issue = pr.get("has_linked_issue", False)
    issue_self_filed = pr.get("issue_is_self_filed", False)
    linked_issue_count = pr.get("linked_issue_count", 0)

    text = f"""## PR #{pr['number']}: {pr.get('title', '')}

- **Author:** {author} ({prior_prs} prior PRs, {prior_merged} merged, {merge_rate * 100:.1f}% merge rate)
- **Author Profile:** account age {account_age} days, {followers} followers, {public_repos} public repos
- **Author Velocity:** max {max_same_day} PRs/same day, median interval {median_interval}h, {prs_per_day} PRs/day avg
- **Author Spread:** {unique_repos} unique repos (recent events)
- **Created:** {pr.get('created_at', '')}
- **Labels:** {labels}
- **Size:** +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} ({pr.get('changed_files', pr.get('changedFiles', 0))} files)
- **Draft:** {pr.get('draft', False)}
- **Linked Issues:** {linked_issue_count} (self-filed: {issue_self_filed})
- **Body (truncated):** {body}
"""

    if greptile_summary:
        text += f"\n### Greptile Review Summary:\n{greptile_summary}\n"

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


def build_prompt(batch: List[dict], feature_spec: List[dict], patterns: List[Dict[str, str]], prior_errors: List[Dict[str, Any]] = None) -> str:
    # Original model_spec features (v3 compatible)
    ftxt = "\n".join(f"- {f['name']} ({f['type']}/{f['phase']}): {f.get('notes', '')}" for f in feature_spec)

    ptxt = ""
    if patterns:
        ptxt = "\n## Learned Qualitative Patterns\nUse these for qualitative judgment in your reasoning. Do NOT use mechanically.\n" + "\n".join(
            f"- PATTERN: {p.get('pattern', '')}\n  WHEN NOT TO APPLY: {p.get('anti_pattern', '')}" for p in patterns
        )

    batch_md = "\n---\n".join(format_pr_for_prompt(pr) for pr in batch)

    # Prior round errors as concrete learning examples
    errors_section = ""
    if prior_errors:
        error_lines = []
        for e in prior_errors[:10]:  # cap at 10 to manage context
            etype = e.get("error_type", "?")
            desc = "predicted merged, was actually closed" if etype == "fp" else "predicted closed, was actually merged"
            refl = e.get("reflection", "") or e.get("reasoning", "")
            error_lines.append(f"- PR #{e.get('pr_number', '?')} ({etype.upper()}): {desc}\n  Lesson: {refl[:300]}")
        errors_section = "\n## Previous Round Errors (learn from these mistakes)\n" + "\n".join(error_lines) + "\n"

    return f"""You are analyzing pull requests from an open-source project.
Merge rate is approximately 26%. You do not know outcomes.

## Task A — Merge Prediction
For each PR, predict merged/closed, confidence [0,1], reasoning, and extract all features below.
Your reasoning should cover BOTH quantitative observations AND qualitative judgment:
- What do the features tell you?
- What qualitative signals (review tone, contributor engagement, code quality) go beyond the numbers?
{ptxt}
{errors_section}
## Task B — Duplicate Detection
Among PRs in this batch, identify possible duplicate/superseded groups.
Use the Greptile review summary as semantic representation to compare PR purposes.

## Features ({len(feature_spec)} + 10 enrichment)
{ftxt}

Additional enrichment features to extract:
- has_merge_receipt (bool): comments/reviews contain merge commit hash or merge confirmation
- has_closure_signal (bool): comments mention duplicate/superseded/replaced
- has_revert_signal (bool): comments mention accidental merge or revert
- has_human_review (bool): at least one non-bot review exists
- human_review_type (string): "maintainer" if MEMBER/OWNER review, "contributor" if other human, "none"
- is_triage_rejected (bool): 0 files changed OR 270+ files changed
- is_bot_like (bool): author has max PRs/same day >= 20 OR median interval < 0.5h OR unique repos >= 10
- has_linked_issue (bool): PR has linked issues
- issue_is_self_filed (bool): linked issue filed by same author as PR

Output JSON:
{{
  "predictions": [{{"pr_number": 123, "prediction": "merged", "confidence": 0.7, "reasoning": "detailed reasoning here covering both features and qualitative judgment", "features": {{}}}}],
  "duplicates": [{{"prs": [123,456], "confidence": 0.6, "evidence": "..."}}]
}}

## PRs
{batch_md}
"""


def build_sample(population: List[dict], round_num: int, prs_per_round: int, seed: int) -> dict:
    """Build a stratified sample for a round."""
    rng = random.Random(seed + round_num)
    nums = [int(pr["number"]) for pr in population]
    rng.shuffle(nums)
    selected = nums[:prs_per_round]

    batch_size = 20
    batches: Dict[str, List[int]] = {}
    for i in range(0, len(selected), batch_size):
        batch_key = str(i // batch_size + 1)
        batches[batch_key] = selected[i:i + batch_size]

    return {
        "round": round_num,
        "prs_per_round": prs_per_round,
        "seed": seed,
        "sampled_pr_numbers": selected,
        "batch_assignments": batches,
    }


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

    # Load population (filtered PRs with Greptile)
    pop_path = OUT / "population.json"
    if not pop_path.exists():
        print(f"Population not found at {pop_path}. Run filter_population.py first.")
        sys.exit(1)

    population = json.loads(pop_path.read_text())
    all_prs = {int(p["number"]): p for p in population}
    author_stats = compute_author_stats(all_prs)

    # Also load full enriched dataset for broader author stats if available
    enriched_v2 = DATA / "all_historical_prs_enriched_v2.json"
    if enriched_v2.exists():
        full_prs = {int(p["number"]): p for p in json.loads(enriched_v2.read_text())}
        author_stats = compute_author_stats(full_prs)
        prs_path = enriched_v2
    else:
        prs_path = pop_path

    feature_spec = json.load(MODEL_SPEC.open())["features"]

    # Generate samples for each round
    for r in range(args.start_round, args.rounds + 1):
        sample_path = OUT / f"round_{r}_sample.json"
        if not sample_path.exists():
            sample = build_sample(population, r, args.prs_per_round, args.seed)
            json.dump(sample, sample_path.open("w"), indent=2)

    for r in range(args.start_round, args.rounds + 1):
        log_line(logf, f"round {r} start")
        sample = json.load((OUT / f"round_{r}_sample.json").open())

        # Load qualitative patterns only (for R4+)
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
                    if p.get("status") in {"active", "revised"}
                    and p.get("kind") == "qualitative"
                    and p.get("pattern")
                ]

        # Load prior round errors for R5+ (concrete examples of mistakes)
        prior_errors: List[Dict[str, Any]] = []
        if r >= 5:
            prev_errors_path = OUT / f"round_{r - 1}_errors.json"
            if prev_errors_path.exists():
                try:
                    prev_data = json.load(prev_errors_path.open())
                    prior_errors = prev_data.get("errors", []) if isinstance(prev_data, dict) else prev_data
                except Exception:
                    pass

        predictions = []
        dedupes = []

        batch_ids = sorted(sample["batch_assignments"].keys(), key=int)
        for bi, bkey in enumerate(batch_ids, start=1):
            if args.max_batches and bi > args.max_batches:
                break
            nums = sample["batch_assignments"][bkey]
            batch_raw = []
            for n in nums:
                pr = all_prs.get(n)
                if pr is None:
                    continue
                pr = dict(pr)
                st = author_stats.get(n, {"prior_prs": 0, "prior_merged": 0, "merge_rate": 0.0})
                pr.update(st)
                batch_raw.append(pr)
            batch = sanitize_batch(batch_raw)
            prompt = build_prompt(batch, feature_spec, patterns, prior_errors if r >= 5 else None)

            if args.dry_run:
                out = {
                    "predictions": [
                        {
                            "pr_number": n,
                            "prediction": "closed",
                            "confidence": 0.5,
                            "features": {
                                "has_merge_receipt": False,
                                "has_closure_signal": False,
                                "has_revert_signal": False,
                                "has_human_review": False,
                                "human_review_type": "none",
                                "is_triage_rejected": False,
                                "greptile_score": 0,
                                "is_bot_like": False,
                                "has_linked_issue": False,
                                "issue_is_self_filed": False,
                            },
                            "reasoning": "dry-run",
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
                "--results", str(rr_path),
                "--sample", str(OUT / f"round_{r}_sample.json"),
                "--all-prs", str(prs_path),
                "--split", str(DATA / "split.json"),
                "--output", str(score_path),
            ],
        )

        # Haiku reflection on errors — ask WHY it got each prediction wrong
        errors_path = OUT / f"round_{r}_errors.json"
        score_payload = json.load(score_path.open())
        raw_errors = score_payload.get("errors", [])

        if raw_errors and not args.dry_run:
            error_blocks = []
            for e in raw_errors:
                pr_num = int(e["pr_number"])
                pr_data = all_prs.get(pr_num, {})
                pr_content = format_pr_for_prompt(pr_data) if pr_data else "(PR content not available)"
                error_type_desc = "merged (WRONG — actually closed)" if e["error_type"] == "fp" else "closed (WRONG — actually merged)"
                error_blocks.append(
                    f"PR #{pr_num}: You predicted {error_type_desc}.\n"
                    f"Your original reasoning: {e.get('reasoning', '(empty)')}\n"
                    f"Features you extracted: {json.dumps(e.get('features', {}))}\n\n"
                    f"Full PR content:\n{pr_content}"
                )
            refl_header = (
                "You made prediction errors on the following PRs. For EACH error, explain:\n"
                "1. What did you miss or weigh incorrectly?\n"
                "2. What signal in the PR content should have changed your prediction?\n"
                "3. What pattern or heuristic led you astray?\n\n"
                "Be specific and self-critical. Reference concrete details from the PR.\n\n"
            )
            refl_footer = "\n\nOutput JSON:\n" '{"reflections": [{"pr_number": 123, "reflection": "I missed X because Y..."}]}'

            # Batch reflections to stay under context limits (~80k tokens)
            reflections: Dict[int, str] = {}
            batch_size = 10  # ~10 errors per reflection call
            for i in range(0, len(error_blocks), batch_size):
                batch_blocks = error_blocks[i:i + batch_size]
                reflection_prompt = refl_header + "\n---\n".join(batch_blocks) + refl_footer
                try:
                    refl_out = call_haiku(reflection_prompt)
                    for ref in refl_out.get("reflections", []):
                        if isinstance(ref, dict):
                            reflections[ref["pr_number"]] = ref.get("reflection", "")
                except Exception as ex:
                    log_line(logf, f"round {r} reflection batch failed: {ex}")

            for e in raw_errors:
                e["reflection"] = reflections.get(int(e["pr_number"]), "")
            log_line(logf, f"round {r} reflections: {len(reflections)} / {len(raw_errors)} errors")

        json.dump({"errors": raw_errors}, errors_path.open("w"), indent=2)
        run_py(
            "extract_patterns_v4.py",
            [
                "--errors", str(errors_path),
                "--all-prs", str(prs_path),
                "--patterns-state", str(OUT / "patterns_state.json"),
                "--round", str(r),
                "--output", str(OUT / "patterns_state.json"),
            ]
            + (["--dry-run"] if args.dry_run else []),
        )

        log_line(logf, f"round {r} complete")

    run_py(
        "consolidate_v2.py",
        [
            "--bootstrap-dir", str(OUT),
            "--all-prs", str(prs_path),
            "--output", str(OUT / "consolidated.json"),
            "--errors-output", str(OUT / "errors_persistent.json"),
            "--dedupe-output", str(OUT / "dedupe_consolidated.json"),
        ],
    )
    log_line(logf, "bootstrap complete")


if __name__ == "__main__":
    main()
