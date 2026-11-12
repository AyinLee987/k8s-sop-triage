"""Write the experience note a team would append after the L01 incident (the "bad case" appended to the runbook).

  python experiments/cloudopsbench/make_badcase_note.py

Same model and the same "general" instruction as every other note (make_experience.STYLE). The incident text below
is what really happens in L01 on the lab cluster (scenarios/scenarios.yaml): symptom, confirmed cause and the checks
that separate it from the look-alikes. Two styles, as for the other notes: general (exp-uniq-bad) and specific (exp-uniq-bad-s).
Each skill set = exp-uniq (23 notes, one per cause) + the note as note-51.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

INCIDENT = """System: event-ingest service (api x2 replicas -> Redis stream -> worker x1), single Kubernetes namespace
User-reported symptom: The submit-event endpoint became slow and sometimes times out; all pods look normal.
Confirmed root cause: application code regression in the newly released api image (the submit handler now waits for the event to be processed before answering) on Deployment api (fault class: Application Code Defect)

How the expert diagnosed it:
Step 1: list pods and events in the namespace
Output:
All pods Running/Ready, 0 restarts, no Warning events. Only rollout events of Deployment api.

Step 2: read the request logs of both api pods
Output:
Every POST /events returns 202 but elapsed_ms is about 1510-1530 ms on BOTH replicas (a few ms is normal). No errors.

Step 3: check the dependency and the consumer
Output:
worker processes each event in about 301 ms as configured; its Redis round trips take about 1 ms. redis-0 is healthy, no new warnings.

Step 4: check resources and config of Deployment api
Output:
CPU/memory limits and the ConfigMap are unchanged.

Step 5: look at the rollout history of Deployment api
Output:
The image of Deployment api was changed to a new version less than a minute before the latency started. The worker image did not change.

Step 6: roll Deployment api back to the previous image and repeat the same load
Output:
POST /events latency returns to a few ms immediately. The slowdown follows the image version, not the pods, the node or Redis.
"""


def build(style: str, fname: str, set_name: str) -> None:
    target = HERE / "experiences" / "general-extra" / fname
    if not target.exists():
        env = me.dotenv()
        client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
        resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=4000,
                                              messages=[{"role": "system", "content": me.STYLE[style]},
                                                        {"role": "user", "content": INCIDENT}])
        target.write_text(resp.choices[0].message.content.strip() + "\n", encoding="utf-8")
    src, dst = REPO / "holmes/skills/exp-uniq", REPO / "holmes/skills" / set_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    title, _, body = target.read_text(encoding="utf-8").strip().partition("\n")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].rstrip("-")
    out = dst / f"note-51-{slug}"
    out.mkdir(parents=True)
    desc = title.replace('"', "'")
    (out / "SKILL.md").write_text(f'---\nname: note-51-{slug}\ndescription: "Team experience note: {desc}"\n---\n\n# {title}\n\n{body.strip()}\n', encoding="utf-8")
    print(f"--- {set_name}\n" + target.read_text(encoding="utf-8"))


def main() -> None:
    build("general", "badcase-L01.md", "exp-uniq-bad")              # reviewed, reusable principle
    build("specific", "badcase-L01-specific.md", "exp-uniq-bad-s")  # the quick note written right after the incident


if __name__ == "__main__":
    main()
