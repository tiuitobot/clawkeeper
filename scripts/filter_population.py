#!/usr/bin/env python3
"""Filter enriched PRs to those with Greptile review in body.

Output: data/bootstrap_v4/population.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> None:
    ap = argparse.ArgumentParser(description="Filter PRs with Greptile reviews")
    ap.add_argument(
        "--input",
        type=Path,
        default=DATA / "all_historical_prs_enriched_v2.json",
        help="enriched PRs JSON",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=DATA / "bootstrap_v4" / "population.json",
        help="filtered output",
    )
    ap.add_argument("--dry-run", action="store_true", help="print stats only, don't write")
    args = ap.parse_args()

    all_prs = json.loads(args.input.read_text())
    total = len(all_prs)

    filtered = [
        pr for pr in all_prs
        if "greptile" in (pr.get("body") or "").lower()
    ]

    merged = sum(1 for pr in filtered if pr.get("merged_at") or pr.get("merged"))
    print(f"Total PRs: {total}")
    print(f"With Greptile: {len(filtered)} ({100 * len(filtered) / total:.1f}%)" if total else "No PRs")
    print(f"Merge rate (filtered): {100 * merged / len(filtered):.1f}%" if filtered else "No filtered PRs")

    if args.dry_run:
        print("dry-run: not writing output")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(filtered, indent=2))
    print(f"Wrote {args.output} ({len(filtered)} PRs)")


if __name__ == "__main__":
    main()
