"""The admission check's bookkeeping: which entries a run gets, the subset library, and the per-entry estimate."""
import sys

from triage.lifecycle import REPO

sys.path.insert(0, str(REPO / "scripts"))
import admission as ad  # noqa: E402

LIB = REPO / "holmes" / "skills" / "xinc-D-cand"


def test_halves_are_complementary_balanced_and_repeatable():
    keys = ad.entries(LIB)
    assert len(keys) == len(set(keys)) >= 4 and all(":" in k for k in keys)
    for sid in ("CV01", "CV05"):
        count = {k: 0 for k in keys}
        for rep in range(1, 11):
            present = ad.halves(keys, "s", LIB.name, sid, rep)
            assert present == ad.halves(keys, "s", LIB.name, sid, rep)
            if rep % 2 == 0:
                assert sorted(set(present) | set(ad.halves(keys, "s", LIB.name, sid, rep - 1))) == sorted(keys)
                assert not set(present) & set(ad.halves(keys, "s", LIB.name, sid, rep - 1))
            assert abs(len(present) - len(keys) / 2) <= 0.5
            for k in present:
                count[k] += 1
        assert set(count.values()) == {5}                       # every entry in exactly half of each scenario's runs
    assert ad.halves(keys, "s", LIB.name, "CV01", 1) != ad.halves(keys, "s", LIB.name, "CV05", 1) or \
        ad.halves(keys, "s", LIB.name, "CV01", 3) != ad.halves(keys, "s", LIB.name, "CV05", 3)


def test_subset_library_keeps_only_the_chosen_entries_and_the_rest_of_the_sops(tmp_path):
    keys = ad.entries(LIB)
    keep = set(keys[::2])
    ad.write_subset(LIB, keep, tmp_path / "sub")
    assert set(ad.entries(tmp_path / "sub")) == keep
    ad.write_subset(LIB, set(), tmp_path / "none")
    assert ad.entries(tmp_path / "none") == []
    base = REPO / "holmes" / "skills" / "svc-sop-v0"
    for p in sorted(base.glob("*/SKILL.md")):                   # no entries left = the base SOP text
        got = (tmp_path / "none" / p.parent.name / "SKILL.md").read_text(encoding="utf-8")
        assert ad.reflect.split(got)[1].split() == ad.reflect.split(p.read_text(encoding="utf-8"))[1].split()
    ad.write_subset(LIB, set(keys), tmp_path / "all")
    for p in sorted(LIB.glob("*/SKILL.md")):
        assert (tmp_path / "all" / p.parent.name / "SKILL.md").read_text(encoding="utf-8") == p.read_text(encoding="utf-8")


def test_effect_is_the_within_scenario_difference():
    keys = ["a:E1", "a:E2"]
    runs, passed = [], {}
    # scenario X is easy, Y is hard; E1 helps by the same amount in both, E2 does nothing
    for sid, base in (("X", 0.5), ("Y", 0.0)):
        for rep in range(1, 9):
            present = [k for k, on in zip(keys, (rep % 2 == 1, rep in (1, 2, 5, 6))) if on]
            runs.append({"scenario": sid, "repeat": rep, "entries_present": present, "entries_all": keys})
            p = base + (0.5 if "a:E1" in present else 0.0)
            passed[(sid, rep)] = p >= 1.0 or (p == 0.5 and rep in (1, 2, 3, 4))
    rows = {r["entry"]: r for r in ad.effects(runs, passed)}
    assert rows["a:E1"]["effect"] > 0.4 and abs(rows["a:E2"]["effect"]) < 0.3
    assert rows["a:E1"]["with"].endswith("/8") and rows["a:E1"]["without"].endswith("/8")
    del passed[("X", 1)]                                        # an ungraded run is left out, not counted as a failure
    assert {r["entry"]: r for r in ad.effects(runs, passed)}["a:E1"]["with"].endswith("/7")
