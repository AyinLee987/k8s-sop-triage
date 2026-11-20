"""Cross-incident learning: turn the investigations of the learning scenarios (BH..) into SOP entries.

  python boutique/reflect.py --mode B --runs results/holmes_loop/loop-v2 --out holmes/skills/xinc-B
  python boutique/reflect.py --mode C --runs results/holmes_loop/loop-v2 --out holmes/skills/xinc-C
  python boutique/reflect.py --mode D --runs results/holmes_loop/loop-v2 --fixes results/fix_records/fix-v1 --out holmes/skills/xinc-D

All modes start from the same SOPs (--base, default svc-sop-v0), use the same model, make one call per incident in the
same order, and never see the real cause or whether the investigation was right. They differ only in the material
(C and D get the same "only what the numbers support" rule, because both have numbers; B has none):
  B  what a one-shot diagnosis leaves behind: the first round's findings, conclusion and proposed fix.
  C  the whole fix-and-verify record: every round's conclusion, the numbers it expected, the actions that were run,
     what users saw afterwards, and whether the round was kept or rolled back.
  D  the one-shot diagnosis of B, plus what the on-call engineer then did (scripts/fix_records.py): each operation as
     it was run and the user-side numbers after it. No reason is given; the engineer may have done something else than
     the agent proposed. This is what production leaves behind when the agent is read-only and people do the fixing.
     (The diagnosis and the fix come from two injections of the same scenario; the incident header uses the fix run's numbers.)
The model does not rewrite SOPs. It returns a few entry-level changes (add / modify / remove) that this program applies
to an "经验条目" section, so one bad reflection cannot wipe or bloat a whole document, and every entry names its source.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "experiments" / "cloudopsbench"))

from triage import remediate  # noqa: E402

SECTION = "## 经验条目"
END = "<!-- 经验条目结束 -->"   # explicit end: whatever follows the section belongs to the original SOP
ITEM = re.compile(r"^- \[E(\d+)\]（(.*?)）(.*)$")
# The model is asked for 500 characters and often writes more (up to 754 in the first runs). Entries used to be cut at
# 500, mid-sentence, in more than half of the cases, usually losing the "what to do next" part. They are now kept whole
# up to MAX_TEXT. --replay rebuilds a library from its log with the current limits, without calling the model.
MAX_CHANGES, ASK_TEXT, MAX_TEXT = 3, 500, 800
LABEL = re.compile(r"^(\[E\d+\]\s*)?([（(]\s*(BH\d+|本次|[、，,~～\-–—\s])+[）)]\s*)?")   # an id or source the model repeated in front of its text

COMMON = ("你维护一套按服务组织的排查 SOP，值班的排障 agent 排查时会读它。下面是一次故障的排查记录。"
          "从中提炼以后排查同类问题能直接用上的经验，写成条目加入 SOP。\n规则：\n"
          "1. 每条写清“看到什么现象 → 怎么判断、下一步查什么”，具体到可以照做；不要写这次故障里的 Pod 名、IP、时间。\n"
          "2. 对整个系统都适用的经验写进 boutique-triage-overview；只和某个服务有关的写进那个服务的 SOP。\n"
          "3. 已有条目能覆盖的，就修改那一条，不要新增重复的；发现已有条目和这次的记录矛盾，可以修改或删除它。\n"
          f"4. 最多 {MAX_CHANGES} 处改动，每条不超过 {ASK_TEXT} 字；没有值得写的就让 changes 为空。\n")
VERIFIED = ("5. 记录里有每一轮的预期、执行的操作、执行后的实际数字。只有被这些结果支持的内容才能写成经验："
            "修复后症状消失的，写成【已证实】；修复后数字没有变化的，写成【已排除】并说明排除了什么；"
            "到最后也没有被结果检验过的猜测，不能写成结论，最多写成【未解决】并说明还缺什么证据。每条以这三个标记之一开头。\n")
ENGINEER = ("5. 记录分两部分：agent 当时的一次性诊断（只读排查，没有人验证过），以及值班工程师之后实际做的每一步操作和每一步之后的用户侧数字。"
            "工程师没有写原因，做的也不一定是 agent 建议的。你要自己对照：哪一步之后哪个数字变了、哪一步之后没有变，这说明了什么；"
            "agent 的诊断哪些和这些结果相符、哪些不符。只有被这些结果支持的内容才能写成经验："
            "某一步之后对应的症状消失的，写成【已证实】；某一步之后数字没有变化的，写成【已排除】并说明排除了什么；"
            "这些数字无法检验的猜测，不能写成结论，最多写成【未解决】并说明还缺什么证据。每条以这三个标记之一开头。\n")
OUTPUT = ('只输出 JSON：{"lesson": "<这次最值得记住的一两句话>", "changes": [{"op": "add", "sop": "<skill 名>", "text": "<条目>"}, '
          '{"op": "modify", "sop": "<skill 名>", "id": "E<编号>", "text": "<新的条目>"}, {"op": "remove", "sop": "<skill 名>", "id": "E<编号>"}]}')


def split(text: str) -> tuple[str, str]:
    _, head, body = text.split("---\n", 2)
    return head, body.strip()


def items_of(body: str) -> list[tuple[str, str, str]]:
    """(id, source, text) of the entries in a SOP body."""
    out = []
    if SECTION in body:
        for line in body.split(SECTION, 1)[1].split(END, 1)[0].splitlines():
            m = ITEM.match(line.strip())
            if m:
                out.append((f"E{m.group(1)}", m.group(2), m.group(3).strip()))
    return out


def with_items(body: str, items: list[tuple[str, str, str]]) -> str:
    """The body with its entries section replaced. The section sits right under the title, where it is read first."""
    if SECTION in body:
        before, rest = body.split(SECTION, 1)
        if END not in rest:
            raise ValueError("entries section without its end marker")
        body = re.sub(r"\n{3,}", "\n\n", before.rstrip() + "\n\n" + rest.split(END, 1)[1].lstrip())
    if not items:
        return body.strip()
    lines = body.strip().split("\n")
    block = [SECTION, "", "以下条目来自过去的排查记录，括号里是来源。", ""] + [f"- [{i}]（{src}）{text}" for i, src, text in items] + ["", END, ""]
    at = 1 if lines and lines[0].startswith("# ") else 0
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines[:at] + [""] + block + lines[at:])).strip()


def apply(lib: dict[str, list], changes: list, source: str) -> list[dict]:
    """Apply the model's changes to {sop: [(id, source, text)]}; anything malformed is skipped and reported."""
    done = []
    for ch in (changes or [])[:MAX_CHANGES]:
        ch = ch if isinstance(ch, dict) else {}
        op, sop = ch.get("op"), ch.get("sop")
        text = LABEL.sub("", " ".join(str(ch.get("text", "")).split()), count=1)[:MAX_TEXT]
        rec = {"change": ch, "applied": False}
        if sop not in lib:
            rec["why"] = f"unknown sop {sop!r}"
        elif op == "add" and text:
            n = 1 + max([int(i[1:]) for i, _, _ in lib[sop]], default=0)
            lib[sop].append((f"E{n}", source, text))
            rec.update(applied=True, id=f"E{n}")
        elif op in ("modify", "remove"):
            idx = next((k for k, (i, _, _) in enumerate(lib[sop]) if i == ch.get("id")), None)
            if idx is None:
                rec["why"] = f"no entry {ch.get('id')!r} in {sop}"
            elif op == "remove":
                rec.update(applied=True, removed=lib[sop].pop(idx)[2])
            elif text:
                old = lib[sop][idx]
                lib[sop][idx] = (old[0], f"{old[1]}、{source}" if source not in old[1] else old[1], text)
                rec.update(applied=True, before=old[2])
            else:
                rec["why"] = "empty text"
        else:
            rec["why"] = f"unknown op {op!r} or empty text"
        done.append(rec)
    for ch in (changes or [])[MAX_CHANGES:]:
        done.append({"change": ch, "applied": False, "why": f"more than {MAX_CHANGES} changes"})
    return done


