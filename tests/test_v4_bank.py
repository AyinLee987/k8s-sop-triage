"""The fourth Online Boutique bank (boutique/make_v4_scenarios.py, docs/V4.md): background workloads, delayed investigation,
network delays chosen so that the probes decide the label, the throttled log-shipper sidecar, and the v4 answer format."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import (AMBIENT_DEPLOYMENTS, AMBIENT_MANIFEST, BoutiqueLifecycle, StepLog, load_boutique_v3, load_boutique_v4,
                              load_scenarios)
from tests.test_v3_bank import _apply, _shapes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "boutique"))
import holmes_pilot  # noqa: E402
from make_v3_scenarios import COUNTABLE  # noqa: E402
from make_v4_scenarios import (DURATION, LEGS, PAIRED, PROBE_DECIDABLE, SIDECAR, SIDECAR_CPU, SIDECAR_NAME, UNDECIDABLE_MAX,  # noqa: E402
                               manifest_facts, probe_kind)
from triage import remediate  # noqa: E402
import pod_metrics  # noqa: E402
import validate_injection  # noqa: E402

V4 = {s["id"]: s for s in load_boutique_v4()}
V3 = {s["id"]: s for s in load_boutique_v3()}
CAUSES = {sid: [s["answer"], *s["answer"].get("also", [])] for sid, s in V4.items()}
PROBES = probe_kind()


def test_fourth_bank_mirrors_the_third_with_its_own_ids():
    assert [sum(s["pool"] == p for s in V4.values()) for p in ("learn", "val", "test")] == [10, 8, 16]
    every = [s["id"] for s in load_scenarios(split="all", source="all")]
    assert len(every) == len(set(every))
    for sid, s in V4.items():
        old = V3["E" + sid[1:]]
        assert sid[0] == "F" and s["bank"] == 4 and s["pool"] == old["pool"]
        assert [(c["object"]["name"], c["mechanism"], c["type"]) for c in CAUSES[sid]] == \
               [(c["object"]["name"], c["mechanism"], c["type"]) for c in [old["answer"], *old["answer"].get("also", [])]]
        # the third bank's harmless changes, except that the sidecar's service carries the sidecar (and the release tag)
        side = SIDECAR[sid]
        assert [h for h in s["answer"]["harmless_changes"] if not (h["object"] == side and SIDECAR_NAME in h["change"])] == \
               [h for h in old["answer"]["harmless_changes"] if not (h["object"] == side and h["change"] == "RELEASE_TAG")]
        assert s["investigate_after_s"] == 600 and s["settle_s"] == 120
        assert s["symptom"] == old["symptom"].replace(old.get("ticket_guess") or "\0", "") + DURATION + (old.get("ticket_guess") or "")
        assert s["release"]["tag"].startswith("2026.11.25-") and "2026.11.24-" not in json.dumps(s["inject"])


def test_network_labels_follow_the_probe_rule_and_avoid_the_grey_zone():
    for sid, causes in CAUSES.items():
        for c in causes:
            if c["mechanism"] != "network":
                continue
            t = c["object"]["name"]
            m = re.search(r"中心 (\d+) 毫秒", c["explanation"])
            if c["type"] == "net_loss":
                assert c["decidable"] and m is None, sid
                continue
            centre = int(m.group(1))
            if t in COUNTABLE:
                assert c["decidable"] and centre <= 285, sid
            elif PROBES[t] == "grpc":
                assert centre <= UNDECIDABLE_MAX or PROBE_DECIDABLE[0] <= centre <= PROBE_DECIDABLE[1], (sid, centre)
                assert c["decidable"] == (centre >= PROBE_DECIDABLE[0]), sid
                # the arithmetic behind the label: three delayed packets per probe, each at most 1.3 x the centre
                assert (LEGS["grpc"] * 1.3 * centre > 1000) == c["decidable"], sid
            else:
                assert PROBES[t] == "tcpSocket" and not c["decidable"], sid
            manifest = (REPO / "scenarios" / "netem" / f"injector-boutique-v4-{sid}.yaml").read_text(encoding="utf-8")
            assert f'"{t}|delay {centre}ms {round(centre * 0.3)}ms"' in manifest, sid
    labels = [c["decidable"] for cs in CAUSES.values() for c in cs if c["mechanism"] == "network"]
    assert 0 < labels.count(False) < labels.count(True)


def test_every_scenario_lists_the_background_workloads_as_non_causes():
    for sid, s in V4.items():
        assert [x["name"] for x in s["answer"]["ambient"]] == list(AMBIENT_DEPLOYMENTS), sid
        assert "ambient" in s["design"] and "delayed" in s["design"]
        assert s["evidence_chain"][-1].startswith("排除后台服务")


def test_reset_still_puts_everything_back():
    base = _shapes()
    for sid, s in V4.items():
        state = copy.deepcopy(base)
        for st in s["inject"] + s["reset"]:
            if "kubectl" in st:
                _apply(state, st["kubectl"])
        assert state == base, sid
        for st in s["inject"]:
            for a in st.get("kubectl", []):
                if "{repo}/" in a:
                    assert (REPO / a.split("{repo}/")[1]).exists() and "-v4-" in a, (sid, a)


def _ambient_secret() -> dict:
    return next(d for d in yaml.safe_load_all(AMBIENT_MANIFEST.read_text(encoding="utf-8")) if d and d["kind"] == "Secret")["stringData"]


def test_every_scenario_adds_one_throttled_sidecar_to_a_service_that_is_not_a_cause():
    facts = manifest_facts()
    assert sorted(SIDECAR) == sorted(V4) and all(SIDECAR[a] == SIDECAR[b] for a, b in PAIRED)
    for sid, s in V4.items():
        side = SIDECAR[sid]
        steps = [(st["kubectl"][4], json.loads(st["kubectl"][-1])["spec"]["template"]["spec"]) for st in s["inject"]
                 if st.get("kubectl", [])[2:4] == ["patch", "deployment"] and SIDECAR_NAME in st["kubectl"][-1]]
        assert [d for d, _ in steps] == [side], sid
        pod = steps[0][1]
        main, extra = pod["containers"]
        assert main["name"] == facts["main"][side] and "resources" not in main, sid   # the service's own container keeps its limit
        assert extra["name"] == SIDECAR_NAME and extra["resources"]["limits"]["cpu"] == SIDECAR_CPU and extra["imagePullPolicy"] == "IfNotPresent"
        assert extra["image"].endswith("/loadgenerator:v0.10.3") and extra["securityContext"]["readOnlyRootFilesystem"], sid
        assert not re.search(r"\b(noise|decoy|chaos|inject|fault|fake|ambient|red.?herring|harmless|trap)\b", json.dumps(pod).lower()), sid
        vol = pod["volumes"][0]["secret"]
        assert vol["secretName"] == "ops-jobs" and [i["key"] for i in vol["items"]] == ["log_shipper.py"] and "log_shipper.py" in _ambient_secret()
        # where it may not go: a cause, a direct caller of a service with a network fault, the guessed service, frontend, redis-cart
        nets = [c["object"]["name"] for c in CAUSES[sid] if c["mechanism"] == "network"]
        assert side not in {c["object"]["name"] for c in CAUSES[sid]} | {"frontend", "redis-cart", "loadgenerator"}, sid
        assert not any(side in facts["callers"].get(n, ()) for n in nets), sid
        assert side not in (s.get("ticket_guess") or ""), sid
        assert side in s["release"]["deployments"] and s["release"]["changes"][side].get("sidecar") == SIDECAR_NAME, sid
        # what the answer says about it
        entry = [h for h in s["answer"]["harmless_changes"] if h["object"] == side]
        assert len(entry) == 1 and SIDECAR_NAME in entry[0]["change"] and "按容器" in entry[0]["why"], sid
        assert [n["why"] for n in s["answer"]["not_cause"] if n["name"] == side] == [entry[0]["why"]], sid
        assert any(line.startswith(f"排除 {side} 的改动") and SIDECAR_NAME in line for line in s["evidence_chain"]), sid
        assert "sidecar" in s["design"] and SIDECAR_NAME in s["realism"]["validate_note"], sid
        for c in CAUSES[sid]:
            if c["mechanism"] == "cpu":   # a real CPU fault no longer claims to be the only throttled pod
                assert side in c["evidence"][1] and "别的服务不被限流" not in c["evidence"][1] and side in c["decidable_why"], sid
                assert not any("后端不被限流" in e for e in c["evidence"]), sid


def test_reset_takes_the_sidecar_out_before_anything_else_touches_that_deployment():
    """`kubectl set resources` and `set env` without -c change every container of the pod template, so the sidecar goes
    first; afterwards every pod template holds only the service's own container."""
    facts = manifest_facts()
    for sid, s in V4.items():
        side = SIDECAR[sid]
        first = s["reset"][0]["kubectl"]
        assert first[2:6] == ["patch", "deployment", side, "--type=strategic"], sid
        assert json.loads(first[-1])["spec"]["template"]["spec"] == {"containers": [{"name": SIDECAR_NAME, "$patch": "delete"}],
                                                                     "volumes": [{"name": SIDECAR_NAME, "$patch": "delete"}]}
        pods = {d: {facts["main"][d]: {}} for d in facts["main"]}   # deployment -> {container: limits}

        def apply(args):
            if args[2:4] == ["patch", "deployment"]:
                for c in json.loads(args[-1])["spec"]["template"]["spec"].get("containers", []):
                    if c.get("$patch") == "delete":
                        pods[args[4]].pop(c["name"], None)
                    else:
                        pods[args[4]].setdefault(c["name"], {}).update((c.get("resources") or {}).get("limits") or {})
            elif args[2] == "set":
                d = args[4].split("/")[1]
                assert list(pods[d]) == [facts["main"][d]], (sid, args)   # would also change the sidecar
                if args[3] == "resources":
                    pods[d][facts["main"][d]]["cpu"] = next(a for a in args if a.startswith("--limits=")).split("=")[-1]
        for st in s["inject"]:
            if "kubectl" in st:
                apply(st["kubectl"])
        assert pods[side][SIDECAR_NAME]["cpu"] == SIDECAR_CPU and len(pods[side]) == 2, sid
        for st in s["reset"]:
            apply(st["kubectl"])
        assert all(list(p) == [facts["main"][d]] for d, p in pods.items()), sid


