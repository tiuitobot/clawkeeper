#!/usr/bin/env python3
"""Sanitization utilities for Bootstrap v2."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

STRIP_PATTERNS = [
    r"[Mm]erged? (?:via|by|in)\b.*",
    r"[Cc]losing as duplicate of #\d+",
    r"[Ss]uperseded by #\d+",
    r"[Cc]losed? in favor of #\d+",
    r"[Rr]eplaced by #\d+",
    r"[Dd]up(?:licate)? of #\d+",
    r"[Aa]ddressed in #\d+",
    r"[Ff]ixed in #\d+",
    r"[Rr]esolved in #\d+",
    r"CLAWDINATOR.*(?:closing|duplicate|merged).*",
    r"[0-9a-f]{40}",
    r"merged commit [0-9a-f]+",
]
COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in STRIP_PATTERNS]

REMOVED_FIELDS = {"merged", "merged_at", "closed_at", "state"}


def _sanitize_text(text: str) -> str:
    out = text or ""
    for rx in COMPILED:
        out = rx.sub("", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def sanitize_pr(pr: dict) -> dict:
    """Return sanitized copy of PR payload.

    Removes leakage fields and strips outcome-revealing patterns from comments/reviews.
    """
    cleaned = {k: v for k, v in pr.items() if k not in REMOVED_FIELDS}

    comments = []
    for c in pr.get("comments", []) or []:
        if not isinstance(c, dict):
            continue
        c2 = dict(c)
        c2["body"] = _sanitize_text(str(c.get("body", "")))
        if c2["body"]:
            comments.append(c2)
    cleaned["comments"] = comments

    reviews = []
    for r in pr.get("reviews", []) or []:
        if not isinstance(r, dict):
            continue
        r2 = dict(r)
        r2["body"] = _sanitize_text(str(r.get("body", "")))
        reviews.append(r2)
    cleaned["reviews"] = reviews

    return cleaned


def _inline_tests() -> None:
    sample = {
        "number": 1,
        "state": "closed",
        "merged": False,
        "merged_at": None,
        "closed_at": "2026-01-01",
        "comments": [
            {"body": "Closing as duplicate of #4223"},
            {"body": "Landed via temp rebase. merged commit abcdef123456"},
            {"body": "Legit technical discussion survives."},
        ],
        "reviews": [{"body": "Looks good, but superseded by #900"}],
    }
    out = sanitize_pr(sample)
    for f in REMOVED_FIELDS:
        assert f not in out, f"{f} should be removed"
    bodies = [c.get("body", "") for c in out["comments"]]
    assert not any("duplicate" in b.lower() for b in bodies), "duplicate ref should be stripped"
    assert not any("merged" in b.lower() for b in bodies), "merge marker should be stripped"
    assert any("Legit technical discussion survives." in b for b in bodies)
    assert "superseded" not in out["reviews"][0]["body"].lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DATA_DIR / "all_historical_prs.json")
    ap.add_argument("--output", type=Path, help="Optional output json/jsonl")
    ap.add_argument("--jsonl", action="store_true", help="Treat input/output as JSONL")
    ap.add_argument("--run-tests", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.run_tests:
        _inline_tests()
        print("sanitize.py inline tests: OK")
        return

    if args.jsonl:
        rows: List[dict] = []
        with args.input.open() as f:
            for i, line in enumerate(f, start=1):
                if args.limit and i > args.limit:
                    break
                rows.append(sanitize_pr(json.loads(line)))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")
        print(f"sanitized {len(rows)} PRs")
    else:
        data = json.load(args.input.open())
        if args.limit:
            data = data[: args.limit]
        out = [sanitize_pr(pr) for pr in data]
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            json.dump(out, args.output.open("w"), indent=2)
        print(f"sanitized {len(out)} PRs")


if __name__ == "__main__":
    main()
