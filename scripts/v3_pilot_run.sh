#!/usr/bin/env bash
# Small pilot of the third bank (docs/V3.md, docs/NEXT.md): five validation scenarios, the v3 and the v2 prompt, 2 repeats
# each, 20 runs. Interleaved by repeat and prompt so both prompts see the same hours of the day.
# Approved by the user on 2026-11-24 at about 8 yuan, stop at 12 yuan. These runs are not part of any evaluation baseline.
#
#   1. .\scripts\Run-Laptop.ps1 -Action check        (PowerShell 7; starts Docker Desktop, waits for the cluster)
#   2. bash scripts/v3_pilot_run.sh                   (Git Bash; about 15 minutes per run)
#
# Safe to run again after an interruption: finished runs are in results/holmes_batch/<tag>/runs.jsonl and are skipped.
# To stop: touch .local/v3-pilot.stop (no new batch starts), then stop the runner container
#   docker stop $(docker ps -q --filter "ancestor=k8s-sop-runner")
# Progress goes to .local/v3-pilot.log (not in git).
cd "$(dirname "$0")/.." || exit 1
mkdir -p .local
LOG=.local/v3-pilot.log
P="${PWSH:-$LOCALAPPDATA/Microsoft/WindowsApps/pwsh.exe}"
SC="EV01,EV04,EV03,EV05,EV02"
for r in 1 2; do
  for g in "v3-pilot-p3 v3" "v3-pilot-p2 v2"; do
    set -- $g
    if [ -f .local/v3-pilot.stop ]; then echo "=== stopped by .local/v3-pilot.stop $(date '+%m-%d %H:%M')" >> "$LOG"; exit 0; fi
    echo "=== $(date '+%m-%d %H:%M') repeat $r prompt $2" >> "$LOG"
    "$P" -NoProfile -File scripts/Run-Laptop.ps1 -Action batch -Scenarios "$SC" -Repeats "$r" -Skills holmes/skills/svc-sop-v0 -Metrics -MaxSteps 100 \
      -Prompt "$2" -Tag "$1" 2>&1 | grep -E "planned|^\[|tokens,|another run|Error|refusing|ERROR" >> "$LOG"
  done
done
echo "=== ALL DONE $(date '+%m-%d %H:%M')" >> "$LOG"