def test_a_left_over_sidecar_fails_the_health_check_and_recover_removes_it_first():
    base = BoutiqueLifecycle(FakeRunner(lambda a: KubeResult(True, "{}")), "/lab/k8s")._baseline()
    assert all(sh["extra_containers"] == [] for (kind, _), sh in base.items() if kind == "Deployment")

    def obj(kind, name, sh):
        if kind == "Service":
            return {"kind": kind, "metadata": {"name": name}, "spec": {"selector": sh["selector"], "ports": [{"port": 1, "targetPort": p} for p in sh["targetPorts"]]}}
        containers = [{"name": "server", "env": [{"name": k, "value": v} for k, v in sh["env"].items()],
                       "resources": {"limits": {"cpu": sh["cpu_limit"]}, "requests": {"cpu": sh["cpu_request"]}}}]
        if name == "cartservice":   # a run that died before its reset
            containers.append({"name": SIDECAR_NAME, "resources": {"limits": {"cpu": SIDECAR_CPU}, "requests": {"cpu": SIDECAR_CPU}}})
        return {"kind": kind, "metadata": {"name": name}, "status": {"readyReplicas": 1},
                "spec": {"replicas": sh["replicas"], "template": {"spec": {"containers": containers}}}}
    items = [obj(kind, name, sh) for (kind, name), sh in base.items()]

    def handler(args):
        if args[:5] == ["-n", "infra-tools", "get", "pod", "net-injector"]:
            return KubeResult(False, "", "NotFound")
        return KubeResult(True, json.dumps({"items": items}))
    lc = BoutiqueLifecycle(FakeRunner(handler), "/lab/k8s")
    problems = lc.health_problems()
    assert problems == ["Deployment/cartservice extra_containers differs from the manifest: ['log-shipper']"], problems
    done = remediate.restore_baseline(lc)
    assert len(done) == 1 and done[0].startswith("Deployment/cartservice: patch deployment cartservice"), done
    patch = next(c for c in lc.k.calls if c[2:5] == ["patch", "deployment", "cartservice"])
    assert json.loads(patch[-1])["spec"]["template"]["spec"] == {"containers": [{"name": SIDECAR_NAME, "$patch": "delete"}],
                                                                 "volumes": [{"name": SIDECAR_NAME, "$patch": "delete"}]}