def material(record: dict, symptom: str, mode: str, cap: int = 3500, fix: dict | None = None) -> str:
    """The incident as the reflecting model sees it. B stops where a one-shot diagnosis stops; D adds the engineer's fix."""
    before = fix["before"] if mode == "D" else record["before"]
    head = f"## 这次故障\n用户反馈：{symptom}\n监控看到的用户侧情况：{remediate.describe(before)}\n"
    rounds = record["rounds"][:1] if mode in ("B", "D") else record["rounds"]
    parts = []
    for r in rounds:
        plan = r.get("plan") or {}
        acts = [json.dumps(a["action"], ensure_ascii=False) for a in r.get("actions") or []] or [json.dumps(a, ensure_ascii=False) for a in plan.get("actions") or []]
        block = [{"B": "### 排查", "C": f"### 第 {r['round']} 轮", "D": "### agent 的一次性诊断（只读，没有验证）"}[mode], f"agent 的分析（节选）：\n{(r.get('answer') or '')[:cap]}",
                 f"结论：{json.dumps(plan.get('root_causes'), ensure_ascii=False)}", f"说明：{plan.get('explanation')}"]
        if mode in ("B", "D"):
            block.append("建议的修复：" + ("；".join(acts) or "（无）"))
        else:
            ran = "\n".join(f"  {json.dumps(a['action'], ensure_ascii=False)} -> {a['result'][:160]}" for a in r.get("actions") or []) or "  （没有申请任何操作）"
            block += [f"预期：{plan.get('expect') or '（没有写）'}", f"执行的操作及结果：\n{ran}"]
            if r.get("observation"):
                kept = {True: "用户侧有好转，改动保留。", False: "用户侧没有好转，改动已撤销。", None: ""}[r.get("kept")]
                block.append(f"执行后用户侧情况：{remediate.describe(r['observation'])} {kept}")
        parts.append("\n".join(block))
    tail = ""
    if mode == "C":
        tail = "\n\n### 结局\n" + {"resolved": "用户侧已恢复正常。", "agent_stopped": "agent 认为现有操作修不了，不再申请操作；用户侧没有恢复。",
                                 "rounds_exhausted": "轮数用完，用户侧没有恢复。"}.get(record.get("outcome"), f"结束状态：{record.get('outcome')}")
    if mode == "D":
        ops = [f"{st['n']}. {st['operation']}\n   之后的用户侧情况：{remediate.describe(st['observation'])}" for st in fix["steps"]]
        tail = ("\n\n### 值班工程师实际做的操作（按时间顺序，每一步之后都重新看了用户侧）\n" + "\n".join(ops) +
                "\n\n### 结局\n" + ("用户侧已恢复正常。" if fix.get("resolved") else "全部操作做完后，用户侧没有完全恢复。"))
    return head + "\n\n" + "\n\n".join(parts) + tail


