#!/usr/bin/env python3
"""Post-hoc pattern extraction for v4A (after all rounds complete).

Builds a single patterns_state by replaying extract_patterns_v4.py across
round_N_errors.json files in chronological order.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_py(script_path: Path, args: list[str]) -> None:
    cmd = [sys.executable, str(script_path)] + args
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap-dir", type=Path, required=True)
    ap.add_argument("--all-prs", type=Path, required=True)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--end-round", type=int, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    extractor = scripts / "extract_patterns_v4.py"

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # start from empty state
    if args.output.exists():
        args.output.unlink()

    for r in range(args.start_round, args.end_round + 1):
        err = args.bootstrap_dir / f"round_{r}_errors.json"
        if not err.exists():
            continue

        cmd_args = [
            "--errors", str(err),
            "--all-prs", str(args.all_prs),
            "--patterns-state", str(args.output),
            "--round", str(r),
            "--output", str(args.output),
        ]
        if args.dry_run:
            cmd_args.append("--dry-run")

        run_py(extractor, cmd_args)

    print(f"posthoc patterns written: {args.output}")


if __name__ == "__main__":
    main()
