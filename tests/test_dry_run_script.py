"""The real-model dry-run script's plumbing (with the model faked), and robustness of the tool layer
against the kinds of arguments a real model might send."""
import importlib.util
import itertools
import json
from pathlib import Path

import pytest
from fake_cluster import GENERATED_STATE, NS, SCENARIO_STATE, FakeCluster

from triage.budget import Budget
from triage.kube import FakeRunner
from triage.llm import ChatResult, FakeLLM
from triage.tools import ReadOnlyTools, Trace

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    spec = importlib.util.spec_from_file_location("dry_run_llm", ROOT / "scripts" / "dry_run_llm.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dry_run_script_end_to_end_with_faked_model(monkeypatch, tmp_path, capsys):
    mod = load_script()
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    preflight = [ChatResult("ok"), ChatResult("", [{"id": "1", "name": "ping", "arguments": {"x": "hello"}}])]
    monkeypatch.setattr(mod, "LLMClient", lambda: FakeLLM(list(preflight)))
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr("sys.argv", ["dry_run_llm.py", "--arms", "rule", "--scenario", "S01,S04,G-api_probe-02"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "[ok] 模型支持工具调用" in out and "| S01 |" in out and "| G-api_probe-02 |" in out
    files = list((tmp_path / "results").glob("dryrun-*.jsonl"))
    recs = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 3 and all(r["dry_run"] and r["grade"]["correct"] for r in recs)


def test_dry_run_script_stops_when_model_cannot_call_tools(monkeypatch, capsys):
    mod = load_script()
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    monkeypatch.setattr(mod, "LLMClient", lambda: FakeLLM([ChatResult("ok"), ChatResult("我不会调用工具")]))
    monkeypatch.setattr("sys.argv", ["dry_run_llm.py"])
    assert mod.main() == 1
    assert "没有返回工具调用" in capsys.readouterr().out


ODD_CALLS = [
    ("overview", {}), ("overview", {"namespace": "kube-system"}),
    ("get_workload", {"kind": "deployment", "name": "nope"}), ("get_workload", {"kind": "Deployment", "name": "api"}),
    ("get_workload", {"kind": "statefulset", "name": "redis"}), ("get_workload", {"kind": "daemonset", "name": "x"}),
    ("get_pod", {"name": "ghost"}), ("get_logs", {"pod": "ghost"}), ("get_logs", {"pod": "redis-0", "previous": True}),
    ("get_logs", {"pod": "worker-7d4b-a", "tail": 100000}), ("get_events", {"object_name": "worker"}),
    ("get_service", {"name": "redis"}), ("get_service", {"name": "missing"}),
    ("get_workload", {"name": "api"}), ("delete_pod", {"name": "api"}), ("get_logs", {"pod": "x", "bogus": 1}),
]


@pytest.mark.parametrize("state", sorted(set(SCENARIO_STATE.values()) | set(GENERATED_STATE.values())))
def test_tool_layer_never_crashes_on_odd_model_arguments(state):
    tools = ReadOnlyTools(FakeRunner(FakeCluster(state).handle), NS, Budget(max_tool_calls=100), Trace())
    for name, args in ODD_CALLS:
        out = tools.dispatch(name, args)
        assert isinstance(out, dict)
        json.dumps(out, ensure_ascii=False)
    assert tools.dispatch("delete_pod", {"name": "api"})["error"] == "unknown_tool"
    assert tools.dispatch("get_workload", {"name": "api"})["error"] == "bad_arguments"
