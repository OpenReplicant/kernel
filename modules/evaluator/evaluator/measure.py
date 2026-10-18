"""Eval run files into DQV measurements (spec §12, D-71). Pure functions, standard library only.

A run file is the JSON an eval writes (eval/<suite>/runs/<run>.json). Each number in it, at any depth
outside `models` (how many answers each model gave, not a score), is one measurement:

  urn:kk:eval:<suite>:<run>  dqv:value <number>
      {dqv:isMeasurementOf: urn:kk:metric:<suite>:<path>, dqv:computedOn: urn:kk:dataset:<suite>[:<split>]}

observed, referencing the run file (an artifact) and the number by JSON pointer (RFC 6901). The run is
labelled with its file name and the models that answered. Lists are left out (per-case detail).
"""
from __future__ import annotations

import re

DQV = "http://www.w3.org/ns/dqv#"
XSD = "http://www.w3.org/2001/XMLSchema#"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SKIP = {"models"}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9@._-]+", "-", str(text)).strip("-")


def numbers(data, path: tuple = ()):
    """(path, number) for every number in `data`, depth first, in file order."""
    if isinstance(data, dict):
        for k, v in data.items():
            if not path and k in SKIP:
                continue
            yield from numbers(v, (*path, str(k)))
    elif isinstance(data, (int, float)) and not isinstance(data, bool) and data == data:
        yield path, data


def pointer(path: tuple) -> str:
    return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in path)


def measurements(run: dict, suite: str, name: str, sha256: str) -> list[dict]:
    """The add_claim ops for one run file (its artifact op is the caller's)."""
    subject = f"urn:kk:eval:{slug(suite)}:{slug(name)}"
    split = run.get("dataset") or run.get("split")
    dataset = f"urn:kk:dataset:{slug(suite)}" + (f":{slug(split)}" if isinstance(split, str) else "")
    models = ", ".join(run.get("models") or {}) if isinstance(run.get("models"), dict) else ""
    ops = [{"op": "add_claim", "subject": subject, "predicate": LABEL,
            "object": {"value": {"@value": f"{suite} eval run {name}" + (f" ({models})" if models else "")}},
            "evidence_type": "deterministic", "references": [{"artifact": sha256}]}]
    for path, value in numbers(run):
        ops.append({"op": "add_claim", "subject": subject, "predicate": DQV + "value",
                    "object": {"value": {"@value": value, "@type": XSD + ("integer" if isinstance(value, int) else "decimal")}},
                    "qualifiers": {DQV + "isMeasurementOf": f"urn:kk:metric:{slug(suite)}:{'.'.join(slug(p) for p in path)}",
                                   DQV + "computedOn": dataset},
                    "evidence_type": "observed",
                    "references": [{"artifact": sha256, "span": {"type": "FragmentSelector", "value": pointer(path),
                                                                 "conformsTo": "https://www.rfc-editor.org/rfc/rfc6901"}}]})
    return ops
