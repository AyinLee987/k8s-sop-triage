"""Find a CPU limit that makes a Boutique service slow without making its pod unready (tuning for the cpu scenarios).

  python scripts/sweep_cpu_limit.py [--limits 40m,20m,10m,5m,3m] [--services productcatalogservice,redis-cart,...]

For each service and limit: set limit=request, wait for the rollout, let it settle, load a page that calls the
service 8 times through the API-server proxy, record the timings and the pod's ready/restart state. The page is the
home page, except for services the home page does not call (recommendationservice: a product page).
Pick, per service, the largest limit at which the page is clearly slower than the baseline in most of the 8 loads
while the rollout finished, the pod is ready and has not restarted; put it into CPU_LIMIT in
boutique/make_hard_scenarios.py and regenerate. The baseline values are put
back at the end (and after an interruption). No agent runs; nothing is deleted.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from triage.kube import KubectlRunner  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

NS = "boutique"
import yaml  # noqa: E402

BASE = {d["metadata"]["name"]: (r["limits"]["cpu"], r["requests"]["cpu"])
        for d in yaml.safe_load_all((REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml").read_text(encoding="utf-8")) if d and d["kind"] == "Deployment"
        for r in [d["spec"]["template"]["spec"]["containers"][0]["resources"]]}
HOME = f"/api/v1/namespaces/{NS}/services/frontend:80/proxy/"
PAGE = {"recommendationservice": HOME + "product/OLJCESPC7Z"}   # services the home page does not call


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limits", default="40m,20m,10m,5m,3m")
    ap.add_argument("--services", default="productcatalogservice,currencyservice,cartservice,recommendationservice,redis-cart")
    ap.add_argument("--settle", type=float, default=30)
    args = ap.parse_args()
    load_dotenv()
    k = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))

    def set_cpu(dep: str, limit: str, request: str) -> bool:
        k.run(["-n", NS, "set", "resources", f"deployment/{dep}", f"--limits=cpu={limit}", f"--requests=cpu={request}"], 30)
        return k.run(["-n", NS, "rollout", "status", f"deployment/{dep}", "--timeout=150s"], 160).ok

    def probe(n: int = 8, page: str = HOME) -> list[float]:
        out = []
        for _ in range(n):
            t = time.monotonic()
            ok = k.run(["get", "--raw", page], 40).ok
            out.append(round(time.monotonic() - t, 2) if ok else -1.0)
            time.sleep(2)
        return out

    def pod_state(dep: str) -> str:
        res = k.run(["-n", NS, "get", "pods", "-l", f"app={dep}", "-o",
                     "jsonpath={range .items[*]}{.status.containerStatuses[0].ready}/{.status.containerStatuses[0].restartCount} {end}"], 20)
        return res.stdout.strip() or res.stderr.strip()[:60]

    print("baseline home page:", probe(5), flush=True)
    print("baseline product page:", probe(5, PAGE["recommendationservice"]), flush=True)
    print(f"{'service':24} {'limit':>6} {'rollout':>8} {'ready/restarts':>15}  median  timings (s; -1 = error)", flush=True)
    try:
        for dep in args.services.split(","):
            for lim in args.limits.split(","):
                rolled = set_cpu(dep, lim, lim)
                time.sleep(args.settle)
                times = probe(page=PAGE.get(dep, HOME))
                good = [t for t in times if t >= 0]
                print(f"{dep:24} {lim:>6} {'ok' if rolled else 'TIMEOUT':>8} {pod_state(dep):>15}  "
                      f"{statistics.median(good) if good else -1:>6}  {times}", flush=True)
            print(f"{dep}: back to baseline ->", "ok" if set_cpu(dep, *BASE[dep]) else "ROLLOUT NOT FINISHED", flush=True)
    finally:
        for dep in args.services.split(","):
            set_cpu(dep, *BASE[dep])
    print("after reset:", probe(3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
