#!/usr/bin/env bash
# Runs inside the k8s-sop-runner container (see docker/runner.Dockerfile and scripts/Run-Laptop.ps1).
#   check                      cluster reachable, tests pass, read-only kubeconfig minted
#   pilot  <id> [args...]      one scenario through HolmesGPT
#   batch  [args...]           scripts/holmes_batch.py (default: dev split, 3 repeats)
#   validate <id>              scripts/validate_injection.py (no agent; safe for holdout)
set -euo pipefail
cd /work

# Container paths win over the Windows paths in .env (load_dotenv never overrides the environment).
export ADMIN_KUBECONFIG=/work/.local/admin-internal.kubeconfig
export ADMIN_CONTEXT=kind-tiktok-k8s-lab
export LAB_K8S_DIR=/lab/k8s
export DIAG_KUBECONFIG=/work/.local/diag-laptop.kubeconfig
export HOLMES_BIN=holmes
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

mint_diag() { python scripts/make_diag_kubeconfig.py --out "$DIAG_KUBECONFIG" --hours 24 >/dev/null; }

cmd=${1:-check}; shift || true
case "$cmd" in
  check)
    kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" get nodes
    kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" -n tiktok-k8s-lab get pods
    python -m pytest -q -p no:cacheprovider
    mint_diag
    python -m triage.runner check-env
    holmes version
    ;;
  pilot)    mint_diag; python scripts/holmes_pilot.py "$@" ;;
  batch)    mint_diag; python scripts/holmes_batch.py "$@" ;;
  validate) python scripts/validate_injection.py "$@" ;;
  shell)    exec bash ;;
  *) echo "unknown command: $cmd (check | pilot | batch | validate | shell)" >&2; exit 2 ;;
esac
