"""Expand scenarios/templates.yaml into concrete scenarios (same schema as scenarios.yaml).

Each generated scenario inherits its template's split and mechanism, so a mechanism never lands in
both dev and holdout. Only the lifecycle and grader read the result; agents never do.

  python -m triage.generator            # print a summary of what would be generated
  python -m triage.generator --dump     # print every generated scenario as YAML
"""
from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_FILE = REPO / "scenarios" / "templates.yaml"
_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z0-9_:]+)\}\}")
_META_KEYS = {"override", "symptom"}


class TemplateError(ValueError):
    pass


def _render(value, params: dict, symptoms: dict):
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1) in params and isinstance(params[whole.group(1)], list):
            return list(params[whole.group(1)])  # e.g. key_evidence: "{{evidence}}" -> list

        def sub(m):
            key = m.group(1)
            if key.startswith("sym:"):
                name = key[4:]
                if name not in symptoms:
                    raise TemplateError(f"unknown symptom {name}")
                return symptoms[name]
            if key not in params:
                raise TemplateError(f"missing parameter {key}")
            return str(params[key])
        return _PLACEHOLDER.sub(sub, value)
    if isinstance(value, list):
        return [_render(v, params, symptoms) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, params, symptoms) for k, v in value.items()}
    return value


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def expand(path: Path = TEMPLATE_FILE, max_per_template: int | None = None) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    symptoms = data.get("symptoms", {})
    out = []
    for tpl in data["templates"]:
        rows = tpl["params"][:max_per_template] if max_per_template else tpl["params"]
        for i, row in enumerate(rows, 1):
            params = {k: v for k, v in row.items() if k not in _META_KEYS}
            body = _render(copy.deepcopy(tpl["scenario"]), params, symptoms)
            if row.get("override"):
                body = _merge(body, _render(row["override"], params, symptoms))
            if "symptom" not in body:
                if row.get("symptom") not in symptoms:
                    raise TemplateError(f"{tpl['template']} row {i}: no symptom")
                body["symptom"] = symptoms[row["symptom"]]
            scenario = {
                "id": f"G-{tpl['template']}-{i:02d}",
                "split": tpl["split"],
                "kind": tpl["kind"],
                "template": tpl["template"],
                "mechanism": tpl["mechanism"],
                "generated": True,
                **body,
            }
            _validate(scenario)
            out.append(scenario)
    return out


def _validate(sc: dict) -> None:
    for key in ("symptom", "inject", "confirm", "reset", "answer"):
        if key not in sc:
            raise TemplateError(f"{sc['id']}: missing {key}")
    if _PLACEHOLDER.search(yaml.safe_dump(sc, allow_unicode=True)):
        raise TemplateError(f"{sc['id']}: unrendered placeholder")
    if sc["answer"]["cause_category"] != sc["mechanism"]:
        raise TemplateError(f"{sc['id']}: answer category differs from template mechanism")
    if sc["inject"] and not sc["reset"]:
        raise TemplateError(f"{sc['id']}: inject without reset")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dump", action="store_true")
    p.add_argument("--max-per-template", type=int)
    a = p.parse_args()
    items = expand(max_per_template=a.max_per_template)
    if a.dump:
        print(yaml.safe_dump(items, allow_unicode=True, sort_keys=False))
        return
    by = {}
    for s in items:
        by.setdefault((s["split"], s["mechanism"]), []).append(s["id"])
    for (split, mech), ids in sorted(by.items()):
        print(f"{split:8s} {mech:24s} {len(ids):2d}  {', '.join(ids)}")
    print(f"total {len(items)} generated scenarios")


if __name__ == "__main__":
    main()
