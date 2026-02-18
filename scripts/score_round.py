#!/usr/bin/env python3
"""Score one bootstrap-v2 round (merge + dedupe + calibration)."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def f1(p: float, r: float) -> float:
    return safe_div(2 * p * r, p + r) if p + r else 0.0


def pairs_from_cluster(cluster: List[int]) -> Set[Tuple[int, int]]:
    s = sorted(cluster)
    out = set()
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            out.add((s[i], s[j]))
    return out


def calibration(predictions: List[dict], bins: float = 0.1) -> List[dict]:
    buckets = defaultdict(list)
    for p in predictions:
        conf = float(p.get("confidence", 0.0))
        idx = min(int(conf // bins), int(1 / bins) - 1)
        buckets[idx].append(p)

    out = []
    for idx in sorted(buckets):
        rows = buckets[idx]
        avg_conf = sum(float(r.get("confidence", 0.0)) for r in rows) / len(rows)
        acc = sum(1 for r in rows if bool(r.get("correct"))) / len(rows)
        out.append({
            "bin": round(idx * bins, 2),
            "count": len(rows),
            "avg_confidence": round(avg_conf, 6),
            "accuracy": round(acc, 6),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True, help="round_N_results.json")
    ap.add_argument("--sample", type=Path, required=True, help="round_N_sample.json")
    ap.add_argument("--all-prs", type=Path, required=True)
    ap.add_argument("--split", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    results = json.load(args.results.open())
    sample = json.load(args.sample.open())
    all_prs = {int(p["number"]): p for p in json.load(args.all_prs.open())}
    split = json.load(args.split.open())

    sampled = sample["sampled_pr_numbers"]

    gt_merge = {n: bool(all_prs[n].get("merged_at") or all_prs[n].get("merged")) for n in sampled}

    pred_by_pr = {int(p["pr_number"]): p for p in results.get("predictions", [])}

    tp = fp = tn = fn = 0
    merged_predictions = []
    errors = []

    for n in sampled:
        pred = pred_by_pr.get(n, {})
        pred_label = str(pred.get("prediction", "closed")).lower().strip()
        yhat = pred_label == "merged"
        y = gt_merge[n]
        correct = yhat == y

        if yhat and y:
            tp += 1
        elif yhat and not y:
            fp += 1
        elif (not yhat) and (not y):
            tn += 1
        else:
            fn += 1

        row = {
            "pr_number": n,
            "confidence": float(pred.get("confidence", 0.0)),
            "correct": correct,
            "prediction": pred_label,
            "ground_truth": "merged" if y else "closed",
        }
        merged_predictions.append(row)
        if not correct:
            errors.append(
                {
                    "pr_number": n,
                    "error_type": "fp" if yhat and not y else "fn",
                    "features": pred.get("features", {}),
                    "reasoning": pred.get("reasoning", "") or pred.get("qualitative_signals", ""),
                }
            )

    acc = safe_div(tp + tn, tp + tn + fp + fn)
    prec = safe_div(tp, tp + fp)
    rec = safe_div(tp, tp + fn)

    # dedupe scoring inside each round sample
    gt_cluster_pairs: Set[Tuple[int, int]] = set()
    for cl in split.get("dedupe_clusters", []):
        in_sample = sorted([n for n in cl if n in sampled])
        if len(in_sample) >= 2:
            gt_cluster_pairs |= pairs_from_cluster(in_sample)

    pred_pairs: Set[Tuple[int, int]] = set()
    for d in results.get("duplicates", []):
        raw_prs = d.get("prs", [])
        prs = []
        for x in raw_prs:
            try:
                prs.append(int(x))
            except (TypeError, ValueError):
                continue
        prs.sort()
        pred_pairs |= pairs_from_cluster(prs)

    dedupe_tp = len(gt_cluster_pairs & pred_pairs)
    dedupe_fp = len(pred_pairs - gt_cluster_pairs)
    dedupe_fn = len(gt_cluster_pairs - pred_pairs)
    dedupe_p = safe_div(dedupe_tp, dedupe_tp + dedupe_fp)
    dedupe_r = safe_div(dedupe_tp, dedupe_tp + dedupe_fn)

    payload = {
        "round": sample.get("round"),
        "merge": {
            "accuracy": round(acc, 6),
            "precision": round(prec, 6),
            "recall": round(rec, 6),
            "f1": round(f1(prec, rec), 6),
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        },
        "dedupe": {
            "precision": round(dedupe_p, 6),
            "recall": round(dedupe_r, 6),
            "f1": round(f1(dedupe_p, dedupe_r), 6),
            "tp": dedupe_tp,
            "fp": dedupe_fp,
            "fn": dedupe_fn,
            "gt_pairs": len(gt_cluster_pairs),
            "pred_pairs": len(pred_pairs),
        },
        "calibration": calibration(merged_predictions, bins=0.1),
        "errors": errors,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    json.dump(payload, args.output.open("w"), indent=2)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
