#!/usr/bin/env python3
"""Stage 0.7.1 — Create stratified sample of 50 PRs for bootstrap rounds.

Stratification axes:
  - outcome: merged vs closed (not merged)
  - size: XS/S/M/L/XL/none (from labels)
  - category: bug/feature/docs/infra/other (from labels)

Target: 50 PRs balanced across axes, preserving real merge rate (~24%).
Output: data/bootstrap_sample.jsonl
"""

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

random.seed(42)  # reproducibility

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT = DATA_DIR / "bootstrap_sample.jsonl"

# --- Load data ---
print("Loading all_historical_prs.json...")
with open(DATA_DIR / "all_historical_prs.json") as f:
    all_prs = json.load(f)

print("Loading enriched_full.jsonl...")
enriched = {}
with open(DATA_DIR / "enriched_full.jsonl") as f:
    for line in f:
        pr = json.loads(line)
        enriched[pr["number"]] = pr

print(f"Total PRs: {len(all_prs)}, Enriched: {len(enriched)}")

# --- Classify each PR ---
SIZE_LABELS = {"size: XS", "size: S", "size: M", "size: L", "size: XL"}
CATEGORY_MAP = {
    "bug": "bug",
    "fix": "bug",
    "feature": "feature",
    "enhancement": "feature",
    "documentation": "docs",
    "docs": "docs",
    "infrastructure": "infra",
    "ci": "infra",
    "build": "infra",
    "chore": "infra",
    "refactor": "infra",
}


def classify_pr(pr):
    labels = [l["name"] if isinstance(l, dict) else l for l in pr.get("labels", [])]
    
    # Outcome
    outcome = "merged" if pr.get("merged_at") else "closed"
    
    # Size
    size = "none"
    for l in labels:
        if l in SIZE_LABELS:
            size = l.split(": ")[1]
            break
    # Fallback: estimate from loc
    if size == "none":
        loc = (pr.get("additions", 0) or 0) + (pr.get("deletions", 0) or 0)
        if loc <= 10:
            size = "XS"
        elif loc <= 50:
            size = "S"
        elif loc <= 200:
            size = "M"
        elif loc <= 500:
            size = "L"
        else:
            size = "XL"
    
    # Category
    category = "other"
    for l in labels:
        l_lower = l.lower()
        for key, cat in CATEGORY_MAP.items():
            if key in l_lower:
                category = cat
                break
        if category != "other":
            break
    
    return outcome, size, category


# --- Stratify ---
strata = defaultdict(list)
for pr in all_prs:
    outcome, size, category = classify_pr(pr)
    strata[(outcome, size, category)].append(pr)

print(f"\nStrata distribution ({len(strata)} groups):")
for key in sorted(strata.keys()):
    print(f"  {key}: {len(strata[key])}")

# --- Sample ---
TARGET = 50
# Preserve approximate merge rate: 12 merged, 38 closed → ~24% merge
# But ensure diversity: at least 1 from each non-empty stratum
MERGE_TARGET = 12
CLOSE_TARGET = 38

selected = []
used_numbers = set()

# Phase 1: 1 from each non-empty stratum (prioritize enriched)
for key, prs in sorted(strata.items()):
    # Prefer enriched PRs
    enriched_prs = [p for p in prs if p["number"] in enriched]
    pool = enriched_prs if enriched_prs else prs
    pick = random.choice(pool)
    selected.append((key, pick))
    used_numbers.add(pick["number"])

print(f"\nPhase 1: {len(selected)} PRs from {len(strata)} strata")

# Phase 2: fill to TARGET maintaining merge rate balance
merged_count = sum(1 for k, _ in selected if k[0] == "merged")
closed_count = sum(1 for k, _ in selected if k[0] == "closed")

# Build remaining pool
remaining_merged = [p for p in all_prs if p.get("merged_at") and p["number"] not in used_numbers and p["number"] in enriched]
remaining_closed = [p for p in all_prs if not p.get("merged_at") and p["number"] not in used_numbers and p["number"] in enriched]

random.shuffle(remaining_merged)
random.shuffle(remaining_closed)

# Fill merged
need_merged = max(0, MERGE_TARGET - merged_count)
for pr in remaining_merged[:need_merged]:
    key = classify_pr(pr)
    selected.append((key, pr))
    used_numbers.add(pr["number"])

# Fill closed
need_closed = max(0, TARGET - len(selected))
for pr in remaining_closed[:need_closed]:
    key = classify_pr(pr)
    selected.append((key, pr))
    used_numbers.add(pr["number"])

# Trim if over target
if len(selected) > TARGET:
    random.shuffle(selected)
    selected = selected[:TARGET]

print(f"Phase 2: {len(selected)} total selected")

# --- Enrich & output ---
output = []
for key, pr in selected:
    number = pr["number"]
    enriched_data = enriched.get(number, {})
    
    entry = {
        "number": number,
        "title": pr.get("title", ""),
        "state": pr.get("state", ""),
        "merged": bool(pr.get("merged_at")),
        "created_at": pr.get("created_at", ""),
        "merged_at": pr.get("merged_at"),
        "closed_at": pr.get("closed_at"),
        "user": pr["user"]["login"] if isinstance(pr.get("user"), dict) else pr.get("user", ""),
        "labels": [l["name"] if isinstance(l, dict) else l for l in pr.get("labels", [])],
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "draft": pr.get("draft", False),
        "stratification": {
            "outcome": key[0],
            "size": key[1],
            "category": key[2],
        },
        # Enriched fields
        "comments": enriched_data.get("comments", []),
        "reviews": enriched_data.get("reviews", []),
        "files": enriched_data.get("files", []),
    }
    output.append(entry)

# Sort by number for reproducibility
output.sort(key=lambda x: x["number"])

# Write
with open(OUTPUT, "w") as f:
    for entry in output:
        f.write(json.dumps(entry) + "\n")

# Stats
merged = sum(1 for e in output if e["merged"])
closed = len(output) - merged
sizes = defaultdict(int)
cats = defaultdict(int)
for e in output:
    sizes[e["stratification"]["size"]] += 1
    cats[e["stratification"]["category"]] += 1

print(f"\n=== Bootstrap Sample ===")
print(f"Total: {len(output)} PRs")
print(f"Merged: {merged} ({merged/len(output)*100:.0f}%), Closed: {closed} ({closed/len(output)*100:.0f}%)")
print(f"Sizes: {dict(sizes)}")
print(f"Categories: {dict(cats)}")
print(f"Enriched: {sum(1 for e in output if e.get('comments'))}/{len(output)}")
print(f"\nWritten to: {OUTPUT}")
