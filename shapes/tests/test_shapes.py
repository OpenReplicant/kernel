"""Checks the kernel shapes against the valid and invalid fixtures.

Run: python -m pytest shapes/tests   (or: python shapes/tests/test_shapes.py)
Needs: rdflib, pyshacl
"""
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Namespace
from rdflib.namespace import SH

HERE = Path(__file__).parent
SHAPES = sorted(HERE.parent.glob("*.shacl.ttl"))   # kernel shapes + pack shapes
EX = Namespace("http://example.org/")

EXPECTED_VIOLATIONS = {
    EX.noQuote,
    EX.unattributed,
    EX.noPremises,
    "urn:kk:module:orphan",
    "urn:kk:chain:c/s",
}


def _run(fixture: str):
    data = Graph().parse(HERE / fixture)
    shapes = Graph()
    for f in SHAPES:
        shapes.parse(f)
    conforms, report, text = validate(data, shacl_graph=shapes, advanced=True, allow_warnings=False)
    focus = {str(o) for o in report.objects(None, SH.focusNode)}
    return conforms, focus, text


def test_valid_fixture_conforms():
    conforms, _, text = _run("valid.ttl")
    assert conforms, text


def test_every_invalid_node_is_reported():
    conforms, focus, text = _run("invalid.ttl")
    assert not conforms
    missing = {str(x) for x in EXPECTED_VIOLATIONS} - focus
    assert not missing, f"not reported: {missing}\n{text}"


if __name__ == "__main__":
    test_valid_fixture_conforms()
    test_every_invalid_node_is_reported()
    print("shape tests passed")
