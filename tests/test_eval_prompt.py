"""The one-shot evaluation prompt for Online Boutique scenarios: default framing, user-side numbers, diagnosis only."""
import sys

from triage import remediate
from triage.lifecycle import REPO

sys.path.insert(0, str(REPO / "scripts"))
import holmes_batch as hb  # noqa: E402
import holmes_loop as hl  # noqa: E402
import holmes_pilot as hp  # noqa: E402

OBS = {"seconds": 45, "requests": 128, "slow_180ms": 92, "share_slow": 0.719, "p50_ms": 410, "p90_ms": 2066, "p99_ms": 2126,
       "http_500": 0, "checkout_attempts": 4, "checkout_failed": 0, "resolved": False}


def test_boutique_prompt_is_framed_measured_and_asks_for_causes_only():
    sc = {"app": "boutique", "symptom": "商城页面明显变慢了"}
    p = hp.build_prompt(sc, "boutique", "prod", OBS)
    assert p.startswith(hp.PROD_FRAME) and hp.PROD_FRAME == hl.PROD_FRAME          # the same words as the fix-and-verify loop
    assert "占 71.9%" in p and "root_causes" in p and "不需要提出或执行修复" in p
    for word in ("actions", "expect", "运维", "轮", "撤销", "set_env", "restart"):     # nothing of the loop's fix protocol
        assert word not in p, word
    bare = hp.build_prompt(sc, "boutique", "none", OBS)
    assert "真实故障" not in bare and bare.endswith(p[len(hp.PROD_FRAME):])
    assert hp.build_prompt({"symptom": "x"}, "tiktok-k8s-lab", "prod", None).endswith("请排查根因。")


def test_root_causes_are_read_from_the_answer_with_or_without_a_fence():
    fenced = '分析\n```json\n{"root_causes": [{"object": "cartservice", "cause": "网络延迟"}], "explanation": "x"}\n```'
    assert remediate.parse_plan(fenced)[0]["root_causes"][0]["object"] == "cartservice"
    assert remediate.parse_plan('结论：{"root_causes": [{"object": "redis-cart", "cause": "丢包"}], "explanation": "y"}')[0]["root_causes"][0]["cause"] == "丢包"
    assert remediate.parse_plan("没有结构化结论")[0] is None
    import inspect
    assert '"--frame", frame' in inspect.getsource(hb.run_one)
