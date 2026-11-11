"""Run the Cloud-OpsBench reference agent (unchanged) on the sampled cases with our model, several at a time.

  python experiments/cloudopsbench/run.py --dataset /path/to/Cloud-OpsBench --tag base [--agent cloudops_agent]
         [--sample sample60.json] [--workers 6] [--python /path/to/python]

Each worker uses its own copy of the agent directory (the agent reads one fixed config file), under
<out>/_workers/. The API key is read from this repo's .env, written only into those worker configs, and the
worker copies are deleted at the end. Finished cases are skipped on a rerun. Results: <out>/<tag>/.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

import yaml

REPO = Path(__file__).resolve().parents[2]


def dotenv() -> dict:
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--agent", default="cloudops_agent")
    ap.add_argument("--sample", default=str(Path(__file__).with_name("sample60.json")))
    ap.add_argument("--out", default=str(REPO / "results" / "cloudopsbench"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--max-iterations", type=int, default=20)
    args = ap.parse_args()
    env = dotenv()
    model = env.get("LLM_MODEL", "deepseek-flash")
    dataset, out = Path(args.dataset).resolve(), Path(args.out).resolve() / args.tag
    cases = json.loads(Path(args.sample).read_text(encoding="utf-8"))["cases"]
    workers_root = out.parent / "_workers" / args.tag
    pool: Queue = Queue()
    for i in range(args.workers):
        w = workers_root / f"w{i}"
        if w.exists():
            shutil.rmtree(w)
        shutil.copytree(dataset / "agents" / args.agent, w)
        pool.put(w)

    def result_path(c: dict) -> Path:
        return out / c["system"] / model / c["category"] / c["case"] / "result_raw.json"

    def one(c: dict) -> str:
        if result_path(c).exists():
            return f"skip {c['id']}"
        w = pool.get()
        try:
            cfg_path = w / "configs" / "model_configs.yaml"
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            cfg["model"].update(model=model, api_base=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1",
                                api_key=env["LLM_API_KEY"])
            cfg["diagnosis"].update(system=c["system"], fault_category=c["category"], case_name=c["case"],
                                    dataset_root=str(dataset), save_root=str(out), max_iterations=args.max_iterations)
            cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            started = time.monotonic()
            try:
                p = subprocess.run([args.python, "run.py"], cwd=w, capture_output=True, text=True, timeout=900)
                status = f"exit {p.returncode}"
            except subprocess.TimeoutExpired:
                status = "timeout"
            return f"{status} {c['id']} {time.monotonic() - started:.0f}s"
        finally:
            pool.put(w)

    try:
        with ThreadPoolExecutor(args.workers) as ex:
            for line in ex.map(one, cases):
                print(line, flush=True)
    finally:
        shutil.rmtree(workers_root, ignore_errors=True)  # removes every config that held the key
    return 0


if __name__ == "__main__":
    sys.exit(main())
