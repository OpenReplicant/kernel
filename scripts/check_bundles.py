"""The committed bundles (distributions/<name>/bundle/) are what `kk distribution bundle` generates, and are sound.

Per distribution: the folder equals a fresh generation (and a second generation changes nothing); compose.yaml
parses, has no `build:`, runs exactly db, init, kernel and caddy, publishes ports on 127.0.0.1 only, uses named
volumes for data and takes images by tag from GHCR (Caddy aside); .env.example leaves every secret empty;
no file holds anything that looks like a secret. Needs PyYAML only. Usage: python scripts/check_bundles.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kk.bundle import DEFAULT_TAG, KERNEL_IMAGE, write  # noqa: E402
from kk.distributions import names  # noqa: E402

FILES = ["compose.yaml", "Caddyfile", ".env.example", "README.md"]
SECRETS = ("POSTGRES_PASSWORD", "KK_AUTHENTICATOR_PASSWORD", "KK_JWT_SECRET")


def check(name: str) -> list[str]:
    errors = []
    committed = ROOT / "distributions" / name / "bundle"
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        write(name, fresh, DEFAULT_TAG)
        if write(name, fresh, DEFAULT_TAG):
            errors.append("generating twice changes files (not idempotent)")
        for f in FILES:
            if not (committed / f).exists():
                errors.append(f"{f} missing: run kk distribution bundle {name} --out distributions/{name}/bundle")
            elif (committed / f).read_text() != (fresh / f).read_text():
                errors.append(f"{f} differs from what kk distribution bundle generates")
        extra = sorted(p.name for p in committed.iterdir() if p.name not in FILES) if committed.exists() else []
        if extra:
            errors.append(f"unexpected files in the bundle: {extra}")
    c = yaml.safe_load((committed / "compose.yaml").read_text()) if (committed / "compose.yaml").exists() else None
    if not c:
        return errors
    if c.get("name") != f"kk-{name}":
        errors.append(f"project name {c.get('name')!r}, want kk-{name}")
    if set(c["services"]) != {"db", "init", "kernel", "caddy"}:
        errors.append(f"services {sorted(c['services'])}")
    for s, v in c["services"].items():
        if "build" in v:
            errors.append(f"{s}: has build:")
        if not v.get("image"):
            errors.append(f"{s}: no image")
        if s != "caddy" and "ghcr.io/openreplicant/" not in v.get("image", ""):
            errors.append(f"{s}: image {v.get('image')} is not from GHCR")
        for port in v.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                errors.append(f"{s}: port {port} not bound to 127.0.0.1")
        for vol in v.get("volumes", []):
            src = vol.split(":")[0]
            if src.startswith(("/", "..", "~")) or (src.startswith(".") and not src.startswith("./Caddyfile")):
                errors.append(f"{s}: volume {vol} reaches the host")
    if f"{KERNEL_IMAGE}" not in c["services"]["kernel"]["image"] or not c["services"]["db"]["volumes"][0].startswith("db-data:"):
        errors.append("kernel image or db volume wrong")
    if not set(c["volumes"]) >= {"db-data", "kk-artifacts", "kk-chats"}:
        errors.append("named volumes missing")
    env = (committed / ".env.example").read_text()
    for k in SECRETS:
        if not re.search(rf"^{k}=$", env, re.M):
            errors.append(f".env.example: {k} must be present and empty")
    for f in FILES:
        text = (committed / f).read_text() if (committed / f).exists() else ""
        if re.search(r"(?i)(password|secret)\w*=[A-Za-z0-9+/_-]{16,}", text):
            errors.append(f"{f}: looks like a secret")
        if re.search(r"/home/|/root/|/Users/", text):
            errors.append(f"{f}: a path from the machine that generated it")
    return errors


def main() -> int:
    bad = 0
    for n in names():
        errs = check(n)
        bad += len(errs)
        for e in errs:
            print("ERROR", n, e)
    print(f"{len(names())} bundles checked, {bad} errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
