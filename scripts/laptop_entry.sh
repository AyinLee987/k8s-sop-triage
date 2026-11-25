#!/usr/bin/env bash
# Runs inside the k8s-sop-runner container (see docker/runner.Dockerfile and scripts/Run-Laptop.ps1).
#   check                      cluster reachable, tests pass, read-only kubeconfig minted
#   pilot  <id> [args...]      one scenario through HolmesGPT
#   batch  [args...]           scripts/holmes_batch.py (default: dev split, 3 repeats)
#   validate <id>              scripts/validate_injection.py (no agent; safe for holdout)
set -euo pipefail
cd /work

# Container paths win over the Windows paths in .env (load_dotenv never overrides the environment).
# Run-Laptop.ps1 passes ADMIN_CONTEXT=kind-<cluster>; the fallback is the laptop's original cluster.
export ADMIN_CONTEXT=${ADMIN_CONTEXT:-kind-tiktok-k8s-lab}
# Everything that belongs to one cluster lives in .local/clusters/<cluster>/ (Run-Laptop.ps1 writes the admin
# kubeconfig there), so runs on two clusters at the same time never overwrite each other's credentials (docs/PARALLEL.md).
CLUSTER_DIR=/work/.local/clusters/${ADMIN_CONTEXT#kind-}
mkdir -p "$CLUSTER_DIR"
export ADMIN_KUBECONFIG=$CLUSTER_DIR/admin-internal.kubeconfig
export LAB_K8S_DIR=/lab/k8s
export DIAG_KUBECONFIG=$CLUSTER_DIR/diag.kubeconfig
export HOLMES_BIN=holmes
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

export METRICS_TOKEN_FILE=$CLUSTER_DIR/metrics.token

mint_diag() { python scripts/make_diag_kubeconfig.py --out "$DIAG_KUBECONFIG" --hours 24 >/dev/null; }
# The account that may read the kubelet's metrics endpoints and nothing else (k8s/rbac-metrics.yaml). Only created when
# a run asks for metrics; the token goes to a file under .local/ (not in git) and is never printed.
mint_metrics() {
  kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" apply -f k8s/rbac-metrics.yaml >/dev/null
  ( umask 077; kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" -n diag-system create token metrics-reader --duration=24h > "$METRICS_TOKEN_FILE" )
}
wants_metrics() { case " $* " in *" --metrics "*) return 0;; *) return 1;; esac; }

cmd=${1:-check}; shift || true
case "$cmd" in
  check)
    kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" get nodes
    python -m pytest -q -p no:cacheprovider
    mint_diag
    if [ -d /lab/k8s ]; then
      kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" -n tiktok-k8s-lab get pods
      python -m triage.runner check-env
    else
      # No lab folder mounted (e.g. the desktop): check-env is about the lab app, so only show Online Boutique.
      echo "no lab app mounted at /lab: skipping the lab checks"
      kubectl --kubeconfig "$ADMIN_KUBECONFIG" --context "$ADMIN_CONTEXT" -n boutique get pods \
        || echo "namespace boutique not deployed yet: run scripts\\Setup-Boutique.ps1"
    fi
    holmes version
    ;;
  pilot)    mint_diag; if wants_metrics "$@"; then mint_metrics; fi; python scripts/holmes_pilot.py "$@" ;;
  batch)    mint_diag; if wants_metrics "$@"; then mint_metrics; fi; python scripts/holmes_batch.py "$@" ;;
  metrics)  mint_diag; mint_metrics; KUBECONFIG="$DIAG_KUBECONFIG" python scripts/pod_metrics.py "$@" ;;
  loop)     mint_diag; if wants_metrics "$@"; then mint_metrics; fi; python scripts/holmes_loop.py "$@" ;;
  fix)      python scripts/fix_records.py "$@" ;;
  anchoring) mint_diag; if wants_metrics "$@"; then mint_metrics; fi; python scripts/anchoring_pilot.py "$@" ;;
  admission) mint_diag; if wants_metrics "$@"; then mint_metrics; fi; python scripts/admission.py run "$@" ;;
  validate) python scripts/validate_injection.py "$@" ;;
  noise)    python scripts/validate_noise.py "$@" ;;
  sweep)    python scripts/sweep_cpu_limit.py "$@" ;;
  shell)    exec bash ;;
  *) echo "unknown command: $cmd (check | pilot | batch | loop | fix | admission | anchoring | metrics | validate | noise | sweep | shell)" >&2; exit 2 ;;
esac
