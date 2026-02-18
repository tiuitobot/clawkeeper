#!/usr/bin/env python3
"""Stage 0.7.2-0.7.5 — Run bootstrap round on sample PRs.

Usage:
  python3 scripts/bootstrap_round.py --round 1 [--model haiku] [--limit 5]

Round modes:
  1: Deep review + signal extraction (supervision). Sees outcome.
  2: Predict WITHOUT outcome → compare → errors become signals.
  3-4: Same as 2. Patterns surviving 3+ rounds promote.
  5: Consolidation. Final predictions + logit estimation.

Requires ANTHROPIC_API_KEY in environment.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
BOOTSTRAP_DIR = DATA_DIR / "bootstrap"
SAMPLE_FILE = DATA_DIR / "bootstrap_sample.jsonl"
FEATURE_MAP = Path(__file__).parent.parent / "features" / "feature_map.json"
MODEL_SPEC = Path(__file__).parent.parent / "model_spec.json"

# Model mapping
MODELS = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-5",
}


def load_sample():
    prs = []
    with open(SAMPLE_FILE) as f:
        for line in f:
            prs.append(json.loads(line))
    return prs


def load_prior_round(round_num):
    """Load signals from previous round."""
    prior_file = BOOTSTRAP_DIR / f"round_{round_num}_signals.jsonl"
    if not prior_file.exists():
        return []
    signals = []
    with open(prior_file) as f:
        for line in f:
            signals.append(json.loads(line))
    return signals


def format_pr_for_review(pr, show_outcome=True):
    """Format PR data for LLM review."""
    labels = ", ".join(pr.get("labels", [])) or "none"
    
    text = f"""## PR #{pr['number']}: {pr['title']}

- **Author:** {pr['user']}
- **Created:** {pr['created_at']}
- **Labels:** {labels}
- **Size:** +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} ({pr.get('changed_files', 0)} files)
- **Draft:** {pr.get('draft', False)}
"""
    
    if show_outcome:
        text += f"- **Outcome:** {'MERGED' if pr['merged'] else 'CLOSED (not merged)'}\n"
        if pr.get("merged_at"):
            text += f"- **Merged at:** {pr['merged_at']}\n"
        if pr.get("closed_at"):
            text += f"- **Closed at:** {pr['closed_at']}\n"
    
    # Comments summary
    comments = pr.get("comments", [])
    if comments:
        text += f"\n### Comments ({len(comments)}):\n"
        for c in comments[:10]:  # limit to 10
            author = c.get("user", {}).get("login", "?") if isinstance(c.get("user"), dict) else c.get("user", "?")
            assoc = c.get("author_association", "")
            body = (c.get("body", "") or "")[:200]
            text += f"- **{author}** ({assoc}): {body}\n"
    
    # Reviews summary
    reviews = pr.get("reviews", [])
    if reviews:
        text += f"\n### Reviews ({len(reviews)}):\n"
        for r in reviews[:10]:
            author = r.get("user", {}).get("login", "?") if isinstance(r.get("user"), dict) else r.get("user", "?")
            state = r.get("state", "?")
            body = (r.get("body", "") or "")[:200]
            text += f"- **{author}**: {state} — {body}\n"
    
    # Files
    files = pr.get("files", [])
    if files:
        text += f"\n### Files changed ({len(files)}):\n"
        for f_item in files[:20]:
            fname = f_item.get("filename", "?") if isinstance(f_item, dict) else str(f_item)
            text += f"- {fname}\n"
    
    return text


def build_round1_prompt(prs_text, features_context):
    """Round 1: supervised review. LLM sees outcome."""
    return f"""You are an econometrician analyzing a GitHub repository's pull request governance patterns. 
Your goal is to extract signals that predict whether a PR gets merged or closed.

## Context
This is an open-source project (OpenClaw) with a benevolent dictator model. Merge rate is ~24%.
You are reviewing a stratified sample of 50 PRs to identify predictive signals.

## Feature specification (33 features identified)
{features_context}

## Task (Round 1 — Supervised)
For each PR below, you CAN see the outcome (merged/closed). Extract:
1. **Feature values**: For each of the 33 specified features, extract the value from this PR
2. **Signals**: Any governance-relevant observations (e.g., "maintainer label is strong predictor")
3. **Surprises**: Cases where the outcome is unexpected given the features

