"""Compose actuator (the Execute step of MAPE-K, D-53): carries out approved plan steps from a fixed
action list (scale, restart) on the services of one compose project, through a Docker socket proxy that
allows only container endpoints. It never writes claims: each step it carries out (or refuses) is a run
of this module with `plan_step` the step and `used` its claims, finished succeeded or failed with what it
did; the metrics observer records the effect.

  python -m actuator_compose [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_actuator_compose), DOCKER_HOST (default tcp://docker-proxy:2375), KK_COMPOSE_PROJECT (the project
whose services it acts on), KK_ACTUATOR_SERVICES (comma-separated allow list), KK_MAX_REPLICAS (default 5),
KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from kk.lib import connect, listen, run

from .docker import Docker
from .plan import PPLAN_STEP_OF, refusal, steps

MODULE = "urn:kk:module:actuator-compose"
AGENT = "urn:kk:agent:actuator-compose"


def one_pass(conn) -> dict:
    project = os.environ["KK_COMPOSE_PROJECT"]
    allowed = {s.strip() for s in os.environ.get("KK_ACTUATOR_SERVICES", "").split(",") if s.strip()}
    max_replicas = int(os.environ.get("KK_MAX_REPLICAS", "5"))
    with conn.cursor() as cur:
        cur.execute("""select id, subject, predicate, object_entity, object_value, change_set from api.claims
                       where status <> 'deprecated' and subject in
                         (select subject from api.claims where predicate = %s and status <> 'deprecated')""",
                    (PPLAN_STEP_OF,))
        cols = [d.name for d in cur.description]
        claims = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("""select id::text, 'review', decided_by, submitted_by, 'applied' from api.review_decisions
                       where id = any(%s::uuid[])""", (sorted({str(c["change_set"]) for c in claims}),))
        sets = {r[0]: dict(zip(("lane", "decided_by", "submitted_by", "status"), r[1:])) for r in cur.fetchall()}
        cur.execute("select distinct plan_step from kernel.run where agent = %s and plan_step is not null", (AGENT,))
        done = {r[0] for r in cur.fetchall()}
        report = {}
        for s in steps(claims, sets):
            if s["step"] in done or not s["approved"]:
                continue                                # unapproved steps wait; they are not refused yet
            why = refusal(s, allowed, max_replicas)
            try:
                with run(cur, MODULE, {"action": s["action"], "service": s["service"], "replicas": s["replicas"],
                                       "plan": s["plan"]}, plan_step=s["step"], used=s["claims"]) as (_, outputs):
                    if why:
                        outputs.update(refused=why)
                        raise PermissionError(why)
                    d = Docker()
                    outputs.update(d.scale(project, s["service"], s["replicas"]) if s["action"] == "scale"
                                   else d.restart(project, s["service"]))
                report[s["step"]] = {"status": "succeeded", **outputs}
            except Exception as e:                      # recorded as a failed run; go on with the next step
                report[s["step"]] = {"status": "failed", "error": str(e)[:300]}
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Carry out approved plan steps (scale, restart).")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_actuator_compose"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, lambda c: print(json.dumps(one_pass(c)), flush=True),
           wake=lambda payload: json.loads(payload).get("status") == "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
