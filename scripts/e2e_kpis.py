"""D-119: process KPIs end to end, run by scripts/smoke_kernel.sh (no Docker, a fresh database of its own).

`kk demo` loads order-to-cash (the event log is an artifact); `kk serve` runs process-kpis with weekly windows.
Checks: the KPIs appear as observed claims on the process, each citing the log artifact; a person's targets
(a bbo:maxValue and a bbo:minValue claim by the owner) supersede the unmarked KPIs with ones marked met and missed,
none deleted; a recompute with another window (a one-shot of the module) supersedes again; the Processes view
shows each KPI with value, window, target, belief and claim (headless; skipped without playwright).
Needs a superuser (PG*), DATABASE_URL (kk_authenticator), KK_JWT_SECRET, gitleaks on PATH (kk demo)."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_demo import post, tool  # noqa: E402
from e2e_interview_demo import ROOT, check, checks, objects, sh, up  # noqa: E402
from urllib.parse import urlparse, urlunparse  # noqa: E402
import socket  # noqa: E402

P = "urn:kk:process:order-to-cash"
RESULT = "http://www.w3.org/ns/sosa/hasSimpleResult"
OBS = "http://www.w3.org/ns/sosa/observedProperty"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
STATUS = "urn:kk:property:target-status"


def until(fn, timeout=90):
    end = time.time() + timeout
    while True:
        got = fn()
        if got or time.time() > end:
            return got
        time.sleep(1)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = f"kk_kpis_{int(time.time())}"
        env = {**os.environ, "KK_HOME": f"{tmp}/kk-home", "KK_ADMIN_URL": f"dbname={db}", "KK_ARTIFACTS": f"{tmp}/artifacts",
               "KK_KPI_WINDOW": "week", "KK_SWEEP_SECONDS": "3"}
        sh("createdb", db)
        sh(sys.executable, "-m", "kk", "init", env=env)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        url = urlunparse(urlparse(os.environ["DATABASE_URL"])._replace(path="/" + db))
        serve = subprocess.Popen([sys.executable, "-m", "kk", "serve", "--port", str(port), "--only", "mcp,validator,belief,kpis"],
                                 env={**env, "DATABASE_URL": url}, stdout=open(f"{tmp}/serve.log", "w"), stderr=subprocess.STDOUT)
        sql = lambda q: [r.split("|") for r in sh("psql", "-d", db, "-tAc", q).stdout.split("\n") if r]  # noqa: E731
        kpis = lambda extra="": sql(  # noqa: E731
            "select c.id, c.qualifiers->>'%s', c.object_value->>'@value', c.qualifiers->>'%s', c.status "
            "from api.claims c where c.subject = '%s' and c.predicate = '%s' and c.evidence_type = 'observed' "
            "and c.qualifiers->>'%s' like 'urn:kk:property:%%' and c.attributed_to = 'urn:kk:agent:process-kpis' %s"
            % (OBS, STATUS, P, RESULT, OBS, extra))
        try:
            for _ in range(60):
                if serve.poll() is not None or up(base):
                    break
                time.sleep(1)
            if serve.poll() is not None:
                raise SystemExit(f"kk serve did not start: {Path(tmp, 'serve.log').read_text()[-1500:]}")
            host = f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"
            first = objects(sh(sys.executable, "-m", "kk", "demo", "--project", f"{tmp}/kk-demo", "--agent", "demo", "--no-stack",
                               "--db-host", host, env=env).stdout)[0]
            live = until(lambda: kpis("and c.status <> 'deprecated'"))
            kinds = {r[1].rsplit(":", 1)[1] for r in live}
            check("D-119 the KPIs appear as observed claims on the process, each kind per window",
                  {"cases-in-window", "cycle-time-median-seconds", "cycle-time-p90-seconds", "rework-rate", "throughput-per-day"} <= kinds
                  and "cost-per-case" not in kinds, f"{len(live)} claims; {sorted(kinds)}")
            cites = sql("select count(*) from api.claim_references r join api.claims c on c.id = r.claim_id "
                        f"where c.attributed_to = 'urn:kk:agent:process-kpis' and r.artifact in (select artifact from api.claim_references "
                        f"where claim_id in (select id from api.claims where predicate = '{RESULT}' and attributed_to = 'urn:kk:agent:encoder-eventlog'))")
            check("D-119 every KPI cites the event-log artifact the encoder stored", int(cites[0][0]) == len(live), f"{cites[0][0]} of {len(live)}")
            cases = sum(int(r[2]) for r in live if r[1].endswith("cases-in-window"))
            check("D-119 the weekly case counts add up to the log's 40 cases", cases == 40, str(cases))
            check("D-119 no target, no mark", all(not r[3] for r in live))

            token = post(base + "/ui/login", first["owner_sign_in"])["token"]
            me = "urn:kk:agent:person:owner"
            run = tool(base, token, "start_run")
            stated = lambda prop, pred, v: {"op": "add_claim", "subject": P, "predicate": pred,  # noqa: E731
                                            "object": {"value": {"@value": v, "@type": "http://www.w3.org/2001/XMLSchema#integer"}},
                                            "qualifiers": {OBS: "urn:kk:property:" + prop}, "evidence_type": "testimony",
                                            "references": [{"source_uri": me}]}
            tool(base, token, "submit_change_set", change_set={
                "idempotency_key": "kpis-targets", "attributed_to": me, "generated_by": run, "target_scope": "urn:kk:scope:default",
                "ops": [stated("cycle-time-p90-seconds", BBO + "maxValue", 30 * 86400), stated("throughput-per-day", BBO + "minValue", 100)]})
            tool(base, token, "finish_run", run_id=run)
            marked = until(lambda: [r for r in kpis("and c.status <> 'deprecated'") if r[3]] and kpis("and c.status <> 'deprecated'"))
            by = {k: {r[3] for r in marked if r[1].endswith(k)} for k in ("cycle-time-p90-seconds", "throughput-per-day", "rework-rate")}
            check("D-119 a person's targets mark the KPIs: the p90 within its maximum is met, the throughput under its minimum is missed",
                  by["cycle-time-p90-seconds"] == {"met"} and by["throughput-per-day"] == {"missed"} and by["rework-rate"] == {""}, str(by))
            old = sql("select count(*), count(s.replaced_by) from api.claims c join kernel.claim_status s on s.claim_id = c.id and s.status = 'deprecated' "
                      f"where c.status = 'deprecated' and c.attributed_to = 'urn:kk:agent:process-kpis' and c.subject = '{P}'")[0]
            check("D-119 the unmarked values are superseded by the marked ones, none deleted", int(old[0]) == len(live) and int(old[1]) == len(live), str(old))

            if subprocess.run(["node", "-e", "require('playwright')"], cwd=ROOT, capture_output=True).returncode != 0:
                if os.environ.get("KK_REQUIRE_UI") == "1":
                    check("D-119 the headless UI check needs Node and playwright", False)
                else:
                    print("skip the KPI view check (no playwright)")
            else:
                tok = sh(sys.executable, "-m", "kk", "token", "person_owner", "--days", "0.01",
                         env={**env, "KK_JWT_SECRET": os.environ["KK_JWT_SECRET"]}).stdout.strip()
                out = subprocess.run(["node", "scripts/e2e_kpis.mjs"], cwd=ROOT, env={**env, "KK_URL": base, "KK_OWNER_TOKEN": tok})
                check("D-119 the Processes view shows each KPI with value, window, target, belief and claim link", out.returncode == 0)

            before = {r[0] for r in kpis("and c.status <> 'deprecated'")}
            run_env = {**env, "DATABASE_URL": url, "KK_KPI_WINDOW": "month"}
            sh(sys.executable, "-m", "process_kpis", "--once", env=run_env)
            after = kpis("and c.status <> 'deprecated'")
            check("D-119 a recompute with another window supersedes the earlier values",
                  not ({r[0] for r in after} & before) and {r[2] for r in after if r[1].endswith("cases-in-window")} == {"40"},
                  f"{len(before)} -> {len(after)} claims (one month window of 40 cases)")
            sh(sys.executable, "-m", "process_kpis", "--once", env=run_env)
            check("D-119 the same recompute again changes nothing", {r[0] for r in kpis("and c.status <> 'deprecated'")} == {r[0] for r in after})
        finally:
            serve.terminate()
            time.sleep(1)
            if not os.environ.get("KK_KEEP_DB"):
                subprocess.run(["dropdb", "--if-exists", "--force", db], capture_output=True)
    print(f"D-119 process KPIs passed ({sum(checks)}/{len(checks)})" if all(checks) else "D-119 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
