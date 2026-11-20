"""Create the diagnoser's read-only identity and write its kubeconfig.

  python scripts/make_diag_kubeconfig.py --out .local/diag.kubeconfig [--admin-kubeconfig PATH] [--hours 24]

Applies k8s/rbac-diag.yaml with ADMIN credentials, mints a short-lived ServiceAccount token, and writes a
kubeconfig that contains only that token. Re-run when the token expires.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from triage.runner import load_dotenv, path_cfg  # noqa: E402

CONTEXT: str | None = None


def kubectl(admin: str | None, *args: str, stdin: str | None = None, check: bool = True) -> str | None:
    cmd = ["kubectl", *(["--kubeconfig", admin] if admin else []), *(["--context", CONTEXT] if CONTEXT else []), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, input=stdin)
    if proc.returncode != 0:
        if not check:
            return None
        sys.exit(f"kubectl {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def apply_existing(admin: str | None, path: Path) -> None:
    """Apply a manifest, leaving out namespaced objects whose namespace is neither on the cluster nor created by
    the same file. rbac-diag.yaml grants read access in tiktok-k8s-lab; a cluster with only Online Boutique (the
    desktop) has no such namespace, and a plain apply would fail on those objects."""
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    created = {d["metadata"]["name"] for d in docs if d.get("kind") == "Namespace"}
    keep = []
    for d in docs:
        ns = (d.get("metadata") or {}).get("namespace")
        if ns and ns not in created and kubectl(admin, "get", "namespace", ns, check=False) is None:
            print(f"skipping {d.get('kind')} {d['metadata'].get('name')} in namespace {ns} (not on this cluster)")
            continue
        keep.append(d)
    kubectl(admin, "apply", "-f", "-", stdin=yaml.safe_dump_all(keep, sort_keys=False))


def main() -> None:
    global CONTEXT
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=path_cfg("DIAG_KUBECONFIG", ".local/diag.kubeconfig"))
    p.add_argument("--admin-kubeconfig", default=path_cfg("ADMIN_KUBECONFIG"))
    p.add_argument("--context", default=os.getenv("ADMIN_CONTEXT") or None)
    p.add_argument("--hours", type=int, default=24)
    a = p.parse_args()
    CONTEXT = a.context

    apply_existing(a.admin_kubeconfig, REPO / "k8s" / "rbac-diag.yaml")
    token = kubectl(a.admin_kubeconfig, "-n", "diag-system", "create", "token", "diag-agent", f"--duration={a.hours}h").strip()
    view = json.loads(kubectl(a.admin_kubeconfig, "config", "view", "--raw", "--minify", "-o", "json"))
    cluster = view["clusters"][0]["cluster"]

    lines = ["apiVersion: v1", "kind: Config", "clusters:", "- name: lab", "  cluster:",
             f"    server: {cluster['server']}"]
    if cluster.get("certificate-authority-data"):
        lines.append(f"    certificate-authority-data: {cluster['certificate-authority-data']}")
    elif cluster.get("certificate-authority"):
        lines.append(f"    certificate-authority: {cluster['certificate-authority']}")
    lines += ["users:", "- name: diag-agent", "  user:", f"    token: {token}",
              "contexts:", "- name: diag", "  context:", "    cluster: lab", "    user: diag-agent", "    namespace: tiktok-k8s-lab",
              "current-context: diag", ""]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    try:
        out.chmod(0o600)
    except OSError:
        pass
    print(f"wrote {out} (token valid {a.hours}h). Verify with: python -m triage.runner check-env")


if __name__ == "__main__":
    main()
