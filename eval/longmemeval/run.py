"""LongMemEval on the kernel (M6-T10, D-89): does the kernel recall the right part of a long chat history?

  python eval/longmemeval/run.py fetch          download LongMemEval_S (cleaned), checked by sha256
  python eval/longmemeval/run.py load [--n N]   N questions (default 100, stratified by question type), each
                                                history into its own scope through the normal write path
  python eval/longmemeval/run.py retrieval [--n N] [--label L]
                                                each question through the kernel's memory recall (the code the
                                                MCP `recall` tool runs), as a login that can read only that
                                                question's history, scored as LongMemEval's own retrieval
                                                code does: recall_any, recall_all and NDCG at 5 and 10, by turn
                                                and by session (turns ranked, then collapsed to sessions)

Each question gets a scope and a reader role that may read only that scope (and the vocabulary), so the
kernel's row-level security keeps the histories apart, as it would keep two users' memories apart. A
turn is one claim: `urn:kk:lme:<question>:<session>:<turn>` as:content "<role>: <text>", valid from
the session's date. Identifiers are hashed: LongMemEval names its evidence sessions `answer_...`, which
must not reach the kernel or the ranking.

The data is MIT (github.com/xiaowu0162/LongMemEval): downloaded into KK_EVAL_CACHE, never committed.
Results go to eval/longmemeval/runs/<date>-<label>.json. Published retrieval numbers are on
LongMemEval_M (500 sessions a question, Table 9 of the paper); _S has about 48, so they compare only in
direction, not in value.

Env: KK_ADMIN_URL (a superuser: scopes, roles, and SET ROLE to the loader and readers), with `kk serve`
running against the same database (validator and recall; add dense to measure hybrid recall),
KK_EVAL_CACHE (default ~/.cache/knowledge-kernel/eval).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("KK_EVAL_CACHE") or Path.home() / ".cache/knowledge-kernel/eval") / "longmemeval"
DATA = CACHE / "longmemeval_s_cleaned.json"
URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"
SHA = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"   # pinned Oct 2026
AGENT, LOADER = "urn:kk:agent:eval-longmemeval", "kke_longmemeval"
CONTENT = "https://www.w3.org/ns/activitystreams#content"
BATCH = 1000


def h(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not DATA.exists():
        urllib.request.urlretrieve(URL, DATA.with_suffix(".part"))
        DATA.with_suffix(".part").rename(DATA)
    got = hashlib.sha256(DATA.read_bytes()).hexdigest()
    if got != SHA:
        sys.exit(f"{DATA}: sha256 {got}, expected {SHA}")
    print(json.dumps({"data": str(DATA), "sha256": got}))


def sample(n: int) -> list[dict]:
    """N questions, stratified by type (in proportion, at least one each), each type's in hash order."""
    data = json.loads(DATA.read_text())
    by_type = defaultdict(list)
    for q in sorted(data, key=lambda q: h(q["question_id"])):
        by_type[q["question_type"]].append(q)
    if n >= len(data):
        return sorted(data, key=lambda q: h(q["question_id"]))
    take = {t: max(1, round(n * len(qs) / len(data))) for t, qs in by_type.items()}
    return sorted((q for t, qs in by_type.items() for q in qs[:take[t]]), key=lambda q: h(q["question_id"]))


def scope(q: dict) -> str:
    return f"urn:kk:scope:lme:{h(q['question_id'])}"


def reader(q: dict) -> str:
    return f"kke_lme_{h(q['question_id'])}"


def when(stamp: str) -> str:
    """LongMemEval's '2023/05/20 (Sat) 02:21' as ISO 8601 (UTC; the data has no zone)."""
    return datetime.strptime(stamp.split(" (")[0] + " " + stamp.split(") ")[1], "%Y/%m/%d %H:%M") \
        .replace(tzinfo=timezone.utc).isoformat()


def turns(q: dict):
    """(subject, text, valid_from, session key, has_answer) for every turn of the question's history."""
    qh = h(q["question_id"])
    for sid, stamp, session in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
        sh = h(qh + sid)
        for i, t in enumerate(session):
            if t["content"].strip():
                yield f"urn:kk:lme:{qh}:{sh}:{i}", f"{t['role']}: {t['content']}", when(stamp), sh, \
                    bool(t.get("has_answer")), sid in q["answer_session_ids"]


def setup(admin, qs: list[dict]) -> None:
    from psycopg import sql
    admin.execute("""do $$ begin if not exists (select 1 from pg_roles where rolname = 'kke_longmemeval') then
                       create role kke_longmemeval nologin; end if; end $$""")
    admin.execute("grant kk_agent to kke_longmemeval")
    admin.execute("set role kk_owner")
    admin.execute("insert into kernel.agent(iri, db_role, kind, label) values (%s, %s, 'software', 'LongMemEval loader')"
                  " on conflict (iri) do nothing", (AGENT, LOADER))
    admin.execute("reset role")
    for q in qs:
        sc, role = scope(q), reader(q)
        admin.execute(sql.SQL("""do $$ begin if not exists (select 1 from pg_roles where rolname = {r}) then
                                   create role {i} nologin; end if; end $$""").format(r=sql.Literal(role),
                                                                                          i=sql.Identifier(role)))
        admin.execute(sql.SQL("grant kk_agent to {}").format(sql.Identifier(role)))
        admin.execute("insert into kernel.scope(iri, label) values (%s, %s) on conflict do nothing",
                      (sc, f"LongMemEval history {h(q['question_id'])}"))
        for r, write in ((LOADER, True), (role, False), ("kkm_validator", False), ("kkm_recall_index", False),
                         ("kkm_recall_dense", False)):
            admin.execute("""insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)
                             on conflict (scope, db_role) do nothing""", (sc, r, write))


