#!/bin/bash
# Bootstrap v2.1 only (Stage 0.8 already done)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
export ANTHROPIC_PROFILE=anthropic:bruno-new

LOG=data/bootstrap_v2/bootstrap_log.txt
mkdir -p data/bootstrap_v2

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; echo "$*"; }

# Clean previous failed attempt
rm -f data/bootstrap_v2/round_*_results.json \
      data/bootstrap_v2/round_*_scores.json \
      data/bootstrap_v2/round_*_patterns.json \
      data/bootstrap_v2/round_*_errors.json \
      data/bootstrap_v2/consolidated.json \
      data/bootstrap_v2/errors_persistent.json \
      data/bootstrap_v2/dedupe_consolidated.json \
      data/bootstrap_v2/execution_log.txt 2>/dev/null

log "=== Bootstrap v2.1 start ==="
if python3 scripts/bootstrap_v2.py --rounds 10 --seed 42 --sleep-seconds 2; then
    log "=== Bootstrap v2.1 COMPLETE ==="
else
    log "=== Bootstrap v2.1 FAILED (exit $?) ==="
    exit 1
fi
