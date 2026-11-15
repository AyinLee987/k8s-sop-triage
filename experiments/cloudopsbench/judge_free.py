"""Grade a --free-answer run: a judge model maps each free-text root cause to one label of the benchmark's list
(or "none"), without seeing the ground truth or which SOP version produced the answer; top-1 = right object + right label.

  python judge_free.py --dataset /path/to/Cloud-OpsBench --tag free-v0 --sample sample_sop_eval.json
"""
import argparse, json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from openai import OpenAI
import make_experience as me

HERE = Path(__file__).parent; REPO = HERE.parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--dataset", required=True); ap.add_argument("--tag", required=True); ap.add_argument("--sample", required=True)
a = ap.parse_args()
src = (Path(a.dataset) / "agents" / "cloudops_agent" / "runtime" / "contracts.py").read_text(encoding="utf-8")
labels = src[src.index('root_cause_list_str = """') + 25:]; labels = labels[: labels.index('"""')].strip()
names = set(re.findall(r"^- (\w+)", labels, re.M))
sp = REPO / "results" / "cloudopsbench" / a.tag / "scores.json"
if not sp.exists():
    sys.exit("run score.py for this tag first")
scores = json.loads(sp.read_text(encoding="utf-8"))
env = me.dotenv()
client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
SYS = ("You map a free-text root-cause statement from a Kubernetes incident diagnosis to a fixed label list. Pick the one "
       "label whose mechanism is what the statement asserts as the root cause. If the statement is too vague to single out "
       "one label, or asserts a mechanism that no label describes, answer none. Do not guess what the truth might be; "
       "judge only what the statement says. Reply with JSON: {\"label\": \"<label or none>\"}.\n\nLabels:\n" + labels)

def judge(row):
    text = ((row.get("pred") or {}).get("root_cause") or "").strip()
    if not text:
        return "none"
    if text in names:
        return text
    r = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=20000,
        messages=[{"role": "system", "content": SYS}, {"role": "user", "content": f"Statement: {text}\nStated fault object: {row['pred'].get('fault_object')}"}])
    t = r.choices[0].message.content or ""
    m = re.search(r'"label"\s*:\s*"(\w+)"', t)
    return m.group(1) if m and (m.group(1) in names) else "none"

rows = scores["rows"]
with ThreadPoolExecutor(8) as ex:
    labs = list(ex.map(judge, rows))
for r, lab in zip(rows, labs):
    r["free_text"] = (r.get("pred") or {}).get("root_cause"); r["judged"] = lab
    r["cause"] = lab == r["truth"]["root_cause"]; r["top1"] = bool(r["object"] and r["cause"])
(sp.parent / "scores_free.json").write_text(json.dumps({"judge": "label mapping, blind to truth and version", "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(a.tag, "top1", sum(r["top1"] for r in rows), "object", sum(bool(r["object"]) for r in rows), "cause", sum(r["cause"] for r in rows), "of", len(rows))