def load(n: int) -> None:
    import psycopg
    from psycopg.types.json import Jsonb
    qs = sample(n)
    t0 = time.time()
    with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as admin:
        setup(admin, qs)
        admin.execute(f"set role {LOADER}")
        run = admin.execute("select api.start_run(null, null, '[]'::jsonb, %s)",
                            (Jsonb({"eval": "longmemeval", "n": n}),)).fetchone()[0]
        ids, claims = [], 0
        for q in qs:
            ops = [{"op": "add_claim", "subject": s, "predicate": CONTENT, "object": {"value": {"@value": text}},
                    "valid_from": vf, "evidence_type": "testimony",
                    "references": [{"source_uri": f"urn:kk:lme:{h(q['question_id'])}:{sh}"}]}
                   for s, text, vf, sh, _, _ in turns(q)]
            claims += len(ops)
            for i in range(0, len(ops), BATCH):
                cs = {"idempotency_key": f"lme:{h(q['question_id'])}:{i // BATCH}", "attributed_to": AGENT,
                      "generated_by": str(run), "target_scope": scope(q), "ops": ops[i:i + BATCH]}
                ids.append(admin.execute("select api.submit_change_set(%s)->>'id'", (Jsonb(cs),)).fetchone()[0])
        # Applied (the validator) and indexed (the recall index) before retrieval is measured.
        while (left := admin.execute("select count(*) from kernel.change_set where id = any(%s::uuid[])"
                                     " and status not in ('applied', 'invalid', 'rejected')", (ids,)).fetchone()[0]):
            print(f"waiting for the validator: {left} of {len(ids)} change sets", file=sys.stderr)
            time.sleep(5)
        bad = admin.execute("select count(*) from kernel.change_set where id = any(%s::uuid[]) and status <> 'applied'",
                            (ids,)).fetchone()[0]
        admin.execute("select api.finish_run(%s, 'succeeded', %s)", (run, Jsonb({"claims": claims})))
    print(json.dumps({"questions": len(qs), "claims": claims, "change_sets": len(ids), "not_applied": bad,
                      "seconds": round(time.time() - t0, 1)}))


def ndcg(labels: list[int], ideal: list[int], k: int) -> float:
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(labels[:k]))
    best = sum(rel / math.log2(i + 2) for i, rel in enumerate(sorted(ideal, reverse=True)[:k]))
    return dcg / best if best else 0.0


def score(ranked: list[str], gold: set[str], universe: list[str], k: int) -> dict:
    """LongMemEval's evaluate_retrieval: recall_any, recall_all and NDCG of the first k of `ranked`."""
    top = ranked[:k]
    return {"recall_any": float(any(g in top for g in gold)), "recall_all": float(gold <= set(top)),
            "ndcg": ndcg([int(x in gold) for x in top], [int(x in gold) for x in universe], k)}


def sessions(ranked_turns: list[str], k: int) -> list[str]:
    """Turns collapsed to sessions in rank order, until k sessions (LongMemEval's turn2session)."""
    out = []
    for t in ranked_turns:
        s = t.rsplit(":", 1)[0]
        if s not in out:
            out.append(s)
        if len(out) == k:
            break
    return out


def retrieval(n: int, label: str) -> dict:
    import psycopg

    from kernel_mcp import recall
    qs = [q for q in sample(n) if not q["question_id"].endswith("_abs")]   # abstention has no evidence
    rows, lat = [], []
    with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as admin:
        for q in qs:
            all_turns = list(turns(q))
            gold_turns = {s for s, _, _, _, ans, _ in all_turns if ans}
            gold_sessions = {s.rsplit(":", 1)[0] for s, _, _, _, _, in_ans in all_turns if in_ans}
            admin.execute(f"set role {reader(q)}")
            t0 = time.time()
            found = recall.recall(admin, q["question"], 50)
            lat.append(time.time() - t0)
            admin.execute("reset role")
            ranked = [c["subject"] for c in found if c.get("scope") == scope(q)]
            row = {"type": q["question_type"]}
            for k in (5, 10):
                if gold_turns:
                    row[f"turn@{k}"] = score(ranked, gold_turns, [s for s, *_ in all_turns], k)
                universe = list(dict.fromkeys(s.rsplit(":", 1)[0] for s, *_ in all_turns))
                row[f"session@{k}"] = score(sessions(ranked, k), gold_sessions, universe, k)
            rows.append(row)

    def mean(key, metric, subset=rows):
        vals = [r[key][metric] for r in subset if key in r]
        return round(statistics.mean(vals), 3) if vals else None
    metrics = {f"{key}.{m}": mean(key, m) for key in ("session@5", "session@10", "turn@5", "turn@10")
               for m in ("recall_any", "recall_all", "ndcg")}
    by_type = {t: {k: mean(k, "recall_any", [r for r in rows if r["type"] == t]) for k in ("session@5", "session@10")}
               for t in sorted({r["type"] for r in rows})}
    out = {"eval": "longmemeval_s retrieval", "label": label, "questions": len(rows), "metrics": metrics,
           "session_recall_any_by_type": by_type, "latency_s": {"p50": round(statistics.median(lat), 3),
                                                                 "p95": round(sorted(lat)[int(0.95 * len(lat))], 3)},
           "recall": os.environ.get("KK_RERANK", "default"), "date": date.today().isoformat(),
           "counts": dict(Counter(r["type"] for r in rows))}
    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "runs" / f"{date.today().isoformat()}-{label}.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=("fetch", "load", "retrieval"))
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--label", default="retrieval")
    a = ap.parse_args()
    if a.stage == "fetch":
        fetch()
    elif a.stage == "load":
        load(a.n)
    else:
        print(json.dumps(retrieval(a.n, a.label), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
