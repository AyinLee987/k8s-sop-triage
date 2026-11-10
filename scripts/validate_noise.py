"""Check that each ambient noise (scenarios/noise.yaml) leaves the app healthy and removes cleanly. No agent.

  python scripts/validate_noise.py [noise_id ...]        # default: every noise, one at a time, then all together
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import Lifecycle, StepLog, load_noises  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402


def main() -> int:
    load_dotenv()
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    lc = Lifecycle(KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT", "") or None),
                   ns, cfg("LAB_K8S_DIR"))
    wanted = sys.argv[1:] or [n["id"] for n in load_noises()]
    groups = [[i] for i in wanted] + ([wanted] if len(wanted) > 1 else [])
    failed = 0
    for group in groups:
        noises, log, empty = load_noises(group), StepLog(), {"id": "noise-only"}
        try:
            lc.verify_healthy(log)
            lc.inject(empty, log, noises)
            lc.verify_healthy(log, phase="healthy_with_noise", wait_s=60)
            pods = lc.k.run(["-n", ns, "get", "pods", "-o", "wide"], timeout=30).stdout
            print(f"OK   {group}\n{pods}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {group}: {exc}")
        finally:
            lc.reset(empty, log, noises)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