Output format (JSON array):
```json
[
  {{
    "pr_number": 123,
    "features": {{"feature_name": "value", ...}},
    "signals": ["signal description", ...],
    "surprise": true/false,
    "surprise_reason": "why unexpected (if surprise=true)"
  }}
]
```

## PRs to review:
{prs_text}
"""


def build_round2_prompt(prs_text, features_context, prior_signals_summary):
    """Round 2+: predict without outcome. Compare later."""
    return f"""You are an econometrician analyzing pull request governance patterns for OpenClaw.
Merge rate is ~24%. You've previously observed these signals:

## Prior signals from Round(s)
{prior_signals_summary}

## Feature specification
{features_context}

## Task (Round 2+ — Prediction)
For each PR below, you CANNOT see the outcome. Predict:
1. **Feature values**: Extract the 33 specified features
2. **Prediction**: Will this PR be merged? (yes/no with confidence 0-1)
3. **Reasoning**: What features drive your prediction?

Output format (JSON array):
```json
[
  {{
    "pr_number": 123,
    "features": {{"feature_name": "value", ...}},
    "prediction": "merged",
    "confidence": 0.7,
    "reasoning": "why"
  }}
]
```

## PRs to review:
{prs_text}
"""


def get_anthropic_token():
    """Get Anthropic OAuth token from OpenClaw auth-profiles."""
    # Check env first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key, "api_key"
    
    # Try OpenClaw auth-profiles
    auth_file = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_file.exists():
        with open(auth_file) as f:
            profiles = json.load(f)
        
        # Prefer eva-new or bruno-new, fallback to openclaw
        for profile_name in ["anthropic:eva-new", "anthropic:bruno-new", "anthropic:openclaw"]:
            p = profiles.get("profiles", {}).get(profile_name, {})
            token = p.get("token") or p.get("access")
            if token:
                print(f"Using token from profile: {profile_name}")
                return token, "oauth"
    
    print("ERROR: No Anthropic token found (set ANTHROPIC_API_KEY or check auth-profiles)")
    sys.exit(1)


def call_anthropic(prompt, model_id, max_tokens=8192):
    """Call Anthropic API directly (supports both API key and OAuth token)."""
    import urllib.request
    
    token, auth_type = get_anthropic_token()
    
    payload = {
        "model": model_id,
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
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers=headers,
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
            text = result["content"][0]["text"]
            usage = result.get("usage", {})
            return text, usage
    except Exception as e:
        print(f"API error: {e}")
        if hasattr(e, "read"):
            print(e.read().decode())
        sys.exit(1)


def run_round(round_num, model, limit=None):
    """Execute a bootstrap round."""
    BOOTSTRAP_DIR.mkdir(exist_ok=True)
    
    prs = load_sample()
    if limit:
        prs = prs[:limit]
    
    print(f"=== Bootstrap Round {round_num} ===")
    print(f"Model: {model}")
    print(f"PRs: {len(prs)}")
    
    # Load feature spec
    with open(MODEL_SPEC) as f:
        spec = json.load(f)
    features_context = "\n".join(
        f"- {feat['name']} ({feat['type']}, {feat['phase']}): {feat.get('notes', '')}"
        for feat in spec["features"]
    )
    
    # Build PR text
    show_outcome = (round_num == 1)
    prs_text = "\n---\n".join(format_pr_for_review(pr, show_outcome=show_outcome) for pr in prs)
    
    # Build prompt
    if round_num == 1:
        prompt = build_round1_prompt(prs_text, features_context)
    else:
        # Load prior signals
        all_prior = []
        for r in range(1, round_num):
            all_prior.extend(load_prior_round(r))
        
        if all_prior:
            # Summarize prior signals
            signals_summary = "\n".join(
                f"- PR#{s.get('pr_number', '?')}: {'; '.join(s.get('signals', [])[:3])}"
                for s in all_prior[:30]
            )
        else:
            signals_summary = "(no prior signals)"
        
        prompt = build_round2_prompt(prs_text, features_context, signals_summary)
    
    # Check prompt size
    prompt_chars = len(prompt)
    prompt_tokens_est = prompt_chars // 4
    print(f"Prompt: ~{prompt_chars:,} chars (~{prompt_tokens_est:,} tokens)")
    
    # Always batch if >5 PRs (output per PR can be 500+ tokens → 50 PRs exceeds 8k output)
    if len(prs) > 5:
        print(f"Prompt too large, batching into groups of 10...")
        all_results = []
        batch_size = 10
        for i in range(0, len(prs), batch_size):
            batch = prs[i:i + batch_size]
            batch_text = "\n---\n".join(format_pr_for_review(pr, show_outcome=show_outcome) for pr in batch)
            
            if round_num == 1:
                batch_prompt = build_round1_prompt(batch_text, features_context)
            else:
                all_prior = []
                for r in range(1, round_num):
                    all_prior.extend(load_prior_round(r))
                signals_summary = "\n".join(
                    f"- PR#{s.get('pr_number', '?')}: {'; '.join(s.get('signals', [])[:3])}"
                    for s in all_prior[:30]
                ) if all_prior else "(no prior signals)"
                batch_prompt = build_round2_prompt(batch_text, features_context, signals_summary)
            
            print(f"\n  Batch {i // batch_size + 1}/{(len(prs) - 1) // batch_size + 1} ({len(batch)} PRs)...")
            response, usage = call_anthropic(batch_prompt, MODELS[model], max_tokens=8192)
            print(f"  Usage: {usage}")
            
            # Parse JSON from response
            try:
                # Extract JSON from markdown code blocks if present
                if "```json" in response:
                    json_text = response.split("```json")[1].split("```")[0]
                elif "```" in response:
                    json_text = response.split("```")[1].split("```")[0]
                else:
                    json_text = response
                batch_results = json.loads(json_text)
                all_results.extend(batch_results)
            except json.JSONDecodeError as e:
                print(f"  WARNING: Could not parse JSON: {e}")
                # Save raw response
                raw_file = BOOTSTRAP_DIR / f"round_{round_num}_batch_{i // batch_size + 1}_raw.txt"
                with open(raw_file, "w") as f:
                    f.write(response)
                print(f"  Raw saved to {raw_file}")
            
            time.sleep(1)  # rate limiting
        
        results = all_results
    else:
        model_id = MODELS[model]
        print(f"\nCalling {model_id}...")
        response, usage = call_anthropic(prompt, model_id, max_tokens=8192)
        print(f"Usage: {usage}")
        
        # Parse JSON
        try:
            if "```json" in response:
                json_text = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_text = response.split("```")[1].split("```")[0]
            else:
                json_text = response
            results = json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"WARNING: Could not parse JSON: {e}")
            raw_file = BOOTSTRAP_DIR / f"round_{round_num}_raw.txt"
            with open(raw_file, "w") as f:
                f.write(response)
            print(f"Raw saved to {raw_file}")
            results = []
    
    # Save results
    output_file = BOOTSTRAP_DIR / f"round_{round_num}_signals.jsonl"
    with open(output_file, "w") as f:
        for entry in results:
            f.write(json.dumps(entry) + "\n")
    
    print(f"\n=== Results ===")
    print(f"Extracted: {len(results)} PR analyses")
    
    if round_num == 1:
        surprises = [r for r in results if r.get("surprise")]
        total_signals = sum(len(r.get("signals", [])) for r in results)
        print(f"Total signals: {total_signals}")
        print(f"Surprises: {len(surprises)}/{len(results)}")
    else:
        # Compare predictions with actual outcomes
        pr_outcomes = {pr["number"]: pr["merged"] for pr in load_sample()}
        correct = 0
        total = 0
        for r in results:
            pn = r.get("pr_number")
            if pn in pr_outcomes:
                predicted = r.get("prediction", "").lower() in ("merged", "yes", "true")
                actual = pr_outcomes[pn]
                if predicted == actual:
                    correct += 1
                total += 1
        if total:
            print(f"Accuracy: {correct}/{total} ({correct / total * 100:.1f}%)")
    
    print(f"Saved to: {output_file}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, required=True, help="Round number (1-5)")
    parser.add_argument("--model", default="haiku", choices=list(MODELS.keys()))
    parser.add_argument("--limit", type=int, help="Limit number of PRs (for testing)")
    args = parser.parse_args()
    
    run_round(args.round, args.model, args.limit)