def test_log_shipper_logs_batches_stretched_by_its_own_limit(monkeypatch, capsys):
    calls = []

    class Stop(Exception):
        pass

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) > 3:
            raise Stop
    monkeypatch.setattr(time, "sleep", sleep)
    started = time.process_time()
    try:
        exec(compile(_ambient_secret()["log_shipper.py"], "log_shipper.py", "exec"), {"__name__": "__main__"})
    except Stop:
        pass
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["message"] == "log shipper started" and len(lines) == 4 and all(4 <= s <= 8 for s in calls)
    assert all(x["logger"] == "log-shipper" and isinstance(x["took_ms"], int) and x["records"] > 0 for x in lines[1:])
    assert time.process_time() - started >= 3 * 0.018   # each batch burns its CPU budget


def test_validation_can_tell_which_container_of_a_pod_is_throttled():
    def text(ts, side_thr, side_per, main_per):
        pod = 'namespace="boutique",pod="cartservice-7d9f-x2k4q"'
        return "\n".join([f'container_cpu_usage_seconds_total{{container="server",{pod}}} 10 {ts}',
                          f'container_cpu_usage_seconds_total{{container="{SIDECAR_NAME}",{pod}}} 1 {ts}',
                          f'container_cpu_cfs_periods_total{{container="server",{pod}}} {main_per} {ts}',
                          f'container_cpu_cfs_throttled_periods_total{{container="server",{pod}}} 0 {ts}',
                          f'container_cpu_cfs_periods_total{{container="{SIDECAR_NAME}",{pod}}} {side_per} {ts}',
                          f'container_cpu_cfs_throttled_periods_total{{container="{SIDECAR_NAME}",{pod}}} {side_thr} {ts}',
                          f'container_network_receive_packets_total{{interface="eth0",{pod}}} 5 {ts}'])
    a, b = text(1_000_000, 0, 0, 0), text(1_060_000, 100, 110, 220)
    per_pod = validate_injection.throttling(pod_metrics.parse(a, "boutique"), pod_metrics.parse(b, "boutique"), 60)
    assert per_pod["cartservice"]["throttled"] == "100/330"   # what the agent's table shows: the pod's containers summed
    by = validate_injection.throttling(pod_metrics.parse(a, "boutique", by_container=True), pod_metrics.parse(b, "boutique", by_container=True), 60)
    assert by["cartservice/server"]["throttled"] == "0/220" and by[f"cartservice/{SIDECAR_NAME}"]["throttled"] == "100/110"
    assert "container_network_receive_packets_total" not in pod_metrics.parse(a, "boutique", by_container=True)["cartservice-7d9f-x2k4q/server"]


