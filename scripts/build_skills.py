"""Turn sop/*.md into HolmesGPT skills (holmes/skills/sop/<name>/SKILL.md), one skill per SOP file.

  python scripts/build_skills.py

The SOP text is copied unchanged; only a frontmatter (name + description, which HolmesGPT uses to decide
whether to fetch the skill) is added, and references to the .md file names are rewritten to skill names.
Re-run after editing sop/. The generated files are committed so the laptop runner needs no build step.
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "holmes" / "skills" / "sop"

SKILLS = {
    "00-index.md": ("tiktok-lab-triage-overview",
                    "tiktok-k8s-lab 命名空间排查总则：api / worker / redis 的依赖关系、按症状选入口、跨组件跳转原则、配置基线、原因类别。排查这个命名空间的任何问题时先读。"),
    "01-pod-common.md": ("tiktok-lab-pod-common",
                         "tiktok-k8s-lab 通用 Pod 排查步骤：总览、找出异常工作负载、按 Pod 状态分支（镜像拉取、卡在创建、反复重启、运行但不就绪）、全部正常时怎么收尾。"),
    "10-api.md": ("tiktok-lab-api", "tiktok-k8s-lab 的 api 排查：发布卡住、api 重启、api 不就绪、提交事件接口报错（例如 503）。"),
    "20-worker.md": ("tiktok-lab-worker", "tiktok-k8s-lab 的 worker 排查：worker 重启、事件积压、一直 queued、处理变慢、点击数不增长。"),
    "30-redis.md": ("tiktok-lab-redis", "tiktok-k8s-lab 的 Redis 排查：客户端报 Redis 连接、认证、超时、内存错误，或症状直接指向 Redis。"),
}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for fname, (name, desc) in SKILLS.items():
        body = (REPO / "sop" / fname).read_text(encoding="utf-8")
        for other, (other_name, _) in SKILLS.items():
            body = body.replace(f"`{other}`", f"skill `{other_name}`")
        target = OUT / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}", encoding="utf-8")
        print("wrote", target.relative_to(REPO))


if __name__ == "__main__":
    main()
