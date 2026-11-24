"""CI gate for scripts/validate_injection.py: fail unless every scenario's fault was confirmed active and
the environment came back healthy after reset.

validate_injection.py is a lab tool: it records an unconfirmed injection and moves on (exit code 0), which is
right when exploring by hand but would let a broken scenario pass CI.

  python scripts/ci_check_validation.py B01 B02
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "results" / "validation"


def problems(record: dict) -> list[str]:
    found = []
    if not record.get("confirmed"):
        found.append(f"fault not confirmed active ({record.get('confirm_error', 'no detail')})")
    for key in ("error", "reset_error"):
        if record.get(key):
            found.append(f"{key}: {record[key]}")
    # every lifecycle step that reported failure (verify_healthy, reset, verify_recovered, ...)
    for step in record.get("steps", []):
        if isinstance(step, dict) and step.get("ok") is False and step.get("phase") != "confirm":
            found.append(f"step failed: {step.get('phase') or step.get('name')} - {str(step.get('detail', ''))[:200]}")
    return found


def main(ids: list[str]) -> int:
    if not ids:
        print("usage: ci_check_validation.py SCENARIO_ID [...]")
        return 2
    failed = False
    for sid in ids:
        path = OUT / f"{sid}.json"
        if not path.exists():
            print(f"FAIL {sid}: no record at {path} (did validate_injection.py run?)")
            failed = True
            continue
        found = problems(json.loads(path.read_text(encoding="utf-8")))
        print(f"{'FAIL' if found else 'ok  '} {sid}" + "".join(f"\n     - {p}" for p in found))
        failed |= bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