def test_v4_sop_is_v0_with_only_the_metrics_sentences_corrected():
    import make_sop_v0_metrics as m
    files = lambda root: sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())  # noqa: E731
    assert files(m.OUT) == files(m.SRC)
    for f in m.SRC.rglob("SKILL.md"):
        want = f.read_bytes().decode("utf-8")
        for old, new in m.EDITS.get(f.parent.name, []):
            want = want.replace(old, new)
        assert (m.OUT / f.relative_to(m.SRC)).read_bytes().decode("utf-8") == want, f.parent.name   # byte for byte, line endings too
    text = "".join(p.read_text(encoding="utf-8") for p in m.OUT.rglob("SKILL.md"))
    assert text.count("boutique_pod_metrics") >= 15 and "没有延迟指标" in text
    assert not re.search(r"无监控|没有监控|查看监控指标|无法直接观测|不要找 CPU|合计|sidecar|容器之和|log-shipper", text)


def test_background_manifest_hides_its_code_and_looks_ordinary():
    docs = [d for d in yaml.safe_load_all(AMBIENT_MANIFEST.read_text(encoding="utf-8")) if d]
    kinds = sorted(d["kind"] for d in docs)
    assert kinds == ["Deployment", "Deployment", "Deployment", "Secret"], "the scripts must sit in a Secret: the agent can read ConfigMaps"
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    assert tuple(deps) == AMBIENT_DEPLOYMENTS
    for name, d in deps.items():
        assert d["metadata"]["namespace"] == "boutique"
        text = json.dumps(d).lower()
        assert not re.search(r"\b(noise|decoy|chaos|inject|fault|fake|ambient|red.?herring)\b", text), name
        c = d["spec"]["template"]["spec"]["containers"][0]
        assert c["image"].endswith("/loadgenerator:v0.10.3") and c["imagePullPolicy"] == "IfNotPresent"
        assert c["resources"]["limits"]["cpu"] and "readinessProbe" not in c
    assert deps["promo-feed-sync"]["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] == "20m"
    secret = next(d for d in docs if d["kind"] == "Secret")["stringData"]
    sessions = set(re.findall(r'"(00000000-0000-4000-8000-[0-9a-f]{12})"', secret["uptime_checker.py"] + secret["catalog_crawler.py"]))
    assert sessions == remediate.AMBIENT_SESSIONS


def _lc(present=True):
    def handler(args):
        if args[-3:] == ["get", "deployment", AMBIENT_DEPLOYMENTS[0]]:
            return KubeResult(present, "", "" if present else "not found")
        return KubeResult(True, "{}")
    k = FakeRunner(handler)
    lc = BoutiqueLifecycle(k, "/lab/k8s")
    lc.health_problems = lambda: []
    return k, lc


def test_lifecycle_runs_the_background_workloads_for_bank_four_only(monkeypatch):
    monkeypatch.setattr("triage.lifecycle.time.sleep", lambda s: None)
    k, lc = _lc()
    lc.inject(V4["FV01"], StepLog())
    flat = [" ".join(c) for c in k.calls]
    applied = next(i for i, c in enumerate(flat) if c.startswith("apply -f") and c.endswith("ambient-v4.yaml"))
    assert applied < next(i for i, c in enumerate(flat) if "delete pods --all" in c)
    assert all(any(f"rollout status deployment/{d}" in c for c in flat) for d in AMBIENT_DEPLOYMENTS)
    k, lc = _lc(present=True)
    lc.inject(V3["EV01"], StepLog())
    assert any(c[:2] == ["delete", "-f"] and c[2].endswith("ambient-v4.yaml") for c in k.calls)
    k, lc = _lc(present=False)
    lc.inject(V3["EV01"], StepLog())
    assert not any(c[:1] == ["delete"] and "ambient-v4.yaml" in " ".join(c) for c in k.calls)


def test_user_side_numbers_leave_out_the_monitor_and_the_crawler():
    rows = [{"message": "request complete", "http.req.path": "/", "http.resp.status": 200, "http.resp.took_ms": 900, "session": s}
            for s in remediate.AMBIENT_SESSIONS]
    rows += [{"message": "request complete", "http.req.path": "/product/X", "http.resp.status": 200, "http.resp.took_ms": 9, "session": "user"}] * 40
    rows += [{"message": "request complete", "http.req.path": "/cart/checkout", "http.resp.status": 200, "http.resp.took_ms": 12, "session": "user"}] * 2

    class LC:
        def _run(self, args, timeout):
            return KubeResult(True, "\n".join(json.dumps(r) for r in rows))
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 150))
    obs = remediate.observe(LC(), since, min_seconds=15, max_seconds=30, sleep=lambda s: None, span_from_since=True)
    assert obs["requests"] == 42 and obs["slow_180ms"] == 0 and 145 <= obs["seconds"] <= 160
    assert remediate.observe(LC(), since, min_seconds=15, max_seconds=30, sleep=lambda s: None)["seconds"] == 15


