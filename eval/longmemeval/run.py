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
  python eval/longmemeval/run.py answer [--n N] [--k K] [--label L]
                                                end to end: the K sessions the kernel recalls first (default 5), in
                                                date order, read by a small model with the paper's reader prompt
                                                (KK_EVAL_MODEL, default liquid/lfm-2.5-2.6b:free on OpenRouter; key
                                                OPENROUTER_API_KEY or KK_EVAL_MODEL_KEY); answers to <cache>/answers/<L>.answers.jsonl
  python eval/longmemeval/run.py judge-prompts --label L
                                                the paper's judge prompt per answer (MIT, src/evaluation/
                                                evaluate_qa.py), as JSON lines for a judge: Haiku sub-agents write
                                                {"question_id", "verdict": "yes" | "no"} lines
  python eval/longmemeval/run.py score --label L --judgments FILE
                                                accuracy, overall and by question type, abstention included

Each question gets a scope and a reader role that may read only that scope (and the vocabulary), so the
kernel's row-level security keeps the histories apart, as it would keep two users' memories apart. A
turn is one claim: `urn:kk:lme:<question>:<session>:<turn>` as:content "<role>: <text>", valid from
the session's date. Identifiers are hashed: LongMemEval names its evidence sessions `answer_...`, which
must not reach the kernel or the ranking.

The data is MIT (github.com/xiaowu0162/LongMemEval): downloaded into KK_EVAL_CACHE, never committed.
Results go to eval/longmemeval/runs/<date>-<label>.json. The paper judges with GPT-4o; ours judges with
Haiku and reads with a model under 10B parameters, so accuracies compare with the paper only roughly. Published retrieval numbers are on
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


# The paper's prompts (LongMemEval, MIT, Copyright (c) 2024 Di Wu): src/generation/run_generation.py (reader,
# step by step, no chain of notes) and src/evaluation/evaluate_qa.py (judge, per question type).
READER = ("I will give you several history chats between you and a user. Please answer the question based on the "
          "relevant chat history. Answer the question step by step: first extract all the relevant information, and "
          "then reason over the information to get the answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\n"
          "Question: {}\nAnswer (step by step):")
_JUDGE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
          "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or "
          "contains all the intermediate steps to get the correct answer, you should also answer yes. If the response "
          "only contains a subset of the information required by the answer, answer no. ")
_TAIL = "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
JUDGE = {
    "single-session-user": _JUDGE + _TAIL, "single-session-assistant": _JUDGE + _TAIL, "multi-session": _JUDGE + _TAIL,
    "temporal-reasoning": _JUDGE + "In addition, do not penalize off-by-one errors for the number of days. If the "
    "question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting "
    "19 days when the answer is 18), the model's response is still correct. " + _TAIL,
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes "
    "if the response contains the correct answer. Otherwise, answer no. If the response contains some previous "
    "information along with an updated answer, the response should be considered as correct as long as the updated "
    "answer is the required answer." + _TAIL,
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a "
    "response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. "
    "The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls "
    "and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}"
    "\n\nIs the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
    "yes if the model correctly identifies the question as unanswerable. The model could say that the information is "
    "incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\n"
    "Explanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? "
    "Answer yes or no only.",
}


