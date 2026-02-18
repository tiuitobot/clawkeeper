#!/usr/bin/env python3
"""Stage 0.8: enrich dedupe ground truth with Sonnet 4.5 (no thinking)."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPTS = ROOT / "scripts"
MODEL_ID = "claude-sonnet-4-5-20250514"
FALLBACK_MODEL_ID = "claude-sonnet-4-5"


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


def call_sonnet(prompt: str, max_tokens: int = 2500, retries: int = 3) -> dict:
    token, auth_type = get_token()
    model_id = MODEL_ID
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

    rate_limit_attempt = 0
    while True:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read())
            text = data["content"][0]["text"]
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1].split("```", 1)[0]
            return json.loads(text)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 404 and model_id != FALLBACK_MODEL_ID:
                model_id = FALLBACK_MODEL_ID
                payload["model"] = model_id
                print(f"model {MODEL_ID} not found; falling back to {FALLBACK_MODEL_ID}")
                continue
            if e.code == 429 and rate_limit_attempt < retries:
                rate_limit_attempt += 1
                print(f"rate limited; sleeping 30s (attempt {rate_limit_attempt}/{retries})")
                time.sleep(30)
                continue
            raise RuntimeError(f"Anthropic HTTP {e.code}: {body}")


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


def load_jsonl(path: Path) -> List[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def to_epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def format_batch(batch: List[dict]) -> str:
    parts = []
    for pr in batch:
        parts.append(f"## PR #{pr.get('number')}\n" + json.dumps(pr, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


def build_prompt(batch: List[dict]) -> str:
    return f"""You are enriching dedupe/superseded ground truth for pull requests.

Goal: identify PRs in this batch that are duplicates/superseded variants of the same underlying work item.
You may use all information shown (including outcomes, comments, and timeline).

Return STRICT JSON:
{{
  "pairs": [
    {{"a": 123, "b": 456, "confidence": 0.0-1.0, "reason": "short reason"}}
  ]
}}

Rules:
- Only include pairs with confidence >= 0.65.
- Only include PR numbers present in this batch.
- No prose outside JSON.

Batch PRs:
{format_batch(batch)}
"""


def clusters_from_uf(uf: UnionFind) -> List[List[int]]:
    groups: Dict[int, List[int]] = {}
    for n in uf.parent:
        r = uf.find(n)
        groups.setdefault(r, []).append(n)
    out = [sorted(v) for v in groups.values() if len(v) >= 2]
    out.sort(key=lambda c: (len(c), c[0]))
    return out


def split_with_enriched_clusters(all_prs: List[dict], clusters: List[List[int]], seed: int, train_ratio: float) -> dict:
    # Reuse existing split logic from build_split.py
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from build_split import split_with_cluster_constraint

    split = split_with_cluster_constraint(all_prs, clusters, seed=seed, train_ratio=train_ratio)
    payload = {
        "seed": seed,
        "train_ratio": train_ratio,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dedupe_edges": sum(len(c) * (len(c) - 1) // 2 for c in clusters),
        "dedupe_clusters": clusters,
        "train": split["train"],
        "holdout": split["holdout"],
        "stats": split["stats"],
        "ground_truth_source": "regex+sonnet_enriched_train",
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=Path, default=DATA / "split.json")
    ap.add_argument("--enriched-full", type=Path, default=DATA / "enriched_full.jsonl")
    ap.add_argument("--all-prs", type=Path, default=DATA / "all_historical_prs.json")
    ap.add_argument("--output", type=Path, default=DATA / "dedupe_ground_truth_enriched.json")
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--sleep-seconds", type=float, default=2.0)
    args = ap.parse_args()

    split = json.load(args.split.open())
    train_set = set(int(n) for n in split["train"])

    rows = load_jsonl(args.enriched_full)
    train_prs = [r for r in rows if int(r.get("number")) in train_set]
    train_prs.sort(key=lambda r: (to_epoch(r.get("created_at")), int(r.get("number", 0))))

    uf = UnionFind()
    for cl in split.get("dedupe_clusters", []):
        if not cl:
            continue
        first = int(cl[0])
        uf.add(first)
        for n in cl[1:]:
            uf.add(int(n))
            uf.union(first, int(n))

    detected_pairs: Set[Tuple[int, int]] = set()
    batches = [train_prs[i : i + args.batch_size] for i in range(0, len(train_prs), args.batch_size)]

    for idx, batch in enumerate(batches, start=1):
        nums = {int(pr["number"]) for pr in batch}
        prompt = build_prompt(batch)
        out = call_sonnet(prompt)

        for pair in out.get("pairs", []):
            try:
                a = int(pair.get("a"))
                b = int(pair.get("b"))
                conf = float(pair.get("confidence", 0.0))
            except Exception:
                continue
            if a == b or a not in nums or b not in nums or conf < 0.65:
                continue
            p = (a, b) if a < b else (b, a)
            if p in detected_pairs:
                continue
            detected_pairs.add(p)
            uf.union(a, b)

        print(f"batch {idx}/{len(batches)} done | pairs_total={len(detected_pairs)}")
        time.sleep(args.sleep_seconds)

    clusters = clusters_from_uf(uf)
    enriched_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_ID,
        "train_pr_count": len(train_prs),
        "batch_size": args.batch_size,
        "batches": len(batches),
        "detected_pairs": [{"a": a, "b": b} for a, b in sorted(detected_pairs)],
        "clusters": clusters,
        "base_cluster_count": len(split.get("dedupe_clusters", [])),
        "enriched_cluster_count": len(clusters),
    }
    json.dump(enriched_payload, args.output.open("w"), indent=2)
    print(f"wrote {args.output}")

    all_prs = json.load(args.all_prs.open())
    new_split = split_with_enriched_clusters(all_prs, clusters, seed=args.seed, train_ratio=args.train_ratio)
    json.dump(new_split, args.split.open("w"), indent=2)
    print(f"updated {args.split}")

    import subprocess

    subprocess.run(
        [
            "python3",
            str(SCRIPTS / "sample_v2.py"),
            "--seed",
            str(args.seed),
            "--rounds",
            "10",
            "--prs-per-round",
            "100",
        ],
        check=True,
    )
    print("re-generated round samples via sample_v2.py")


if __name__ == "__main__":
    main()