def library_view(heads: dict, lib: dict) -> str:
    out = ["## 现有 SOP 及其经验条目"]
    for name in lib:
        desc = (yaml.safe_load(heads[name]) or {}).get("description", "")
        out.append(f"【{name}】{desc[:120]}")
        out += [f"  - [{i}]（{src}）{text}" for i, src, text in lib[name]] or ["  （还没有经验条目）"]
    return "\n".join(out)


def replay(out: Path, base: Path) -> None:
    """Apply the logged changes again, in order, to a fresh copy of the base SOPs. Same entries and ids as the run that
    wrote the log; only the limits of apply() differ. The log's own record of what was applied is left as it is."""
    log = json.loads(out.with_suffix(".log.json").read_text(encoding="utf-8"))
    shutil.rmtree(out)
    shutil.copytree(base, out)
    docs = {p.parent.name: split(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/SKILL.md"))}
    lib = {n: items_of(b) for n, (_, b) in docs.items()}
    for entry in log:
        apply(lib, [c["change"] for c in entry["changes"]], entry["scenario"])
    for name, (head, body) in docs.items():
        new = with_items(body, lib[name])
        if new != body:
            (out / name / "SKILL.md").write_text(f"---\n{head}---\n\n{new}\n", encoding="utf-8")
    texts = [t for v in lib.values() for _, _, t in v]
    print(f"{out.name}: {len(texts)} entries, longest {max(map(len, texts))} characters, {sum(len(t) >= MAX_TEXT for t in texts)} cut at {MAX_TEXT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["B", "C", "D"])
    ap.add_argument("--runs", default="", help="directory with <id>.json records written by scripts/holmes_loop.py")
    ap.add_argument("--fixes", default="", help="mode D: directory with <id>.json records written by scripts/fix_records.py")
    ap.add_argument("--out", required=True, help="skill directory to write, e.g. holmes/skills/xinc-C")
    ap.add_argument("--base", default="holmes/skills/svc-sop-v0")
    ap.add_argument("--ids", default=",".join(f"BH{i:02d}" for i in range(1, 16)))
    ap.add_argument("--dry", action="store_true", help="print the first incident's prompt and stop (no model call)")
    ap.add_argument("--replay", action="store_true", help="rebuild --out from --base and the existing log (no model call)")
    args = ap.parse_args()
    if args.replay:
        return replay(REPO / args.out, REPO / args.base)
    from openai import OpenAI
    import make_experience as me
    runs, out, base = REPO / args.runs, REPO / args.out, REPO / args.base
    scenarios = {s["id"]: s for s in yaml.safe_load((REPO / "scenarios" / "boutique_hard.yaml").read_text(encoding="utf-8"))["scenarios"]}
    log_path = out.with_suffix(".log.json")
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    if not out.exists():
        shutil.copytree(base, out)
    docs = {p.parent.name: split(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/SKILL.md"))}
    heads = {n: h for n, (h, _) in docs.items()}
    lib = {n: items_of(b) for n, (_, b) in docs.items()}
    system = COMMON + {"B": "", "C": VERIFIED, "D": ENGINEER}[args.mode] + OUTPUT
    if args.mode == "D" and not args.fixes:
        raise SystemExit("--mode D needs --fixes")
    client = None
    for sid in [i for i in args.ids.split(",") if i]:
        if any(e["scenario"] == sid for e in log):
            continue
        record = json.loads((runs / f"{sid}.json").read_text(encoding="utf-8"))
        if scenarios[sid].get("pool") != "learn":
            raise SystemExit(f"{sid} is not a learning scenario")
        fix = None
        if args.mode == "D":
            fix = json.loads((REPO / args.fixes / f"{sid}.json").read_text(encoding="utf-8"))
            if not fix.get("steps") or fix.get("reset_error"):
                raise SystemExit(f"{sid}: the fix record is incomplete ({fix.get('outcome')}); rerun scripts/fix_records.py {sid}")
        user = library_view(heads, lib) + "\n\n" + material(record, scenarios[sid]["symptom"], args.mode, fix=fix)
        if args.dry:
            print(system, "\n\n", user)
            return
        if client is None:
            env = me.dotenv()
            client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
        r = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=20000,
                                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        t = (r.choices[0].message.content or "").strip()
        data = json.loads(t[t.index("{"): t.rindex("}") + 1])
        applied = apply(lib, data.get("changes"), sid)
        for name, (head, body) in docs.items():
            new = with_items(body, lib[name])
            if new != body:
                docs[name] = (head, new)
                (out / name / "SKILL.md").write_text(f"---\n{head}---\n\n{new}\n", encoding="utf-8")
        # the real cause is logged for the later comparison; it is never part of a prompt
        entry = {"scenario": sid, "mode": args.mode, "lesson": data.get("lesson"), "changes": applied,
                 "prompt_chars": len(user), "tokens": getattr(r.usage, "total_tokens", None), "entries_total": sum(len(v) for v in lib.values()),
                 "truth": scenarios[sid]["answer"].get("explanation")}
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({k: entry[k] for k in ("scenario", "lesson", "entries_total")}, ensure_ascii=False), flush=True)
        for a in applied:
            print("   ", "ok " if a["applied"] else "SKIP", json.dumps(a["change"], ensure_ascii=False)[:260], a.get("why", ""), flush=True)


if __name__ == "__main__":
    main()