def test_v4_prompt_keeps_smaller_causes_in_root_causes():
    assert holmes_pilot.prompt_version(V4["FV02"]) == "v4" and holmes_pilot.prompt_version(V3["EV02"]) == "v3"
    assert holmes_pilot.holmes_overrides("v4") == holmes_pilot.holmes_overrides("v3")
    obs = {"seconds": 150, "requests": 300, "slow_180ms": 200, "share_slow": 0.66, "p50_ms": 400, "p90_ms": 1700, "p99_ms": 2100,
           "http_500": 0, "checkout_attempts": 4, "checkout_failed": 0, "checkout_p50_ms": 800}
    p4 = holmes_pilot.build_prompt(V4["FV02"], "boutique", "prod", obs, "v4")
    p3 = holmes_pilot.build_prompt(V3["EV02"], "boutique", "prod", obs, "v3")
    assert '"role"' in p4 and "不要因为一个原因的影响小，就把它放进 not_causes" in p4 and DURATION in p4
    assert '"role"' not in p3 and holmes_pilot.CHECK_STEPS in p4 and holmes_pilot.CHECK_STEPS in p3


def test_fourth_bank_test_scenarios_need_finalize():
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "holmes_batch.py"), "--scenarios", "FT01", "--repeats", "1", "--tag", "never"],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode and "refusing the third bank's test scenarios" in res.stderr and "FT01" in res.stderr
