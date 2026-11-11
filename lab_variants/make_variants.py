"""Derive the api image variants used by the "slow but healthy" scenarios from the lab's source, without editing it.

  python lab_variants/make_variants.py /path/to/tiktok-k8s-lab /path/to/out

Writes out/v3/app and out/v3.1/app (complete copies of the lab's app/ with small edits):
  v3    adds one log line per request (method, path, status, elapsed_ms; health probes excluded). The lab's api
        logs nothing for a successful request, so without this a slow-but-working service leaves no trace at all.
  v3.1  v3 plus a defect: the submit handler waits for the event to leave the "queued" state before answering
        (up to 1.5 s). It reads like a read-your-writes nicety; under load every submit now takes as long as the
        single worker needs to reach it.
Each edit is anchored on an exact line of the lab's api.py and fails loudly if the lab's source has changed.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REQUEST_LOG = '''
    @app.middleware("http")
    async def request_log(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if not request.url.path.startswith("/health"):
            log("request", elapsed_ms=(time.perf_counter() - started) * 1000, method=request.method,
                path=request.url.path, status=response.status_code)
        return response
'''

WAIT_FOR_PROCESSING = '''        # answer with the final state when it is available quickly, so clients need not poll
        deadline = time.monotonic() + 1.5
        while status == "queued" and time.monotonic() < deadline:
            time.sleep(0.05)
            current = store.event(event_id)
            status = current["status"] if current else status
'''


def edit(text: str, anchor: str, new: str, *, after: bool) -> str:
    if text.count(anchor) != 1:
        sys.exit(f"anchor not found exactly once in api.py: {anchor!r}")
    return text.replace(anchor, anchor + new if after else new + anchor)


def main() -> None:
    lab, out = Path(sys.argv[1]), Path(sys.argv[2])
    base = (lab / "app" / "api.py").read_text(encoding="utf-8")
    v3 = edit(base, "from contextlib import asynccontextmanager\n", "import time\n", after=True)
    v3 = edit(v3, "    app.state.store = store\n", REQUEST_LOG, after=True)
    v31 = edit(v3, "        response.status_code = 200 if result == \"duplicate\" else 202\n", WAIT_FOR_PROCESSING, after=False)
    for name, text in (("v3", v3), ("v3.1", v31)):
        target = out / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(lab / "app", target / "app", ignore=shutil.ignore_patterns("__pycache__"))
        (target / "app" / "api.py").write_text(text, encoding="utf-8")
        compile(text, f"{name}/api.py", "exec")
        print("wrote", target / "app")


if __name__ == "__main__":
    main()
