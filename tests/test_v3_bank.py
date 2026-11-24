"""The third Online Boutique bank (boutique/make_v3_scenarios.py, docs/V3.md) and the v3 prompt (scripts/holmes_pilot.py)."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import BOUTIQUE_DEPLOYMENTS, BoutiqueLifecycle, StepLog, load_boutique_v2, load_boutique_v3, load_scenarios

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "boutique"))
import holmes_pilot  # noqa: E402
from make_v3_scenarios import COUNTABLE  # noqa: E402
from triage import remediate  # noqa: E402

V3 = {s["id"]: s for s in load_boutique_v3()}
CAUSES = {sid: [s["answer"], *s["answer"].get("also", [])] for sid, s in V3.items()}


def test_third_bank_is_split_three_ways_with_its_own_ids():
    pools = {p: [s for s in V3.values() if s["pool"] == p] for p in ("learn", "val", "test")}
    assert [len(pools[p]) for p in ("learn", "val", "test")] == [10, 8, 16]
    assert all(s["id"][:2] == {"learn": "EL", "val": "EV", "test": "ET"}[s["pool"]] and s["bank"] == 3 for s in V3.values())
    every = [s["id"] for s in load_scenarios(split="all", source="all")]
    assert len(every) == len(set(every)), "two banks share an id: the loader would let one shadow the other"
    assert set(V3) <= set(every) and not set(V3) & {s["id"] for s in load_boutique_v2()}


def test_every_scenario_rolls_out_a_batch_and_a_deployment_fault_is_never_the_only_real_change():
    for sid, s in V3.items():
        rel = s["release"]
        assert len(rel["deployments"]) >= 2, sid
        # changes beyond the release tag: the faults on deployments plus the harmless lookalikes
        real = sum(len(ch.get("env") or {}) + bool(ch.get("cpu")) for ch in rel["changes"].values())
        dep_causes = [c for c in CAUSES[sid] if c["object"]["kind"] == "Deployment"]
        if dep_causes:
            assert real >= len(dep_causes) + 1, f"{sid}: the faulty change is the only change besides the tag"
            assert all(c["object"]["name"] in rel["deployments"] for c in dep_causes), sid
        assert s["settle_s"] == 120, sid


def test_the_ticket_never_names_a_cause_and_guesses_are_a_minority():
    guessed = 0
    for sid, s in V3.items():
        for c in CAUSES[sid]:
            assert c["object"]["name"] not in s["symptom"], sid
            assert all(kw.lower() not in s["symptom"].lower() for kw in c["key_evidence"]), sid
        if s.get("ticket_guess"):
            guessed += 1
            assert s["ticket_guess"] in s["symptom"]
    assert 0.2 <= guessed / len(V3) <= 0.45


def test_a_single_cause_of_a_test_scenario_is_never_a_single_cause_of_a_learning_scenario():
    def single(pool):
        return {(c["object"]["name"], c["mechanism"]) for s in V3.values() if s["pool"] == pool and s["root_causes"] == 1 for c in CAUSES[s["id"]]}
    assert not single("test") & single("learn")


def test_every_cause_says_whether_its_mechanism_can_be_told_apart():
    for sid, causes in CAUSES.items():
        for c in causes:
            assert isinstance(c["decidable"], bool) and c["decidable_why"] and c["evidence"], sid
            assert c["mechanism"] in ("network", "cpu", "config", "app"), sid
            if c["mechanism"] == "network":
                assert c["decidable"] == (c["object"]["name"] in COUNTABLE), sid
            else:
                assert c["decidable"], sid
    mixed = [sid for sid, cs in CAUSES.items() if any(c["mechanism"] == "network" and c["decidable"] for c in cs)]
    assert len(mixed) >= 4   # some network faults must be decidable, or "cannot tell" would always be safe on the network


def test_harmless_changes_are_listed_and_the_chain_explains_each():
    for sid, s in V3.items():
        a = s["answer"]
        assert len(a["harmless_changes"]) >= 1, sid
        cause_objects = {c["object"]["name"] for c in CAUSES[sid]}
        assert not cause_objects & {n["name"] for n in a["not_cause"]}, sid
        assert len(s["evidence_chain"]) == len(CAUSES[sid]) + len(a["harmless_changes"]), sid


def _shapes():
    docs = [d for d in yaml.safe_load_all((REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml").read_text(encoding="utf-8")) if d]
    out = {}
    for d in docs:
        name = d["metadata"]["name"]
        if d["kind"] == "Deployment":
            c = d["spec"]["template"]["spec"]["containers"][0]
            out[("deployment", name)] = {"env": {e["name"]: e.get("value") for e in c.get("env", []) or []},
                                         "limit": c["resources"]["limits"]["cpu"], "request": c["resources"]["requests"]["cpu"], "replicas": d["spec"].get("replicas", 1)}
        elif d["kind"] == "Service" and name != "frontend-external":
            out[("service", name)] = {"selector": dict(d["spec"]["selector"])}
    return out


def _apply(state: dict, args: list[str]) -> None:
    """The effect of one inject/reset step on the fields the health check compares."""
    if "patch" in args and args[args.index("patch") + 1] == "deployment":
        d = state[("deployment", args[args.index("patch") + 2])]
        c = json.loads(args[-1])["spec"]["template"]["spec"]["containers"][0]
        for e in c.get("env", []):
            d["env"][e["name"]] = e["value"]
        if "resources" in c:
            d["limit"], d["request"] = c["resources"]["limits"]["cpu"], c["resources"]["requests"]["cpu"]
    elif "patch" in args and args[args.index("patch") + 1] == "service":
        state[("service", args[args.index("patch") + 2])]["selector"] = json.loads(args[-1])["spec"]["selector"]
    elif "set" in args and args[args.index("set") + 1] == "env":
        d = state[("deployment", args[args.index("set") + 2].split("/")[1])]
        for kv in args[args.index("set") + 3:]:
            if kv.endswith("-"):
                d["env"].pop(kv[:-1], None)
            else:
                k, v = kv.split("=", 1)
                d["env"][k] = v
    elif "set" in args and args[args.index("set") + 1] == "resources":
        d = state[("deployment", args[args.index("set") + 2].split("/")[1])]
        d["limit"] = next(a for a in args if a.startswith("--limits=")).split("=")[-1]
        d["request"] = next(a for a in args if a.startswith("--requests=")).split("=")[-1]
    elif "scale" in args:
        state[("deployment", args[args.index("scale") + 1].split("/")[1])]["replicas"] = int(args[-1].split("=")[1])


def test_reset_puts_every_field_back_and_steps_stay_in_the_two_namespaces():
    base = _shapes()
    for sid, s in V3.items():
        state = copy.deepcopy(base)
        for st in s["inject"]:
            if "kubectl" in st:
                _apply(state, st["kubectl"])
        assert state != base, sid
        for st in s["reset"]:
            _apply(state, st["kubectl"])
        assert state == base, (sid, [k for k in base if state[k] != base[k]])
        inject = [st.get("kubectl", []) for st in s["inject"]]
        for args in inject + [st["kubectl"] for st in s["reset"]]:
            if "-n" in args:
                assert args[args.index("-n") + 1] in ("{ns}", "infra-tools"), (sid, args)
            assert "delete" not in args or args[args.index("delete") + 1] == "pod", (sid, args)
            assert all((REPO / a.split("{repo}/")[1]).exists() for a in args if "{repo}/" in a)
        applied = [i for i, args in enumerate(inject) if "apply" in args]
        if applied:   # nothing may recreate pods after the network injector starts
            assert not any({"set", "scale", "patch", "rollout"} & set(args) for args in inject[applied[0]:]), sid


def test_network_delays_differ_and_are_not_multiples_of_the_cfs_period():
    seen = []
    for sid, s in V3.items():
        nets = [c for c in CAUSES[sid] if c["type"] == "net_delay"]
        if not nets:
            continue
        manifest = (REPO / "scenarios" / "netem" / f"injector-boutique-v3-{sid}.yaml").read_text(encoding="utf-8")
        for _svc, centre, jitter in re.findall(r'"([a-z-]+)\|delay (\d+)ms (\d+)ms"', manifest):
            centre, jitter = int(centre), int(jitter)
            assert jitter == round(centre * 0.3) and centre % 100 and centre - jitter >= 110, sid
            seen.append(centre)
    assert len(seen) >= 12 and len(set(seen)) >= 8


def test_paired_scenarios_share_the_cpu_fault():
    for a, b in (("EV01", "EV02"), ("ET05", "ET16"), ("ET06", "ET11"), ("EV08", "EL08")):
        cpu = lambda sid: {c["object"]["name"] for c in CAUSES[sid] if c["mechanism"] == "cpu"}  # noqa: E731
        assert cpu(a) == cpu(b) and len(cpu(a)) == 1 and V3[a]["root_causes"] != V3[b]["root_causes"]


# ---------------- lifecycle ----------------
def _lc():
    k = FakeRunner(lambda args: KubeResult(True, "{}"))
    lc = BoutiqueLifecycle(k, "/lab/k8s")
    lc.health_problems = lambda: []
    return k, lc


def test_third_bank_strips_last_applied_and_waits_for_healthy_traffic(monkeypatch):
    slept = []
    monkeypatch.setattr("triage.lifecycle.time.sleep", lambda s: slept.append(s))
    k, lc = _lc()
    lc.inject(V3["EV01"], StepLog())
    flat = [" ".join(c) for c in k.calls]
    assert any("annotate deployments --all kubectl.kubernetes.io/last-applied-configuration-" in c for c in flat)
    assert any("annotate services --all kubectl.kubernetes.io/last-applied-configuration-" in c for c in flat)
    assert 120 in slept
    first_fault = next(i for i, c in enumerate(flat) if "RELEASE_TAG" in c)
    assert next(i for i, c in enumerate(flat) if "delete pods --all" in c) < first_fault


def test_second_bank_keeps_its_environment(monkeypatch):
    monkeypatch.setattr("triage.lifecycle.time.sleep", lambda s: None)
    k, lc = _lc()
    lc.inject({s["id"]: s for s in load_boutique_v2()}["CV02"], StepLog())
    assert not any("last-applied" in " ".join(c) for c in k.calls)


# ---------------- v3 prompt ----------------
def test_v3_prompt_switches_off_holmes_hedging_blocks_and_keeps_the_skills():
    env, args = holmes_pilot.holmes_overrides("v3")
    parts = set(env["ENABLED_PROMPTS"].split(","))
    assert not parts & {"general_instructions", "style_guide"}
    assert {"time_skills", "toolset_instructions", "system_prompt_additions", "intro", "files"} <= parts
    assert args[0] == "--system-prompt-additions" and "# Investigation guidelines" in args[1]
    assert holmes_pilot.holmes_overrides("v2") == ({}, [])
    assert holmes_pilot.prompt_version(V3["EV01"]) == "v3"
    assert holmes_pilot.prompt_version({s["id"]: s for s in load_boutique_v2()}["CV01"]) == "v2"


def test_v3_user_prompt_asks_to_commit_and_check_every_symptom():
    obs = {"seconds": 60, "requests": 100, "slow_180ms": 5, "share_slow": 0.05, "p50_ms": 9, "p90_ms": 120, "p99_ms": 400,
           "http_500": 0, "checkout_attempts": 3, "checkout_failed": 0, "checkout_p50_ms": 260}
    p = holmes_pilot.build_prompt(V3["EV03"], "boutique", "prod", obs, "v3")
    assert "就把两个都写出来" not in p and "才写分不开" in p
    assert holmes_pilot.CHECK_STEPS in p and '"mechanism"' in p and '"confidence"' in p
    assert "下单请求耗时中位数 260 毫秒" in p
    old = holmes_pilot.build_prompt(V3["EV03"], "boutique", "prod", obs, "v2")
    assert "就把两个都写出来" in old and "下单请求耗时中位数" not in old
    assert "下单请求耗时中位数" not in remediate.describe(obs)


def test_system_v3_differs_from_holmes_only_in_the_hedging_lines():
    try:
        import holmes.plugins.prompts as prompts
    except ImportError:
        pytest.skip("HolmesGPT is not installed here (it is in the runner image)")
    template = (Path(prompts.__file__).parent / "generic_ask.jinja2").read_text(encoding="utf-8")
    blocks = re.findall(r"\{% if (general_instructions_enabled|style_guide_enabled) %\}\n(.*?)\{% endif %\}", template, flags=re.S)
    original = [ln for _, b in blocks for ln in b.splitlines() if ln.strip()]
    ours = [ln for ln in (REPO / "holmes" / "prompts" / "system-v3.md").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(original) == len(ours)
    changed = [(a, b) for a, b in zip(original, ours) if a != b]
    assert len(changed) == 3, changed
    assert all(("hedg" in a or "numbered list" in a or "hypotheses" in a) for a, _ in changed)


def test_test_scenarios_of_the_third_bank_need_finalize():
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "holmes_batch.py"), "--scenarios", "ET01", "--repeats", "1", "--tag", "never"],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode and "refusing the third bank's test scenarios" in res.stderr
    assert not (REPO / "results" / "holmes_batch" / "never").exists()
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "holmes_pilot.py"), "ET01"], capture_output=True, text=True, timeout=120)
    assert res.returncode and "not run until the method is fixed" in res.stderr


def test_every_deployment_named_exists():
    for sid, s in V3.items():
        assert set(s["release"]["deployments"]) <= set(BOUTIQUE_DEPLOYMENTS), sid
