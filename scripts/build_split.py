#!/usr/bin/env python3
"""Build deterministic train/holdout split for Bootstrap v2.

Rules:
- 70/30 split with seed=42
- dedupe clusters are extracted from enriched_full.jsonl comments via regex
- clusters are never split between partitions
- merge-rate difference between partitions must be <= 3pp

Output:
  data/split.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

DEDUPE_REGEX = re.compile(
    r"(?:superseded|duplicate|replaced|favor|instead|closing in favor|superceded|dupe|dup of|same as|covered by|addressed in|fixed in|resolved in|merged in)\s*(?:by|of|in)?\s*#(\d+)",
    re.IGNORECASE,
)


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[int, int] = {}

    def add(self, x: int) -> None:
        if x not in self.parent:
            self.parent[x] = x

    def find(self, x: int) -> int:
        self.add(x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _pr_number(entry: dict) -> int:
    return int(entry.get("number"))


def _merged(entry: dict) -> bool:
    return bool(entry.get("merged_at") or entry.get("merged"))


def load_all_prs(path: Path) -> List[dict]:
    with path.open() as f:
        return json.load(f)


def build_dedupe_clusters(enriched_jsonl: Path, known_prs: Set[int]) -> Tuple[List[List[int]], List[Tuple[int, int]]]:
    uf = UnionFind()
    edges: List[Tuple[int, int]] = []

    with enriched_jsonl.open() as f:
        for line in f:
            pr = json.loads(line)
            src = int(pr.get("number"))
            if src not in known_prs:
                continue
            uf.add(src)
            for c in pr.get("comments", []) or []:
                body = (c or {}).get("body", "") if isinstance(c, dict) else ""
                for m in DEDUPE_REGEX.finditer(body or ""):
                    dst = int(m.group(1))
                    if dst not in known_prs:
                        continue
                    uf.add(dst)
                    uf.union(src, dst)
                    edges.append((src, dst))

    clusters: Dict[int, List[int]] = defaultdict(list)
    for prn in uf.parent:
        clusters[uf.find(prn)].append(prn)

    result = [sorted(v) for v in clusters.values() if len(v) >= 2]
    result.sort(key=lambda c: (len(c), c[0]))
    return result, edges


def split_with_cluster_constraint(
    all_prs: List[dict],
    clusters: List[List[int]],
    seed: int,
    train_ratio: float,
) -> Dict[str, object]:
    rng = random.Random(seed)

    by_number = {_pr_number(pr): pr for pr in all_prs}
    all_numbers = set(by_number.keys())

    cluster_numbers = set(n for cl in clusters for n in cl)
    non_cluster_numbers = sorted(all_numbers - cluster_numbers)

    cluster_payload = []
    for cl in clusters:
        merged_count = sum(1 for n in cl if _merged(by_number[n]))
        cluster_payload.append({"prs": cl, "size": len(cl), "merged": merged_count})

    rng.shuffle(cluster_payload)
    rng.shuffle(non_cluster_numbers)

    train_target = round(len(all_prs) * train_ratio)

    train: Set[int] = set()
    holdout: Set[int] = set()

    # place clusters first (atomic units)
    for item in cluster_payload:
        prs = item["prs"]
        if len(train) < train_target:
            train.update(prs)
        else:
            holdout.update(prs)

    # place non-cluster PRs to balance absolute target
    remaining = [n for n in non_cluster_numbers if n not in train and n not in holdout]
    for n in remaining:
        if len(train) < train_target:
            train.add(n)
        else:
            holdout.add(n)

    # final balancing pass in case cluster step overshot heavily
    if len(train) > train_target:
        overflow = len(train) - train_target
        movable = [n for n in train if n in non_cluster_numbers]
        rng.shuffle(movable)
        for n in movable[:overflow]:
            train.remove(n)
            holdout.add(n)
    elif len(train) < train_target:
        needed = train_target - len(train)
        movable = [n for n in holdout if n in non_cluster_numbers]
        rng.shuffle(movable)
        for n in movable[:needed]:
            holdout.remove(n)
            train.add(n)

    def merge_rate(numbers: Iterable[int]) -> float:
        nums = list(numbers)
        if not nums:
            return 0.0
        return sum(1 for n in nums if _merged(by_number[n])) / len(nums)

    train_rate = merge_rate(train)
    hold_rate = merge_rate(holdout)
    diff = abs(train_rate - hold_rate)

    cluster_index = {n: i for i, cl in enumerate(clusters) for n in cl}
    violated = []
    for cl in clusters:
        in_train = [n for n in cl if n in train]
        in_hold = [n for n in cl if n in holdout]
        if in_train and in_hold:
            violated.append(cl)

    return {
        "train": sorted(train),
        "holdout": sorted(holdout),
        "stats": {
            "total_prs": len(all_prs),
            "train_count": len(train),
            "holdout_count": len(holdout),
            "train_merge_rate": round(train_rate, 6),
            "holdout_merge_rate": round(hold_rate, 6),
            "merge_rate_abs_diff": round(diff, 6),
            "clusters_total": len(clusters),
            "cluster_prs_total": len(cluster_numbers),
            "cluster_split_violations": len(violated),
        },
        "cluster_split_violations": violated,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-prs", type=Path, default=DATA_DIR / "all_historical_prs.json")
    ap.add_argument("--enriched", type=Path, default=DATA_DIR / "enriched_full.jsonl")
    ap.add_argument("--output", type=Path, default=DATA_DIR / "split.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--max-merge-rate-diff", type=float, default=0.03)
    args = ap.parse_args()

    all_prs = load_all_prs(args.all_prs)
    all_numbers = {int(pr["number"]) for pr in all_prs}
    clusters, edges = build_dedupe_clusters(args.enriched, all_numbers)

    split = split_with_cluster_constraint(all_prs, clusters, args.seed, args.train_ratio)
    stats = split["stats"]

    if stats["cluster_split_violations"] != 0:
        raise SystemExit(f"Cluster split violations found: {stats['cluster_split_violations']}")
    if stats["merge_rate_abs_diff"] > args.max_merge_rate_diff:
        raise SystemExit(
            f"Merge-rate diff too high: {stats['merge_rate_abs_diff']:.4f} > {args.max_merge_rate_diff:.4f}"
        )

    payload = {
        "seed": args.seed,
        "train_ratio": args.train_ratio,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regex": DEDUPE_REGEX.pattern,
        "dedupe_edges": len(edges),
        "dedupe_clusters": clusters,
        "train": split["train"],
        "holdout": split["holdout"],
        "stats": stats,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(payload, f, indent=2)

    print(f"wrote {args.output}")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
