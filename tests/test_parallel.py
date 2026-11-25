"""Two laptop clusters at once (docs/PARALLEL.md): batch shards, per-cluster files, one run per cluster."""
import importlib.util
import json
import sys
from collections import Counter

import pytest

from triage.lifecycle import REPO


def load_batch():
    spec = importlib.util.spec_from_file_location("holmes_batch_parallel", REPO / "scripts" / "holmes_batch.py")
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
    return hb


def test_each_scenarios_repeats_alternate_between_the_clusters():
    hb = load_batch()
    for ids in (["FV01", "FL01"], ["FV01", "FL01", "FV03"], ["FL05"]):
        for repeats in (2, 4, 6):
            got = Counter((sc, hb.shard_of(ids, sc, rep, 2)) for rep in range(1, repeats + 1) for sc in ids)
            for sc in ids:
                assert got[(sc, 1)] == got[(sc, 2)] == repeats // 2, (ids, repeats, got)
    # the six-run pilot split this way: three runs per cluster, both scenarios on both clusters
    ids = ["FV01", "FL01"]
    shard1 = [(sc, rep) for rep in (1, 2, 3) for sc in ids if hb.shard_of(ids, sc, rep, 2) == 1]
    assert len(shard1) == 3 and {sc for sc, _ in shard1} == set(ids)
    with pytest.raises(SystemExit):
        hb.parse_shard("3/2")
    with pytest.raises(SystemExit):
        hb.parse_shard("two")


def fake_run_one(calls):
    def run(sc_id, out, *args, **kwargs):
        calls.append(sc_id)
        return {"scenario": sc_id, "noise": [], "exit": 0, "wall_s": 1, "answer_file": str(out), "answer": "",
                "total_tokens": 10, "num_llm_calls": 1, "tool_calls": 0, "skills_fetched": []}
    return run


def run_batch(hb, monkeypatch, tmp_path, cluster, *extra):
    monkeypatch.setattr(hb, "REPO", tmp_path)
    monkeypatch.setenv("ADMIN_CONTEXT", f"kind-{cluster}")
    calls = []
    monkeypatch.setattr(hb, "run_one", fake_run_one(calls))
    monkeypatch.setattr(sys, "argv", ["holmes_batch.py", "--scenarios", "FV01,FL01", "--repeats", "2", "--tag", "par", *extra])
    hb.main()
    return calls


def test_two_shards_fill_one_tag_and_each_record_names_its_cluster(monkeypatch, tmp_path):
    hb = load_batch()
    first = run_batch(hb, monkeypatch, tmp_path, "tiktok-k8s-lab", "--shard", "1/2")
    second = run_batch(hb, monkeypatch, tmp_path, "sop-lab-2", "--shard", "2/2")
    assert len(first) == len(second) == 2 and Counter(first + second) == {"FV01": 2, "FL01": 2}
    out = tmp_path / "results" / "holmes_batch" / "par"
    runs = [json.loads(l) for l in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["scenario"], r["repeat"]) for r in runs] == [("FV01", 1), ("FL01", 1), ("FV01", 2), ("FL01", 2)]   # plan order
    assert {r["cluster"] for r in runs if r["shard"] == "1/2"} == {"tiktok-k8s-lab"}
    assert {r["cluster"] for r in runs if r["shard"] == "2/2"} == {"sop-lab-2"}
    assert "FV01 | 2 (0 failed)" in (out / "summary.md").read_text(encoding="utf-8")
    # continuing a shard that is done runs nothing; an unsharded run on a sharded tag, or another shard count, is refused
    assert run_batch(hb, monkeypatch, tmp_path, "tiktok-k8s-lab", "--shard", "1/2") == []
    with pytest.raises(SystemExit):
        run_batch(hb, monkeypatch, tmp_path, "tiktok-k8s-lab")
    with pytest.raises(SystemExit):
        run_batch(hb, monkeypatch, tmp_path, "tiktok-k8s-lab", "--shard", "1/3")


def test_an_unsharded_batch_is_unchanged_and_cannot_be_continued_in_shards(monkeypatch, tmp_path):
    hb = load_batch()
    assert run_batch(hb, monkeypatch, tmp_path, "tiktok-k8s-lab") == ["FV01", "FL01", "FV01", "FL01"]
    out = tmp_path / "results" / "holmes_batch" / "par"
    assert not list(out.glob("runs-shard*")) and len((out / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    with pytest.raises(SystemExit):
        run_batch(hb, monkeypatch, tmp_path, "sop-lab-2", "--shard", "2/2")


def test_credentials_and_the_one_run_rule_are_per_cluster():
    entry = (REPO / "scripts" / "laptop_entry.sh").read_text(encoding="utf-8")
    laptop = (REPO / "scripts" / "Run-Laptop.ps1").read_text(encoding="utf-8")
    assert "CLUSTER_DIR=/work/.local/clusters/${ADMIN_CONTEXT#kind-}" in entry
    for var in ("ADMIN_KUBECONFIG", "DIAG_KUBECONFIG", "METRICS_TOKEN_FILE"):
        assert f"export {var}=$CLUSTER_DIR/" in entry, var
    assert '"clusters\\$Cluster"' in laptop and "'admin-internal.kubeconfig'" in laptop
    assert '--label "k8s-sop.cluster=$Cluster"' in laptop and '--filter "label=k8s-sop.cluster=$Cluster"' in laptop
    assert "-n $Cluster" not in laptop   # the lab app's namespace is tiktok-k8s-lab whatever the cluster is called
