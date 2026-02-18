#!/bin/bash
# Pipeline: Stage 0.8 (enrichment) + Bootstrap v2.1
# Runs autonomously, logs to data/bootstrap_v2/pipeline_log.txt
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1

LOG=data/bootstrap_v2/pipeline_log.txt
mkdir -p data/bootstrap_v2

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

# Force bruno-new token (eva-new is rate-limited)
export ANTHROPIC_PROFILE="anthropic:bruno-new"

log "=== Pipeline start ==="

# Stage 0.8: Ground truth enrichment (Sonnet)
log "Stage 0.8: enrich_ground_truth.py starting..."
if python3 scripts/enrich_ground_truth.py \
    --batch-size 25 \
    --seed 42 \
    --sleep-seconds 3 2>&1 | tee -a "$LOG"; then
    log "Stage 0.8: COMPLETE"
else
    log "Stage 0.8: FAILED (exit $?)"
    exit 1
fi

# Bootstrap v2.1: 10 rounds (Haiku)
log "Bootstrap v2.1: bootstrap_v2.py starting..."
if python3 scripts/bootstrap_v2.py \
    --rounds 10 \
    --seed 42 \
    --sleep-seconds 1.5 2>&1 | tee -a "$LOG"; then
    log "Bootstrap v2.1: COMPLETE"
else
    log "Bootstrap v2.1: FAILED (exit $?)"
    exit 1
fi

log "=== Pipeline complete ==="
