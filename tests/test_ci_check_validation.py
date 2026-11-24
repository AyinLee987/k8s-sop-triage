import json

import pytest

from scripts import ci_check_validation as gate


def ok_record() -> dict:
    return {"scenario": "B01", "confirmed": True,
            "steps": [{"phase": "verify_healthy", "ok": True}, {"phase": "inject", "ok": True},
                      {"phase": "confirm", "ok": True}, {"phase": "reset", "ok": True},
                      {"phase": "verify_recovered", "ok": True}]}


def test_clean_record_passes():
    assert gate.problems(ok_record()) == []


@pytest.mark.parametrize("change, expected", [
    ({"confirmed": False, "confirm_error": "endpoints still ready"}, "not confirmed"),
    ({"reset_error": "rollout frontend timed out"}, "reset_error"),
    ({"error": "cluster unhealthy before inject"}, "error"),
])
def test_each_failure_kind_is_reported(change, expected):
    assert any(expected in p for p in gate.problems({**ok_record(), **change}))


def test_failed_recovery_step_is_reported():
    record = ok_record()
    record["steps"][-1] = {"phase": "verify_recovered", "ok": False, "detail": "cartservice 0/1"}
    assert any("verify_recovered" in p for p in gate.problems(record))


def test_missing_record_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "OUT", tmp_path)
    (tmp_path / "B01.json").write_text(json.dumps(ok_record()), encoding="utf-8")
    assert gate.main(["B01"]) == 0
    assert gate.main(["B01", "B02"]) == 1