def ask(prompt: str) -> tuple[str, str]:
    """One completion from an OpenAI-compatible endpoint (as eval/scifact/run.py): (text, the model)."""
    import urllib.error
    url = os.environ.get("KK_EVAL_MODEL_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    key = os.environ.get("KK_EVAL_MODEL_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    body = json.dumps({"model": os.environ.get("KK_EVAL_MODEL", "liquid/lfm-2.5-2.6b:free"), "temperature": 0,
                       "max_tokens": 6000, "messages": [{"role": "user", "content": prompt}]}).encode()
    for attempt in range(6):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out = json.load(r)
            if out.get("choices"):
                return out["choices"][0]["message"]["content"] or "", out.get("model", "")
            if attempt == 5:
                return "", f"error: {str(out.get('error', out))[:80]}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if getattr(e, "code", 429) not in (429, 500, 502, 503) or attempt == 5:
                return "", f"error: {e}"
        time.sleep(2 ** attempt * 5)
    raise RuntimeError("unreachable")


def history(q: dict, ranked_sessions: list[str], k: int) -> str:
    """The first k recalled sessions, in date order, as the paper's reader sees them (natural language)."""
    qh = h(q["question_id"])
    by_key = {h(qh + sid): (stamp, s) for sid, stamp, s in
              zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"])}
    chosen = sorted((by_key[key] for key in ranked_sessions[:k] if key in by_key), key=lambda x: when(x[0]))
    out = ""
    for i, (stamp, session) in enumerate(chosen):
        text = "".join(f"\n\n{t['role']}: {t['content'].strip()}" for t in session)
        out += f"\n### Session {i + 1}:\nSession Date: {stamp}\nSession Content:\n{text}\n"
    return out


def answer(n: int, k: int, label: str) -> dict:
    import psycopg

    from kernel_mcp import recall
    qs = sample(n)
    path = CACHE / "answers" / f"{label}.answers.jsonl"
    path.parent.mkdir(exist_ok=True)
    done = {json.loads(line)["question_id"] for line in path.open()} if path.exists() else set()
    with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as admin, path.open("a") as out:
        for q in qs:
            if q["question_id"] in done:
                continue
            admin.execute(f"set role {reader(q)}")
            found = recall.recall(admin, q["question"], 60)
            admin.execute("reset role")
            ranked = sessions([c["subject"] for c in found if c.get("scope") == scope(q)], k)
            text, model = ask(READER.format(history(q, [s.rsplit(":", 1)[1] for s in ranked], k),
                                            q["question_date"], q["question"]))
            out.write(json.dumps({"question_id": q["question_id"], "hypothesis": text.strip(), "model": model}) + "\n")
            out.flush()
            print(f"{q['question_id']}: {model} {len(text)} chars", file=sys.stderr)
    return {"answers": str(path)}


def judge_prompts(label: str) -> None:
    data = {q["question_id"]: q for q in json.loads(DATA.read_text())}
    for line in (CACHE / "answers" / f"{label}.answers.jsonl").open():
        a = json.loads(line)
        q = data[a["question_id"]]
        kind = "abstention" if q["question_id"].endswith("_abs") else q["question_type"]
        print(json.dumps({"question_id": q["question_id"],
                          "prompt": JUDGE[kind].format(q["question"], q["answer"], a["hypothesis"])}))


def score_qa(label: str, judgments: str) -> dict:
    data = {q["question_id"]: q for q in json.loads(DATA.read_text())}
    answers = {json.loads(line)["question_id"]: json.loads(line) for line in (CACHE / "answers" / f"{label}.answers.jsonl").open()}
    verdicts = {}
    for line in Path(judgments).read_text().splitlines():
        if line.strip():
            j = json.loads(line)
            verdicts[j["question_id"]] = str(j["verdict"]).strip().lower().startswith("yes")
    rows = [(("abstention" if qid.endswith("_abs") else data[qid]["question_type"]), verdicts.get(qid, False))
            for qid in answers]
    by_type = defaultdict(list)
    for kind, ok in rows:
        by_type[kind].append(ok)
    failed = sum(1 for a in answers.values() if not a["hypothesis"])
    out = {"eval": "longmemeval_s qa", "label": label, "questions": len(rows),
           "accuracy": round(sum(ok for _, ok in rows) / len(rows), 3),
           "by_type": {t: {"n": len(v), "accuracy": round(sum(v) / len(v), 3)} for t, v in sorted(by_type.items())},
           "unanswered": failed, "unjudged": sum(1 for qid in answers if qid not in verdicts),
           "reader": sorted({a.get("model") or "" for a in answers.values()}), "judge": os.environ.get("KK_EVAL_JUDGE", "haiku"),
           "date": date.today().isoformat()}
    (HERE / "runs" / f"{date.today().isoformat()}-{label}-qa.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=("fetch", "load", "retrieval", "answer", "judge-prompts", "score"))
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--label", default="retrieval")
    ap.add_argument("--judgments")
    a = ap.parse_args()
    if a.stage == "fetch":
        fetch()
    elif a.stage == "load":
        load(a.n)
    elif a.stage == "answer":
        print(json.dumps(answer(a.n, a.k, a.label)))
    elif a.stage == "judge-prompts":
        judge_prompts(a.label)
    elif a.stage == "score":
        print(json.dumps(score_qa(a.label, a.judgments), indent=2))
    else:
        print(json.dumps(retrieval(a.n, a.label), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
