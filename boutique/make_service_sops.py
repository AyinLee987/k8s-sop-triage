"""Write the first version (v0) of the per-service troubleshooting SOPs for Online Boutique, as HolmesGPT skills.

  python boutique/make_service_sops.py [--out holmes/skills/svc-sop-v0]

One skill per service (boutique-<service>) plus an entry skill (boutique-triage-overview). The model sees only
what an SOP author would have before any incident: the release manifest of that service, the call graph derived
from the *_ADDR environment variables, and what the diagnoser's read-only account can do. No scenario, no
answer and no run result is given to it. The interviewer's layout is asked for: a "signal -> step" table, local
checks, and the conditions under which to jump to another service's SOP.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from openai import OpenAI

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "experiments" / "cloudopsbench"))
import make_experience as me  # noqa: E402  (dotenv reader)

MANIFEST = REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml"
TOOLS = ("排障者用的是只读账号：可以看命名空间 boutique 里的 Pod、Pod 日志、事件、Service、EndpointSlice、Deployment、ReplicaSet、"
         "ConfigMap、ResourceQuota；不能进容器执行命令，不能看节点，集群里没有装监控（没有 CPU、内存、延迟指标）。")
SYSTEM = ("你是这个微服务系统的值班负责人，要为其中一个服务写排查 SOP，给以后排障的人（或 AI）按步骤照着做。"
          "只根据给出的清单、调用关系和排障者的权限来写，不要编造这个服务不存在的日志格式、接口或配置项；"
          "不确定的地方写成“先确认……”。用中文，简洁，不要客套话。")
LAYOUT = """按下面的版式写，只输出 SOP 正文（Markdown），不要加前言：

# <服务名> 排查 SOP

适用症状：一行。

## 信号 → 步骤
一张表：左列是能观察到的信号，右列是“去第几步”或“跳到 skill `boutique-<别的服务>`”。

## 步骤
编号步骤。每步写清楚看什么、看到什么说明什么。本服务自身的原因（配置、镜像、资源、探针、它的 Service）在这里确认。

## 什么时候跳到别的服务
逐条写：看到什么现象 → 跳到 skill `boutique-<服务名>`；以及什么情况下应当回到调用方、结论写在谁身上。
只能跳到这个服务实际调用的下游，或回到它的调用方。

## 结论怎么写
两三行：结论里必须有哪个对象、什么原因、哪条证据。"""


def load() -> tuple[dict, dict]:
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if d]
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    svcs = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    return deps, svcs


def call_graph(deps: dict) -> dict[str, list[str]]:
    graph = {}
    for name, d in deps.items():
        out = []
        for c in d["spec"]["template"]["spec"]["containers"]:
            for e in c.get("env", []) or []:
                if e["name"].endswith("_ADDR") and e.get("value"):
                    target = e["value"].split(":")[0]
                    if target in deps and target != name and target not in out:
                        out.append(target)
        graph[name] = out
    return graph


def describe(name: str, deps: dict, svcs: dict) -> str:
    c = deps[name]["spec"]["template"]["spec"]["containers"][0]
    keep = {k: c[k] for k in ("image", "ports", "env", "resources", "readinessProbe", "livenessProbe") if k in c}
    text = "Deployment（副本数 %s）的容器：\n" % deps[name]["spec"].get("replicas", 1) + yaml.safe_dump(keep, allow_unicode=True, sort_keys=False)
    if name in svcs:
        text += "\nService：\n" + yaml.safe_dump({k: svcs[name]["spec"][k] for k in ("type", "selector", "ports")}, allow_unicode=True, sort_keys=False)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "holmes" / "skills" / "svc-sop-v0"))
    args = ap.parse_args()
    deps, svcs = load()
    graph = call_graph(deps)
    services = [n for n in deps if n != "loadgenerator"]
    callers = {n: [a for a in services if n in graph[a]] for n in services}
    graph_text = "\n".join(f"- {a} 调用：{'、'.join(graph[a]) or '（不调用别的服务）'}" for a in services)
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    model = env.get("LLM_MODEL", "deepseek-flash")
    out = Path(args.out)

    def ask(user: str) -> str:
        r = client.chat.completions.create(model=model, temperature=0, max_tokens=60000,
                                           messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        return re.sub(r"^```(?:markdown)?\n|\n```\s*$", "", (r.choices[0].message.content or "").strip())

    def write(skill: str, desc: str, body: str) -> None:
        target = out / skill / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"---\nname: {skill}\ndescription: {desc}\n---\n\n{body.strip()}\n", encoding="utf-8")
        print("wrote", target.relative_to(REPO), len(body), "chars", flush=True)

    for n in services:
        user = (f"系统：Online Boutique（命名空间 boutique），用户从 frontend 进入，另有 loadgenerator 持续产生访问流量。\n\n全系统调用关系：\n{graph_text}\n\n"
                f"要写 SOP 的服务：{n}\n它调用：{'、'.join(graph[n]) or '无'}\n调用它的：{'、'.join(callers[n]) or '只有用户（它是入口）'}\n\n"
                f"它的发布清单：\n{describe(n, deps, svcs)}\n{TOOLS}\n\n{LAYOUT}")
        desc = (f"Online Boutique 的 {n} 排查 SOP：症状涉及 {n}，或从调用方（{'、'.join(callers[n]) or '用户入口'}）查到它时读。"
                f"下游：{'、'.join(graph[n]) or '无'}。")
        write(f"boutique-{n}", desc, ask(user))
    user = ("系统：Online Boutique（命名空间 boutique），用户从 frontend 进入，另有 loadgenerator 持续产生访问流量。\n\n"
            f"全系统调用关系：\n{graph_text}\n\n每个服务都有一份自己的排查 SOP，skill 名是 `boutique-<服务名>`。\n{TOOLS}\n\n"
            "写一份总则（不是某个服务的 SOP），只输出正文（Markdown）：\n# Online Boutique 排查总则\n"
            "包含：调用关系；拿到用户症状后从哪份 SOP 进入；跨服务跳转的原则（什么证据才算可以跳、什么时候停、结论写在哪个服务上）；"
            "多个服务同时异常时怎么判断谁是源头。不要写具体某个服务的检查步骤。")
    write("boutique-triage-overview", "Online Boutique（命名空间 boutique）排查总则：服务调用关系、按症状选入口 SOP、跨服务跳转原则、"
          "怎么判断故障源头。排查这个命名空间的任何问题时先读。", ask(user))


if __name__ == "__main__":
    main()
