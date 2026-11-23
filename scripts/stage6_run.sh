#!/usr/bin/env bash
# Stage 6 (docs/STAGE6-plan.md): the four groups on the 12 test scenarios, 5 repeats each, interleaved by repeat.
# Safe to run again after an interruption (reboot, Docker stopped): every finished run is in
# results/holmes_batch/<tag>/runs.jsonl and is skipped; only the run that was in flight is repeated.
#
#   1. .\scripts\Run-Laptop.ps1 -Action check        (PowerShell 7; starts Docker Desktop, waits for the cluster)
#   2. bash scripts/stage6_run.sh                     (Git Bash; about 8 minutes per run)
#
# Progress goes to .local/stage6.log (not in git). One run at a time: Run-Laptop.ps1 refuses to start while
# another runner container is active.
cd "$(dirname "$0")/.." || exit 1
mkdir -p .local
LOG=.local/stage6.log
P="${PWSH:-$LOCALAPPDATA/Microsoft/WindowsApps/pwsh.exe}"
CT="CT01,CT02,CT03,CT04,CT05,CT07,CT08,CT09,CT11,CT12,CT15,CT16"
for r in 1 2 3 4 5; do
  for g in "v2-S0 holmes/skills/svc-sop-v0" "v2-B holmes/skills/xinc-B-cand" "v2-D holmes/skills/xinc-D-cand" "v2-Dds holmes/skills/xinc-D-ds"; do
    set -- $g
    echo "=== $(date '+%m-%d %H:%M') repeat $r group $1" >> "$LOG"
    "$P" -NoProfile -File scripts/Run-Laptop.ps1 -Action batch -Scenarios "$CT" -Repeats "$r" -Skills "$2" -Metrics -MaxSteps 100 -Tag "$1" 2>&1 \
      | grep -E "planned|^\[|tokens,|another run|Error|refusing" >> "$LOG"
  done
done
echo "=== ALL DONE $(date '+%m-%d %H:%M')" >> "$LOG"
