"""Entry-level SOP updates for the cross-incident comparison: what the reflecting model may change and what it is shown."""
import importlib.util

from triage.lifecycle import REPO

spec = importlib.util.spec_from_file_location("reflect", REPO / "boutique" / "reflect.py")
rf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rf)

OBS = {"seconds": 75, "requests": 200, "slow_180ms": 150, "share_slow": 0.75, "p50_ms": 410, "p90_ms": 2000, "p99_ms": 2100,
       "http_500": 0, "checkout_attempts": 5, "checkout_failed": 0, "resolved": False}
RECORD = {"before": OBS, "outcome": "agent_stopped", "rounds": [
    {"round": 1, "answer": "货币服务每次调用约 200ms", "plan": {"root_causes": [{"object": "currencyservice", "cause": "CPU 限流"}], "explanation": "调高上限",
                                                     "expect": "慢请求占比降到 0", "actions": [{"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "1"}]},
     "actions": [{"action": {"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "1"}, "result": "ok"}], "observation": OBS, "kept": False},
    {"round": 2, "answer": "请求晚到 200ms", "plan": {"root_causes": [{"object": "currencyservice", "cause": "网络路径延迟"}], "explanation": "修不了", "actions": []},
     "actions": [], "observation": None}]}


def test_entries_round_trip_and_sit_under_the_title():
    body = "# currencyservice 排查 SOP\n\n适用症状：……\n\n## 信号 → 步骤\n\n表\n\n## 步骤\n\n1. ……"
    one = rf.with_items(body, [("E1", "BH05", "看到 A → 查 B")])
    assert one.split("\n")[0].startswith("# ") and one.index(rf.SECTION) < one.index("## 信号")
    assert rf.items_of(one) == [("E1", "BH05", "看到 A → 查 B")]
    two = rf.with_items(one, [("E1", "BH05、BH06", "看到 A → 查 C"), ("E2", "BH06", "D")])
    assert rf.items_of(two) == [("E1", "BH05、BH06", "看到 A → 查 C"), ("E2", "BH06", "D")] and two.count(rf.SECTION) == 1
    assert "## 信号 → 步骤" in two and "## 步骤" in two and two.count("表") == 1      # the rest of the SOP is untouched
    assert two.count("适用症状：……") == 1                                             # including text between the title and the first heading
    assert rf.with_items(two, []) == body


def test_changes_are_applied_one_entry_at_a_time():
    lib = {"boutique-triage-overview": [], "boutique-currencyservice": [("E1", "BH01", "旧")]}
    done = rf.apply(lib, [{"op": "add", "sop": "boutique-triage-overview", "text": "新 " * 400},
                          {"op": "modify", "sop": "boutique-currencyservice", "id": "E1", "text": "改"},
                          {"op": "remove", "sop": "boutique-currencyservice", "id": "E9"},
                          {"op": "add", "sop": "boutique-triage-overview", "text": "第四个"}], "BH05")
    assert [d["applied"] for d in done] == [True, True, False, False]
    assert len(lib["boutique-triage-overview"][0][2]) <= rf.MAX_TEXT and lib["boutique-triage-overview"][0][:2] == ("E1", "BH05")
    assert lib["boutique-currencyservice"] == [("E1", "BH01、BH05", "改")]
    assert not rf.apply(lib, [{"op": "add", "sop": "nope", "text": "x"}, {"op": "rewrite", "sop": "boutique-currencyservice"}, "x"], "BH06")[0]["applied"]
    rf.apply(lib, [{"op": "remove", "sop": "boutique-currencyservice", "id": "E1"}], "BH07")
    assert lib["boutique-currencyservice"] == []


def test_one_shot_material_has_no_outcomes_and_neither_has_the_answer():
    b, c = rf.material(RECORD, "页面变慢", "B"), rf.material(RECORD, "页面变慢", "C")
    assert "CPU 限流" in b and "建议的修复" in b
    for leak in ("网络路径延迟", "预期", "执行后", "撤销", "第 2 轮", "结局"):
        assert leak not in b, leak
    for part in ("慢请求占比降到 0", "改动已撤销", "网络路径延迟", "不再申请操作"):
        assert part in c, part
    assert "已证实" in rf.VERIFIED and "已证实" not in rf.COMMON          # the verification rule is only given where outcomes exist
    import inspect
    assert "truth" not in inspect.getsource(rf.material) and "answer\"]" not in inspect.getsource(rf.material).replace('r.get("answer")', "")
