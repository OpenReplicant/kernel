"""M3-T2: a fixture repository (Python, npm, Go, Cargo, Markdown) gives the expected claims; dependency
folders are skipped. Offline: no database or services.

Run: python modules/self-encoder/tests/test_repo.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/self-encoder"), str(ROOT / "modules/validator-shacl")]
from self_encoder.encode import encode_file, list_files  # noqa: E402
from validator.core import validate  # noqa: E402

S = "http://schema.org/"
REQ, SUG, LANG, VER = S + "softwareRequirements", S + "softwareSuggestions", S + "programmingLanguage", S + "version"


def claims(repo: Path) -> tuple[set, dict]:
    """(subject, predicate, object, qualifier) for every claim, and each claim's file and line."""
    out, where = set(), {}
    with tempfile.TemporaryDirectory() as artifacts:
        for rel in list_files(repo):
            cs = encode_file(repo, rel, Path(artifacts))
            cs["generated_by"] = "00000000-0000-0000-0000-000000000001"
            ok, report = validate(cs, [])
            assert ok, (rel, report)
            for op in cs["ops"]:
                if op["op"] != "add_claim":
                    continue
                o = op["object"]
                o = o.get("entity") or o["value"]["@value"]
                q = (op.get("qualifiers") or {}).get(VER)
                out.add((op["subject"], op["predicate"], o, q))
                where[(op["subject"], op["predicate"], o, q)] = (rel, op["references"][0]["span"]["start_line"])
    return out, where


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "acme"
        shutil.copytree(Path(__file__).parent / "fixtures/polyglot", repo)
        files = list_files(repo)
        got, where = claims(repo)
    R = "urn:kk:repo:acme"
    py, web, go, rs = "pkg:pypi/acme-tools", "pkg:npm/%40acme/web", "pkg:golang/github.com/acme/svc", "pkg:cargo/acme-core"
    expect = {
        (R, "rdf:type", S + "SoftwareSourceCode", None), (R, "rdfs:label", "acme", None),
        (py, "rdf:type", S + "SoftwareSourceCode", None), (py, "rdfs:label", "Acme_Tools", None),
        (py, "owl:versionInfo", "1.4.2", None), (py, LANG, "Python", None), (py, "dcterms:isPartOf", R, None),
        (py, REQ, "pkg:pypi/requests", ">=2.31,<3"), (py, REQ, "pkg:pypi/pyyaml", "==6.0.2"),
        (py, REQ, "pkg:pypi/tomli", None), (py, SUG, "pkg:pypi/pytest", ">=8"),
        (f"{R}/requirements-dev.txt", REQ, "pkg:pypi/ruff", "==0.6.9"),   # the file requires it
        (f"{R}/requirements-dev.txt", "dcterms:isPartOf", py, None),       # and is part of the package beside it
        (web, "rdfs:label", "@acme/web", None), (web, "owl:versionInfo", "0.3.0", None), (web, LANG, "JavaScript", None),
        (web, REQ, "pkg:npm/react", "^18.3.1"), (web, SUG, "pkg:npm/vite", "5.4.8"),
        (go, "rdfs:label", "github.com/acme/svc", None), (go, LANG, "Go", None), (go, LANG, "Go 1.22", None),
        (go, REQ, "pkg:golang/github.com/lib/pq", "v1.10.9"), (go, REQ, "pkg:golang/golang.org/x/sync", "v0.8.0"),
        (go, REQ, "pkg:golang/github.com/google/uuid", "v1.6.0"),
        (rs, "owl:versionInfo", "0.2.0", None), (rs, LANG, "Rust", None),
        (rs, REQ, "pkg:cargo/serde", "1.0"), (rs, REQ, "pkg:cargo/anyhow", "1"), (rs, SUG, "pkg:cargo/proptest", "1.5"),
        (f"{R}/README.md", "dcterms:title", "Acme tools", None), (f"{R}/README.md", "dcterms:isPartOf", R, None),
        (f"{R}/README.md#install", "rdfs:label", "Install", None), (f"{R}/README.md#usage", "rdfs:label", "Usage", None),
        (f"{R}/README.md#usage", "dcterms:isPartOf", f"{R}/README.md", None),
        (f"{R}/docs/guide.md", "dcterms:title", "docs/guide.md", None),
        (f"{R}/docs/guide.md#configuring-the-service", "rdfs:label", "Configuring the service", None),
    }
    checks = [
        ("the expected claims are there", not (expect - got), sorted(expect - got)),
        ("dependency folders are skipped", not any("node_modules" in f for f in files), files),
        ("no package from node_modules", not any("leftpad" == c[2] for c in got), None),
        ("a heading inside a code fence is not a heading",
         not any(c[2] == "not a heading" for c in got), None),
        ("spans point at the line: requests in pyproject.toml line 9",
         where[(py, REQ, "pkg:pypi/requests", ">=2.31,<3")] == ("pyproject.toml", 9), None),
        ("spans point at the line: vite in web/package.json line 8",
         where[(web, SUG, "pkg:npm/vite", "5.4.8")] == ("web/package.json", 8), None),
    ]
    for name, ok, detail in checks:
        print(("ok  " if ok else "FAIL"), name, "" if ok or detail is None else detail)
    passed = all(ok for _, ok, _ in checks)
    print(f"repository encoder tests passed ({len(checks)}/{len(checks)}; {len(got)} claims from {len(files)} files)"
          if passed else "repository encoder tests FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
