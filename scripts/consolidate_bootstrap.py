#!/usr/bin/env python3
"""Stage 0.7.5 — Consolidate bootstrap rounds.

- Extract patterns that survived 3+ prediction rounds (R2-R5)
- Estimate initial logit coefficients from feature × outcome data
- Output: bootstrap_patterns.jsonl, initial_logit.json, learning_curve.json
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
BOOTSTRAP_DIR = DATA_DIR / "bootstrap"
OUTPUT_DIR = DATA_DIR / "bootstrap"


def load_sample():
    prs = {}
    with open(DATA_DIR / "bootstrap_sample.jsonl") as f:
        for line in f:
            pr = json.loads(line)
            prs[str(pr["number"])] = pr
    return prs


def load_round(n):
    path = BOOTSTRAP_DIR / f"round_{n}_signals.jsonl"
    entries = []
    with open(path) as f:
        for line in f:
            entries.append(json.loads(line))
    return entries


def normalize_prediction(pred):
    """Normalize various prediction strings to True/False."""
    if isinstance(pred, bool):
        return pred
    s = str(pred).lower().strip()
    return s in ("merged", "yes", "true", "1")


def build_learning_curve(sample):
    """Build accuracy curve R2→R5."""
    curve = []
    for r in range(2, 6):
        try:
            entries = load_round(r)
        except FileNotFoundError:
            break

        correct = 0
        total = 0
        for e in entries:
            pn = str(e.get("pr_number", ""))
            if pn in sample:
                pred = normalize_prediction(e.get("prediction", ""))
                actual = sample[pn]["merged"]
                if pred == actual:
                    correct += 1
                total += 1

        curve.append({
            "round": r,
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 4) if total else 0,
        })
    return curve


def extract_patterns(sample):
    """Find reasoning themes that appear consistently across rounds.
    
    Strategy: extract keywords from 'reasoning' field across R2-R5.
    Patterns surviving 3+ rounds = candidate rules.
    """
    # Per-PR, collect reasoning across rounds
    pr_reasoning = defaultdict(list)
    for r in range(2, 6):
        try:
            entries = load_round(r)
        except FileNotFoundError:
            break
        for e in entries:
            pn = str(e.get("pr_number", ""))
            reasoning = e.get("reasoning", "")
            if reasoning and pn:
                pr_reasoning[pn].append(reasoning)

    # Per-PR signals from R1
    r1 = load_round(1)
    pr_r1_signals = {}
    for e in r1:
        pn = str(e.get("pr_number", ""))
        pr_r1_signals[pn] = e.get("signals", [])

    # Keyword frequency across predictions (proxy for signal stability)
    governance_keywords = [
        "maintainer", "label", "steipete", "draft", "ci", "test",
        "scope", "size", "engagement", "contributor", "closure",
        "review", "comment", "fork", "revert", "superseded",
        "bot", "approval", "changes_requested", "age",
    ]

    keyword_counts = Counter()
    keyword_correct = Counter()

    for pn, reasonings in pr_reasoning.items():
        merged = sample.get(pn, {}).get("merged", False)
        full_text = " ".join(reasonings).lower()
        for kw in governance_keywords:
            if kw in full_text:
                keyword_counts[kw] += 1
                # Check if predictions for this PR were correct
                correct_preds = 0
                for r in range(2, 6):
                    try:
                        entries = load_round(r)
                    except FileNotFoundError:
                        break
                    for e in entries:
                        if str(e.get("pr_number", "")) == pn:
                            pred = normalize_prediction(e.get("prediction", ""))
                            if pred == merged:
                                correct_preds += 1
                if correct_preds >= 3:
                    keyword_correct[kw] += 1

    patterns = []
    for kw in governance_keywords:
        freq = keyword_counts[kw]
        correct_freq = keyword_correct[kw]
        if freq >= 5:  # appeared in at least 5 PRs
            precision = round(correct_freq / freq, 3) if freq else 0
            patterns.append({
                "signal": kw,
                "frequency": freq,
                "correct_predictions": correct_freq,
                "precision": precision,
                "promoted": precision >= 0.7 and freq >= 8,
            })

    patterns.sort(key=lambda x: (-x["frequency"], -x["precision"]))
    return patterns


def estimate_logit_weights(sample):
    """Simple logit weight estimation from feature presence × outcome.
    
    Uses log-odds approximation:
      weight ≈ log(P(merged|feature=1) / P(closed|feature=1))
               - log(P(merged|feature=0) / P(closed|feature=0))
    
    Only for binary/presence features extractable from R1.
    """
    # Load R1 feature extractions
    r1 = load_round(1)
    
    binary_features = [
        "has_tests", "ci_green", "is_draft", "has_maintainer_label",
        "has_trusted_contributor_label", "has_experienced_contributor_label",
        "has_approval", "has_changes_requested", "has_maintainer_comment",
        "has_top_contributor_comment", "has_greptile_review",
        "touches_multiple_channels", "touches_extensions", "is_fork_pr",
        "high_engagement",
    ]

    feature_stats = defaultdict(lambda: {"merged_1": 0, "closed_1": 0, "merged_0": 0, "closed_0": 0})

    for e in r1:
        pn = str(e.get("pr_number", ""))
        if pn not in sample:
            continue
        merged = sample[pn]["merged"]
        features = e.get("features", {})

        for feat in binary_features:
            val = features.get(feat)
            # Normalize to binary
            present = bool(val) and str(val).lower() not in ("0", "false", "none", "null", "no", "unknown")
            outcome = "merged" if merged else "closed"
            key = f"{outcome}_{'1' if present else '0'}"
            feature_stats[feat][key] += 1

    # Compute log-odds weights
    import math
    weights = {}
    for feat, stats in feature_stats.items():
        m1 = stats["merged_1"] + 0.5  # Laplace smoothing
        c1 = stats["closed_1"] + 0.5
        m0 = stats["merged_0"] + 0.5
        c0 = stats["closed_0"] + 0.5

        # Log-odds ratio
        lor = math.log((m1 / c1) / (m0 / c0))
        weights[feat] = round(lor, 3)

    return dict(sorted(weights.items(), key=lambda x: -abs(x[1])))


def main():
    print("Loading sample...")
    sample = load_sample()
    print(f"  {len(sample)} PRs")

    print("\n=== Learning Curve ===")
    curve = build_learning_curve(sample)
    for c in curve:
        print(f"  R{c['round']}: {c['correct']}/{c['total']} ({c['accuracy']*100:.1f}%)")

    print("\n=== Pattern Extraction ===")
    patterns = extract_patterns(sample)
    promoted = [p for p in patterns if p["promoted"]]
    print(f"  {len(patterns)} signals with freq≥5, {len(promoted)} promoted (precision≥70%, freq≥8)")
    for p in patterns[:15]:
        status = "✅ PROMOTED" if p["promoted"] else "  candidate"
        print(f"  {status}  {p['signal']:30s} freq={p['frequency']:3d} precision={p['precision']:.2f}")

    print("\n=== Initial Logit Weights ===")
    weights = estimate_logit_weights(sample)
    for feat, w in list(weights.items())[:10]:
        direction = "→ MERGE" if w > 0 else "→ CLOSE"
        print(f"  {feat:40s} {w:+.3f}  {direction}")

    # Save outputs
    with open(OUTPUT_DIR / "bootstrap_patterns.jsonl", "w") as f:
        for p in patterns:
            f.write(json.dumps(p) + "\n")

    with open(OUTPUT_DIR / "initial_logit.json", "w") as f:
        json.dump({
            "version": "0.7.5",
            "model": "haiku (bootstrap)",
            "n_rounds": 5,
            "n_prs": len(sample),
            "binary_weights": weights,
            "note": "Log-odds weights from 50-PR bootstrap sample. Superseded by Stage 1 scikit-learn logit on full dataset.",
        }, f, indent=2)

    with open(OUTPUT_DIR / "learning_curve.json", "w") as f:
        json.dump({
            "rounds": curve,
            "note": "R1=supervised, R2-R5=blind prediction. Accuracy measured against actual merge outcomes.",
        }, f, indent=2)

    print(f"\n✅ Saved to data/bootstrap/")
    print(f"   bootstrap_patterns.jsonl ({len(patterns)} patterns, {len(promoted)} promoted)")
    print(f"   initial_logit.json ({len(weights)} binary weights)")
    print(f"   learning_curve.json ({len(curve)} rounds)")


if __name__ == "__main__":
    main()
