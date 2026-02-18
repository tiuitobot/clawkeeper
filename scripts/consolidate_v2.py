#!/usr/bin/env python3
"""Consolidate bootstrap-v2 rounds into final artifacts."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List


def mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def var(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch_t(a: List[float], b: List[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = mean(a), mean(b)
    va, vb = var(a), var(b)
    denom = math.sqrt(va / len(a) + vb / len(b))
    return (mb - ma) / denom if denom else 0.0


def train_logit(round_results: List[dict], all_prs_by_num: Dict[int, dict]):
    try:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression
    except Exception:
        return {"available": False, "weights": []}

    X, y = [], []
    for rr in round_results:
        for p in rr.get("predictions", []):
            n = int(p.get("pr_number"))
            if n not in all_prs_by_num:
                continue
            X.append(p.get("features", {}))
            y.append(1 if bool(all_prs_by_num[n].get("merged_at") or all_prs_by_num[n].get("merged")) else 0)

    if not X:
        return {"available": True, "weights": []}

    vec = DictVectorizer(sparse=True)
    Xv = vec.fit_transform(X)
    if Xv.shape[1] == 0:
        return {"available": True, "weights": [], "note": "No features available in round results."}
    clf = LogisticRegression(max_iter=200, class_weight="balanced")
    clf.fit(Xv, y)

    names = vec.get_feature_names_out()
    coefs = clf.coef_[0]
    ranked = sorted(zip(names, coefs), key=lambda t: abs(t[1]), reverse=True)
    return {
        "available": True,
        "weights": [{"feature": n, "coef": float(c)} for n, c in ranked[:50]],
        "intercept": float(clf.intercept_[0]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap-dir", type=Path, default=Path("data/bootstrap_v2"))
    ap.add_argument("--all-prs", type=Path, default=Path("data/all_historical_prs.json"))
    ap.add_argument("--output", type=Path, default=Path("data/bootstrap_v2/consolidated.json"))
    ap.add_argument("--errors-output", type=Path, default=Path("data/bootstrap_v2/errors_persistent.json"))
    ap.add_argument("--dedupe-output", type=Path, default=Path("data/bootstrap_v2/dedupe_consolidated.json"))
    args = ap.parse_args()

    scores = []
    round_results = []
    persistent = Counter()
    dedupe_rounds = []

    for r in range(1, 11):
        s = args.bootstrap_dir / f"round_{r}_scores.json"
        rr = args.bootstrap_dir / f"round_{r}_results.json"
        if s.exists():
            payload = json.load(s.open())
            scores.append(payload)
            dedupe_rounds.append({"round": r, **payload.get("dedupe", {})})
            for e in payload.get("errors", []):
                persistent[int(e["pr_number"])] += 1
        if rr.exists():
            round_results.append(json.load(rr.open()))

    baseline = [s["merge"]["accuracy"] for s in scores if s.get("round", 0) in (1, 2, 3)]
    learning = [s["merge"]["accuracy"] for s in scores if s.get("round", 0) >= 4]

    patterns = []
    for r in range(1, 11):
        p = args.bootstrap_dir / f"round_{r}_patterns.json"
        if p.exists():
            patterns.extend(json.load(p.open()).get("patterns", []))

    pattern_counts = Counter(p.get("pattern", "") for p in patterns if p.get("pattern"))
    promoted = [pat for pat, c in pattern_counts.items() if c >= 5]

    all_prs = {int(p["number"]): p for p in json.load(args.all_prs.open())}
    logit = train_logit(round_results, all_prs)

    consolidated = {
        "rounds_scored": len(scores),
        "learning_curve": {
            "baseline_rounds_1_3_mean_accuracy": mean(baseline),
            "learning_rounds_4_10_mean_accuracy": mean(learning),
            "delta": mean(learning) - mean(baseline),
            "welch_t_stat": welch_t(baseline, learning),
        },
        "promoted_patterns": promoted,
        "persistent_error_count": sum(1 for _, c in persistent.items() if c >= 3),
        "logit": logit,
    }

    persistent_payload = {
        "persistent_errors": [
            {"pr_number": n, "error_rounds": c}
            for n, c in sorted(persistent.items(), key=lambda x: (-x[1], x[0]))
            if c >= 3
        ]
    }

    dedupe_payload = {
        "rounds": dedupe_rounds,
        "mean_f1": mean([d.get("f1", 0.0) for d in dedupe_rounds]),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    json.dump(consolidated, args.output.open("w"), indent=2)
    json.dump(persistent_payload, args.errors_output.open("w"), indent=2)
    json.dump(dedupe_payload, args.dedupe_output.open("w"), indent=2)
    print(f"wrote {args.output}")
    print(f"wrote {args.errors_output}")
    print(f"wrote {args.dedupe_output}")


if __name__ == "__main__":
    main()
