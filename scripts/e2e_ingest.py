"""Ingestion without a runner (T13), run by scripts/smoke_kernel.sh after the kernel checks.

1. Scan this repository with the self-encoder command (ingest filter first); module change sets wait in
   the review lane and the owner approves them; the self-model then has every module, and every claim
   is referenced (G2's core checks).
2. Re-ingest a changed copy: a bumped module version supersedes the old version claim (replaced_by the
   new one); a removed file's claims are retracted; a file with a secret is blocked and never stored.

Env: DATABASE_URL (kk_authenticator), AGENT_DATABASE_URL (reads), OWNER_DATABASE_URL (review); gitleaks
on PATH; the knowledge-kernel package installed (pip install -e .).
"""
import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from self_encoder.encode import list_files

ROOT = Path(__file__).resolve().parents[1]

ENCODER = "urn:kk:agent:self-encoder"
VERSION = "http://www.w3.org/2002/07/owl#versionInfo"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def scan(repo: Path, artifacts: str) -> dict:
    out = subprocess.run([sys.executable, "-m", "kk", "scan", "--repo", str(repo), "--artifacts", artifacts],
                         capture_output=True, text=True, env={**os.environ, "KK_ROLE": "kkm_self_encoder"})
    if out.returncode != 0:
        raise SystemExit(f"scan failed: {out.stderr[-1500:]}")
    return json.loads(out.stdout)


def settle():
    """Wait until the validator has judged every submitted encoder change set."""
    for _ in range(240):
        if "submitted" not in statuses():
            return
        time.sleep(0.25)


def approve_encoder_sets() -> str:
    settle()
    out = subprocess.run([sys.executable, "-m", "kk", "review", "approve", "--all", "--from", ENCODER,
                          "--note", "smoke test: self-model"], capture_output=True, text=True,
                         env={**os.environ, "KK_DATABASE_URL": os.environ["OWNER_DATABASE_URL"]})
    return (out.stdout + out.stderr).strip()


def q(sql, *args):
    with psycopg.connect(os.environ["AGENT_DATABASE_URL"]) as conn:
        return conn.execute(sql, args).fetchall()


def as_encoder(sql, *args):
    """Reads as the encoder's role: change sets are visible only to the agent that submitted them."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("set role kkm_self_encoder")
        return conn.execute(sql, args).fetchall()


def statuses():
    return dict(as_encoder("select status, count(*) from api.change_sets where attributed_to = %s"
                           " group by status", ENCODER))


def main() -> int:
    with tempfile.TemporaryDirectory() as artifacts:
        first = scan(ROOT, artifacts)
        check("scan: every listed file encoded, nothing blocked in this repository",
              first["encoded"] == first["files"] and not first["blocked"], json.dumps(first["change_sets"]))
        approve_encoder_sets()
        st = statuses()
        check("module and vocabulary change sets approved by the owner; nothing left pending",
              set(st) == {"applied"}, json.dumps(st))
        manifests = len([p for p in ROOT.glob("modules/*/module.yaml") if "_template" not in str(p)])
        modules = q("select count(distinct subject) from api.claims where predicate = %s and object_entity = %s"
                    " and status <> 'deprecated'", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
                    "https://w3id.org/knowledge-kernel/ns#Module")[0][0]
        check("every module manifest is in the self-model", modules == manifests, f"{modules}/{manifests}")
        unreferenced = q("""select count(*) from api.claims c where c.attributed_to = %s
                            and not exists (select 1 from api.claim_references r where r.claim_id = c.id)""", ENCODER)
        check("every encoder claim references its file artifact", unreferenced == [(0,)], str(unreferenced))
        again = scan(ROOT, artifacts)
        settle()
        check("re-scanning an unchanged repository changes nothing", statuses() == st,
              json.dumps(again["change_sets"]))

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in list_files(ROOT):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / rel, repo / rel)
            manifest = repo / "modules/belief-corroboration/module.yaml"
            text = manifest.read_text()
            old_version = next(line.split(": ")[1] for line in text.splitlines() if line.startswith("version: "))
            manifest.write_text(text.replace(f"version: {old_version}", "version: 9.9.9", 1))
            (repo / "modules/ingest-filter-gitleaks/migration.sql").unlink()
            token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
            leaky = repo / "modules/validator-shacl/migration.sql"
            leaky.write_text(leaky.read_text() + f"\n-- github_token: {token}\n")

            third = scan(repo, artifacts)
            check("the file with a secret is blocked and named without the secret",
                  [b["path"] for b in third["blocked"]] == ["modules/validator-shacl/migration.sql"]
                  and token not in json.dumps(third), json.dumps(third["blocked"]))
            check("the blocked file's bytes are not stored",
                  not any(token.encode() in p.read_bytes() for p in Path(artifacts).iterdir()))
            print("   ", approve_encoder_sets().splitlines()[-1])
            current = q("select object_value->>'@value' from api.claims where subject = %s and predicate = %s"
                        " and status <> 'deprecated'", "urn:kk:module:belief-corroboration-v0", VERSION)
            check("the changed version supersedes the old one", current == [("9.9.9",)], str(current))
            history = q("""select api.explain(c.id)->'status' from api.claims c
                           where c.subject = %s and c.predicate = %s and c.object_value->>'@value' = %s""",
                        "urn:kk:module:belief-corroboration-v0", VERSION, old_version)
            last = history[0][0][-1] if history and history[0][0] else {}
            check("the old version claim is deprecated as superseded, pointing at its replacement",
                  last.get("status") == "deprecated" and last.get("reason") == "superseded" and last.get("replaced_by"),
                  json.dumps(last)[:160])
            removed = as_encoder("""select count(*) filter (where c.status = 'deprecated'), count(*) from api.claims c
                           join api.change_sets s on s.id = c.change_set
                           where s.idempotency_key like 'self-encoder:modules/ingest-filter-gitleaks/migration.sql:%%'""")
            check("the removed file's claims are retracted", removed[0][1] > 0 and removed[0][0] == removed[0][1],
                  f"{removed[0][0]}/{removed[0][1]} deprecated")
    print("T13 ingestion", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
    return 0 if all(checks) else 1


sys.exit(main())
