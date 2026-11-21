"""The metrics table the agent can ask for: read from canned kubelet output, no cluster and no token involved."""
import sys

import yaml

from triage.lifecycle import REPO

sys.path.insert(0, str(REPO / "scripts"))
import pod_metrics as pm  # noqa: E402


def cadvisor(usage, periods, throttled, thr_s, rx, ts):
    L = 'namespace="boutique",pod="currencyservice-abc"'
    return "\n".join([
        "# TYPE container_cpu_usage_seconds_total counter",
        f'container_cpu_usage_seconds_total{{container="server",cpu="total",id="/x",{L}}} {usage} {ts}',
        f'container_cpu_usage_seconds_total{{container="POD",cpu="total",id="/p",{L}}} 99 {ts}',          # pause container: ignored
        f'container_cpu_usage_seconds_total{{container="",cpu="total",id="/pod",{L}}} 99 {ts}',           # pod-level cgroup: ignored
        f'container_cpu_cfs_periods_total{{container="server",id="/x",{L}}} {periods} {ts}',
        f'container_cpu_cfs_throttled_periods_total{{container="server",id="/x",{L}}} {throttled} {ts}',
        f'container_cpu_cfs_throttled_seconds_total{{container="server",id="/x",{L}}} {thr_s} {ts}',
        f'container_memory_working_set_bytes{{container="server",id="/x",{L}}} 6.7108864e+07 {ts}',
        f'container_network_receive_packets_total{{interface="eth0",{L}}} {rx} {ts}',
        f'container_network_receive_packets_total{{interface="lo",{L}}} 777777 {ts}',                    # not the pod interface
        f'container_network_transmit_packets_total{{interface="eth0",{L}}} {rx} {ts}',
        f'container_network_receive_packets_dropped_total{{interface="eth0",{L}}} 0 {ts}',
        f'container_network_transmit_packets_dropped_total{{interface="eth0",{L}}} 0 {ts}',
        f'container_cpu_usage_seconds_total{{container="x",id="/y",namespace="infra-tools",pod="net-injector"}} 5 {ts}',   # another namespace: never shown
        'container_fs_reads_total{container="server",namespace="boutique",pod="currencyservice-abc"} 1',  # not a metric we show
    ])


PODS = [{"name": "currencyservice-abc", "host": "172.18.0.2", "cpu_limit_m": 200.0, "mem_limit_mi": 128.0},
        {"name": "frontend-new", "host": "172.18.0.2", "cpu_limit_m": 200.0, "mem_limit_mi": 128.0}]


def test_table_shows_usage_throttling_and_network_for_the_namespace_only():
    a = pm.parse(cadvisor(10.0, 1000, 100, 8.0, 5000, 1_000_000), "boutique")
    b = pm.parse(cadvisor(13.0, 1150, 220, 17.5, 5600, 1_015_000), "boutique")
    assert list(a) == ["currencyservice-abc"] and a["currencyservice-abc"]["container_cpu_usage_seconds_total"] == (10.0, 1_000_000)
    t = pm.table(PODS, a, b, 15)
    row = next(line for line in t.splitlines() if line.startswith("| currencyservice-abc"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    # 3 CPU-seconds in 15 s = 200m against a 200m limit; 120 of 150 periods throttled in the window, 220 of 1150 since start
    assert cells[1:6] == ["200", "200", "80%", "19%", "17.5"] and cells[6] == "64 / 128" and cells[7] == "600/600" and cells[8] == "0/0"
    assert "net-injector" not in t and "infra-tools" not in t and "777777" not in t
    # a pod the kubelet has not reported yet gets dashes, not zeros
    assert next(line for line in t.splitlines() if line.startswith("| frontend-new")).count("-") >= 6
    for word in ("注入", "netem", "故障", "原因", "根因"):      # numbers and what they measure; no interpretation
        assert word not in t, word


def test_a_counter_the_kubelet_did_not_refresh_is_not_reported_as_zero_activity():
    a = pm.parse(cadvisor(10.0, 1000, 100, 8.0, 5000, 1_000_000), "boutique")
    t = pm.table(PODS[:1], a, a, 15)                       # same timestamps twice
    cells = [c.strip() for c in t.splitlines()[2].strip("|").split("|")]
    assert cells[1] == "-" and cells[3] == "-" and cells[4] == "10%"      # the since-start share is still there


def test_limits_are_read_in_any_unit():
    assert pm.cpu_m("200m") == 200 and pm.cpu_m("1") == 1000 and pm.cpu_m(None) is None
    assert pm.mem_mi("128Mi") == 128 and pm.mem_mi("1Gi") == 1024 and round(pm.mem_mi("134217728")) == 128


def test_without_a_token_the_tool_says_so_and_prints_no_traceback(monkeypatch, capsys):
    monkeypatch.delenv("METRICS_TOKEN_FILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["pod_metrics.py"])
    assert pm.main() == 1
    out = capsys.readouterr().out
    assert "指标不可用" in out and "Traceback" not in out


def test_end_to_end_with_a_fake_kubelet_never_prints_the_token(monkeypatch, capsys, tmp_path):
    token = tmp_path / "t"
    token.write_text("SECRET-TOKEN-VALUE\n", encoding="utf-8")
    monkeypatch.setenv("METRICS_TOKEN_FILE", str(token))
    monkeypatch.setattr(sys, "argv", ["pod_metrics.py", "--window", "0"])
    monkeypatch.setattr(pm, "list_pods", lambda ns: PODS)
    samples = iter([cadvisor(10.0, 1000, 100, 8.0, 5000, 1_000_000), cadvisor(13.0, 1150, 220, 17.5, 5600, 1_015_000)])
    seen = []
    monkeypatch.setattr(pm, "scrape", lambda host, tok, timeout=20: (seen.append((host, tok)), next(samples))[1])
    assert pm.main() == 0
    out = capsys.readouterr().out
    assert "80%" in out and "SECRET" not in out and seen == [("172.18.0.2", "SECRET-TOKEN-VALUE")] * 2
    monkeypatch.setattr(pm, "scrape", lambda *a, **k: (_ for _ in ()).throw(PermissionError("HTTP Error 403: Forbidden")))
    assert pm.main() == 1 and "SECRET" not in capsys.readouterr().out


def test_the_tool_takes_no_arguments_and_the_account_can_only_read_node_metrics():
    ts = yaml.safe_load((REPO / "holmes" / "toolsets-metrics.yaml").read_text(encoding="utf-8"))["toolsets"]["boutique/metrics"]
    (tool,) = ts["tools"]
    assert "parameters" not in tool and "{{" not in tool["command"] and "$" not in tool["command"]     # nothing the model writes reaches the shell
    docs = list(yaml.safe_load_all((REPO / "k8s" / "rbac-metrics.yaml").read_text(encoding="utf-8")))
    role = next(d for d in docs if d["kind"] == "ClusterRole")
    assert role["rules"] == [{"apiGroups": [""], "resources": ["nodes/metrics"], "verbs": ["get"]}]
    # the default configuration is untouched: earlier groups keep running without metrics
    cfg = yaml.safe_load((REPO / "holmes" / "config.yaml").read_text(encoding="utf-8"))["toolsets"]
    assert cfg["kubernetes/live-metrics"]["enabled"] is False and "boutique/metrics" not in cfg
    import inspect, holmes_pilot
    src = inspect.getsource(holmes_pilot.main)
    assert 'if args.metrics:\n            cmd += ["--custom-toolsets"' in src and 'env.pop("METRICS_TOKEN_FILE", None)' in src
