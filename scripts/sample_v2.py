#!/usr/bin/env python3
"""Round sampler for Bootstrap v2.

- 10 rounds x 100 PRs by default
- directed dedupe injection: same cluster members in same batch of 10
- approximate merge-rate preservation (~24% by default)

Writes:
  data/bootstrap_v2/round_{N}_sample.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = DATA_DIR / "bootstrap_v2"


def load_split(path: Path) -> dict:
    return json.load(path.open())


def load_all(path: Path) -> List[dict]:
    return json.load(path.open())


def sample_round(
    rng: random.Random,
    round_idx: int,
    pool_numbers: Set[int],
    clusters_pool: List[List[int]],
    by_num: Dict[int, dict],
    used_numbers: Set[int],
    prs_per_round: int,
    batches: int,
    dedupe_clusters_target: int,
    merge_target: float,
) -> dict:
    target_merged = round(prs_per_round * merge_target)

    # pick eligible clusters (all members available and unused)
    eligible_clusters = [
        cl for cl in clusters_pool if all((n in pool_numbers and n not in used_numbers) for n in cl)
    ]
    rng.shuffle(eligible_clusters)
    eligible_clusters.sort(key=lambda cl: sum(1 for n in cl if bool(by_num[n].get("merged_at") or by_num[n].get("merged"))) / max(len(cl),1))

    selected_clusters: List[List[int]] = []
    selected_numbers: List[int] = []

    for cl in eligible_clusters:
        if len(selected_clusters) >= dedupe_clusters_target:
            break
        if len(selected_numbers) + len(cl) > prs_per_round:
            continue
        selected_clusters.append(cl)
        selected_numbers.extend(cl)

    # fill remainder from non-cluster pool respecting merge target approximately
    available = [n for n in pool_numbers if n not in used_numbers and n not in selected_numbers]
    merged = [n for n in available if bool(by_num[n].get("merged_at") or by_num[n].get("merged"))]
    closed = [n for n in available if n not in merged]
    rng.shuffle(merged)
    rng.shuffle(closed)

    current_merged = sum(1 for n in selected_numbers if bool(by_num[n].get("merged_at") or by_num[n].get("merged")))
    need_total = prs_per_round - len(selected_numbers)
    need_merged = max(0, min(need_total, target_merged - current_merged))

    picks = merged[:need_merged]
    rest_needed = need_total - len(picks)

    merged_remaining = [n for n in merged if n not in picks]
    # prefer closed to keep merge-rate near target
    fill_pool = closed + merged_remaining
    picks.extend(fill_pool[:rest_needed])

    selected_numbers.extend(picks)
    selected_numbers = selected_numbers[:prs_per_round]

    # build batch assignments with cluster co-location
    batch_assignments: Dict[str, List[int]] = {str(i): [] for i in range(1, batches + 1)}
    cluster_batch_map = {}
    normal_numbers = [n for n in selected_numbers if all(n not in cl for cl in selected_clusters)]

    batch_cursor = 1
    for cl in selected_clusters:
        chunk = list(cl)
        if len(chunk) <= 10:
            while batch_cursor <= batches and len(batch_assignments[str(batch_cursor)]) > 0:
                batch_cursor += 1
            if batch_cursor > batches:
                batch_cursor = 1
            bkey = str(batch_cursor)
            batch_assignments[bkey].extend(chunk)
            cluster_batch_map["-".join(map(str, cl))] = [batch_cursor]
            batch_cursor += 1
        else:
            # very large clusters: split with overlap of 1 PR across sub-batches
            idx = 0
            used_batches = []
            while idx < len(chunk):
                while batch_cursor <= batches and len(batch_assignments[str(batch_cursor)]) > 0:
                    batch_cursor += 1
                if batch_cursor > batches:
                    batch_cursor = 1
                bkey = str(batch_cursor)
                piece = chunk[idx : idx + 10]
                if idx + 10 < len(chunk):
                    # overlap sentinel with next batch
                    nxt = chunk[idx + 9]
                    piece = chunk[idx : idx + 9] + [nxt]
                batch_assignments[bkey].extend(piece)
                used_batches.append(batch_cursor)
                idx += 9
                batch_cursor += 1
            cluster_batch_map["-".join(map(str, cl))] = used_batches

    # fill all batches to size 10 with normals
    rng.shuffle(normal_numbers)
    ptr = 0
    for b in range(1, batches + 1):
        bkey = str(b)
        while len(batch_assignments[bkey]) < 10 and ptr < len(normal_numbers):
            n = normal_numbers[ptr]
            ptr += 1
            if n not in batch_assignments[bkey]:
                batch_assignments[bkey].append(n)

    # if some batches still underfilled, distribute leftovers anywhere not full
    leftovers = normal_numbers[ptr:]
    for n in leftovers:
        for b in range(1, batches + 1):
            bkey = str(b)
            if len(batch_assignments[bkey]) < 10 and n not in batch_assignments[bkey]:
                batch_assignments[bkey].append(n)
                break

    sampled = sorted({n for vals in batch_assignments.values() for n in vals})
    if len(sampled) != prs_per_round:
        raise RuntimeError(f"round {round_idx}: expected {prs_per_round} sampled PRs, got {len(sampled)}")

    for n in sampled:
        used_numbers.add(n)

    merge_rate = sum(1 for n in sampled if bool(by_num[n].get("merged_at") or by_num[n].get("merged"))) / len(sampled)

    return {
        "round": round_idx,
        "sampled_pr_numbers": sampled,
        "dedupe_clusters_selected": selected_clusters,
        "batch_assignments": {k: sorted(v) for k, v in batch_assignments.items()},
        "cluster_batch_map": cluster_batch_map,
        "stats": {
            "sample_size": len(sampled),
            "merge_rate": round(merge_rate, 6),
            "dedupe_cluster_count": len(selected_clusters),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=Path, default=DATA_DIR / "split.json")
    ap.add_argument("--all-prs", type=Path, default=DATA_DIR / "all_historical_prs.json")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--prs-per-round", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--merge-target", type=float, default=0.24)
    ap.add_argument("--dedupe-clusters-per-round", type=int, default=7)
    args = ap.parse_args()

    split = load_split(args.split)
    all_prs = load_all(args.all_prs)
    by_num = {int(pr["number"]): pr for pr in all_prs}

    train_numbers = set(split["train"])
    clusters = [cl for cl in split.get("dedupe_clusters", []) if all(n in train_numbers for n in cl)]

    rng = random.Random(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    used_numbers: Set[int] = set()
    for r in range(1, args.rounds + 1):
        sample = sample_round(
            rng=rng,
            round_idx=r,
            pool_numbers=train_numbers,
            clusters_pool=clusters,
            by_num=by_num,
            used_numbers=used_numbers,
            prs_per_round=args.prs_per_round,
            batches=args.prs_per_round // 10,
            dedupe_clusters_target=args.dedupe_clusters_per_round,
            merge_target=args.merge_target,
        )
        out = OUT_DIR / f"round_{r}_sample.json"
        with out.open("w") as f:
            json.dump(sample, f, indent=2)
        print(f"wrote {out} | merge_rate={sample['stats']['merge_rate']:.3f} | dedupe_clusters={sample['stats']['dedupe_cluster_count']}")


if __name__ == "__main__":
    main()
