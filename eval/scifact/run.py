"""SciFact on the kernel (M3-T3, D-59): is the kernel a good base for scientific belief?

  python eval/scifact/run.py fetch       download SciFact and its BEIR split, checked by sha256
  python eval/scifact/run.py encode      the 5,183 abstracts into the kernel (paper encoder, as its role)
  python eval/scifact/run.py retrieval   stage 1, no model: the 300 labelled claims through the kernel's
                                         recall, scored as BEIR does (nDCG@10, recall@100), with latency
  python eval/scifact/run.py prompts [--n N] [--k K]
                                         stage 2 inputs: each claim with the top K abstracts the kernel
                                         recalls (sentences numbered), and the same claim alone (closed book)
  python eval/scifact/run.py verify [--n N] [--k K] [--answers FILE]
                                         stage 2: a model labels each (claim, abstract) SUPPORT, CONTRADICT or
                                         NOT_ENOUGH_INFO with the sentences it rests on, quoted; and each claim
                                         alone. Scored with SciFact's abstract-level metrics, claim-level
                                         accuracy with and without the kernel, and quote fidelity (quotes
                                         found verbatim in the abstract). The model: any OpenAI-compatible
                                         endpoint (KK_EVAL_MODEL_URL, default OpenRouter; KK_EVAL_MODEL,
                                         default google/gemma-4-26b-a4b-it:free; key OPENROUTER_API_KEY or
                                         KK_EVAL_MODEL_KEY), or answers produced elsewhere (--answers, the
                                         JSON lines `prompts` describes, e.g. by a harness sub-agent)
  python eval/scifact/run.py belief --answers FILE [--n N]
                                         stage 3: those verdicts recorded as claims (`<paper> cito:supports or
                                         cito:refutes <proposition>`, extracted, with the quotes), then the
                                         belief model's belief in each proposition against SciFact's label
                                         (AUC supported vs contradicted, sign accuracy)

The data is CC BY-NC 2.0 (SciFact): downloaded into KK_EVAL_CACHE for measurement, never committed.
Results go to eval/scifact/runs/<date>-<stage>.json.

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_papers, for encode), AGENT_DATABASE_URL (the
agent's own login, for retrieval), KK_EVAL_CACHE (default ~/.cache/knowledge-kernel/eval).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("KK_EVAL_CACHE") or Path.home() / ".cache/knowledge-kernel/eval") / "scifact"
FILES = {  # pinned Oct 2026
    "data.tar.gz": ("https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz",
                    "11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be"),
    "beir.zip": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
                 "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"),
}
SPLIT = os.environ.get("KK_EVAL_SPLIT", "dev")   # dev (compared with published results) or train (for tuning)
CORPUS, CLAIMS, QRELS = CACHE / "data/corpus.jsonl", CACHE / f"data/claims_{SPLIT}.jsonl", CACHE / "scifact/qrels/test.tsv"
SOURCE = "scifact"


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, (url, sha) in FILES.items():
        path = CACHE / name
        if not path.exists():
            urllib.request.urlretrieve(url, path)
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != sha:
            sys.exit(f"{name}: sha256 {got}, expected {sha}")
    with tarfile.open(CACHE / "data.tar.gz") as t:
        t.extractall(CACHE, filter="data")
    zipfile.ZipFile(CACHE / "beir.zip").extractall(CACHE)
    print(json.dumps({"cache": str(CACHE), "papers": sum(1 for _ in open(CORPUS)), "claims": sum(1 for _ in open(CLAIMS))}))


def encode() -> None:
    t0 = time.time()
    out = subprocess.run([sys.executable, "-m", "paper_encoder", "--source", SOURCE, "--artifacts",
                          str(CACHE / "artifacts"), str(CORPUS)], capture_output=True, text=True,
                         env={**os.environ, "KK_ROLE": "kkm_encoder_papers"})
    if out.returncode:
        sys.exit(out.stderr[-2000:])
    res = json.loads(out.stdout)
    submitted = time.time() - t0
    import psycopg
    with psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True) as db:   # applied = visible to an agent
        while True:
            applied = db.execute("select count(*) from api.claims where subject like %s and belief is not null",
                                 (f"urn:kk:paper:{SOURCE}:%",)).fetchone()[0]
            if applied >= res["claims"]:
                break
            time.sleep(2)
    res.update(submit_seconds=round(submitted, 1), applied_and_scored_seconds=round(time.time() - t0, 1))
    print(json.dumps({k: v for k, v in res.items() if k != "change_sets"}))


def ndcg10(ranked: list[str], relevant: set[str]) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:10]) if d in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    return dcg / ideal if ideal else 0.0


def retrieval(limit: int = 200) -> dict:
    import psycopg
    sys.path.insert(0, str(HERE.parents[1] / "modules/kernel-mcp"))
    from kernel_mcp import recall
    claims = {json.loads(l)["id"]: json.loads(l)["claim"] for l in open(CLAIMS)}
    qrels: dict[str, set[str]] = {}
    for line in open(QRELS).read().splitlines()[1:]:
        q, d, _ = line.split("\t")
        qrels.setdefault(q, set()).add(d)
    prefix = f"urn:kk:paper:{SOURCE}:"
    scores, recalls, ms = [], [], []
    with psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True) as db:
        indexed = recall.indexed(db)
        model = recall.dense(db) if indexed else None
        for q, rel in sorted(qrels.items(), key=lambda kv: int(kv[0])):
            t0 = time.perf_counter()   # includes embedding the query, as search does
            rows = recall.recall(db, claims[int(q)], limit)
            ms.append((time.perf_counter() - t0) * 1000)
            ranked = list(dict.fromkeys(r["subject"][len(prefix):] for r in rows if r["subject"].startswith(prefix)))
            scores.append(ndcg10(ranked, rel))
            recalls.append(len(rel & set(ranked)) / len(rel))
    ms.sort()
    out = {"stage": "retrieval", "queries": len(scores), "ndcg@10": round(statistics.mean(scores), 4),
           f"recall@{limit}claims": round(statistics.mean(recalls), 4),
           "latency_ms": {"p50": round(ms[len(ms) // 2]), "p95": round(ms[int(len(ms) * 0.95)]), "max": round(ms[-1])},
           "baseline": {"bm25_ndcg@10": 0.665, "source": "BEIR (Thakur et al. 2021)"},
           "recall": f"api.recall_hybrid: BM25 and {model} (recall-dense), min-max normalised, averaged" if model else
                     "api.recall: BM25 over the recall-index module (k1 1.2, b 0.75)" if indexed else
                     "kernel_mcp.recall.RANKED (Postgres full-text search, OR of lexemes, ts_rank_cd, no index)"}
    if indexed and recall.reranker():
        out["recall"] += f"; top {recall.POOL} claims re-ranked per subject by {recall.RERANKER} (cross-encoder)"
    return out


# ---------------------------------------------------------------- stage 2: verification
LABELS = ("SUPPORT", "CONTRADICT", "NOT_ENOUGH_INFO")
ASK_EVIDENCE = """You check a scientific claim against one abstract.
Claim: {claim}
Abstract (numbered sentences):
{sentences}
Does the abstract SUPPORT the claim, CONTRADICT it, or give NOT_ENOUGH_INFO? If it supports or contradicts,
quote the sentences it rests on, exactly as written. Answer with JSON only:
{{"label": "SUPPORT" | "CONTRADICT" | "NOT_ENOUGH_INFO", "quotes": ["exact sentence", ...]}}"""
# The strict prompt (D-64) follows SciFact's annotation rule: an abstract is evidence only if it directly
# tests the claim; related findings and background are not.
ASK_STRICT = """You check a scientific claim against one abstract, as SciFact's annotators did.
Claim: {claim}
Abstract (numbered sentences):
{sentences}
Answer SUPPORT or CONTRADICT only if the abstract itself reports a finding that directly tests this
claim: the same intervention or exposure, the same outcome, and a population the claim covers, so that
the finding alone shows the claim true or false. Otherwise answer NOT_ENOUGH_INFO: when the abstract is
on the topic but tests something else, gives background or cites other work, reports a related but
different measure, or would need extra assumptions. For SUPPORT or CONTRADICT, quote the one to three
sentences that carry the finding, exactly as written. Answer with JSON only:
{{"label": "SUPPORT" | "CONTRADICT" | "NOT_ENOUGH_INFO", "quotes": ["exact sentence", ...]}}"""
ASK_CLOSED = """Is this scientific claim true? Use what you know; no sources are given.
Claim: {claim}
Answer with JSON only: {{"label": "SUPPORT" | "CONTRADICT" | "NOT_ENOUGH_INFO"}}
(SUPPORT: true; CONTRADICT: false; NOT_ENOUGH_INFO: you cannot tell.)"""


def corpus() -> dict[str, list[str]]:
    return {str(json.loads(l)["doc_id"]): json.loads(l)["abstract"] for l in open(CORPUS)}


def sample(n: int) -> list[dict]:
    """The first n labelled claims, in id order (deterministic)."""
    return sorted((json.loads(l) for l in open(CLAIMS)), key=lambda c: c["id"])[:n]


def prompts(n: int, k: int) -> list[dict]:
    """One item per (claim, recalled abstract) and one closed-book item per claim."""
    import psycopg
    sys.path.insert(0, str(HERE.parents[1] / "modules/kernel-mcp"))
    from kernel_mcp import recall
    abstracts, prefix, items = corpus(), f"urn:kk:paper:{SOURCE}:", []
    with psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True) as db:
        for c in sample(n):
            rows = recall.recall(db, c["claim"], 60)
            docs = list(dict.fromkeys(r["subject"][len(prefix):] for r in rows if r["subject"].startswith(prefix)))[:k]
            for d in docs:
                text = "\n".join(f"[{i}] {s}" for i, s in enumerate(abstracts[d]))
                ask_evidence = ASK_STRICT if os.environ.get("KK_EVAL_PROMPT") == "strict" else ASK_EVIDENCE
                items.append({"key": f"{c['id']}|{d}", "claim_id": c["id"], "doc": d, "rank": docs.index(d) + 1,
                              "prompt": ask_evidence.format(claim=c["claim"], sentences=text)})
            items.append({"key": f"{c['id']}|closed", "claim_id": c["id"], "doc": None,
                          "prompt": ASK_CLOSED.format(claim=c["claim"])})
    return items


def ask(prompt: str) -> tuple[str, str]:
    """One completion from an OpenAI-compatible endpoint: (text, the model that answered)."""
    url = os.environ.get("KK_EVAL_MODEL_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    key = os.environ.get("KK_EVAL_MODEL_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    body = json.dumps({"model": os.environ.get("KK_EVAL_MODEL", "google/gemma-4-26b-a4b-it:free"), "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    for attempt in range(6):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.load(r)
            if out.get("choices"):
                return out["choices"][0]["message"]["content"] or "", out.get("model", "")
            if attempt == 5:                    # an error inside a 200 answer (upstream limits): no verdict
                return "", f"error: {str(out.get('error', out))[:80]}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if getattr(e, "code", 429) not in (429, 500, 502, 503) or attempt == 5:
                return "", f"error: {e}"
        time.sleep(2 ** attempt * 5)
    raise RuntimeError("unreachable")


def parse(text: str) -> dict:
    """The JSON object in a model's answer; NOT_ENOUGH_INFO when there is none."""
    a, b = text.find("{"), text.rfind("}")
    try:
        out = json.loads(text[a:b + 1])
    except ValueError:
        return {"label": "NOT_ENOUGH_INFO", "quotes": [], "unparsed": True}
    label = str(out.get("label", "")).upper().replace(" ", "_")
    return {"label": label if label in LABELS else "NOT_ENOUGH_INFO",
            "quotes": [q for q in out.get("quotes", []) if isinstance(q, str)]}


def f1(p: float, r: float) -> float:
    return round(2 * p * r / (p + r), 4) if p + r else 0.0


def score(claims: list[dict], abstracts: dict[str, list[str]], answers: dict[str, dict]) -> dict:
    """SciFact abstract-level (label only, and with rationale), claim-level accuracy, quote fidelity."""
    gold_pairs = {(c["id"], d): ev for c in claims for d, ev in c["evidence"].items()}
    predicted = correct_label = correct_rationale = 0
    quotes = found = 0
    claim_hits = {"kernel": 0, "closed": 0}
    with_evidence = 0
    for c in claims:
        verdicts = []
        for key, ans in answers.items():
            cid, doc = key.split("|")
            if int(cid) != c["id"] or doc == "closed":
                continue
            sents = abstracts[doc]
            idx = set()
            for q in ans["quotes"]:
                quotes += 1
                hit = next((i for i, s in enumerate(sents) if q.strip() and (q.strip() in s or s in q)), None)
                if hit is not None:
                    found += 1
                    idx.add(hit)
            if ans["label"] == "NOT_ENOUGH_INFO":
                continue
            # As SciFact's official scorer: at most the first three predicted rationale sentences count.
            idx = set(sorted(idx, key=lambda i: next(n for n, q in enumerate(ans["quotes"])
                                                     if q.strip() and (q.strip() in sents[i] or sents[i] in q)))[:3])
            verdicts.append(ans["label"])
            predicted += 1
            gold = gold_pairs.get((c["id"], doc))
            if gold and gold[0]["label"] == ans["label"]:
                correct_label += 1
                if any(set(r["sentences"]) <= idx for r in gold):
                    correct_rationale += 1
        if c["evidence"]:
            with_evidence += 1
            truth = next(iter(c["evidence"].values()))[0]["label"]
            mine = max(set(verdicts), key=verdicts.count) if verdicts else "NOT_ENOUGH_INFO"
            claim_hits["kernel"] += mine == truth
            claim_hits["closed"] += answers.get(f"{c['id']}|closed", {}).get("label") == truth
    gold_n = len(gold_pairs)
    pl, rl = (correct_label / predicted if predicted else 0), correct_label / gold_n
    pr, rr = (correct_rationale / predicted if predicted else 0), correct_rationale / gold_n
    return {"claims": len(claims), "claims_with_evidence": with_evidence, "pairs_gold": gold_n,
            "pairs_predicted": predicted,
            "abstract_label_only": {"precision": round(pl, 4), "recall": round(rl, 4), "f1": f1(pl, rl)},
            "abstract_rationalized": {"precision": round(pr, 4), "recall": round(rr, 4), "f1": f1(pr, rr)},
            "claim_accuracy_with_kernel": round(claim_hits["kernel"] / with_evidence, 4) if with_evidence else None,
            "claim_accuracy_closed_book": round(claim_hits["closed"] / with_evidence, 4) if with_evidence else None,
            "quote_fidelity": {"quotes": quotes, "found_verbatim": found,
                               "rate": round(found / quotes, 4) if quotes else None},
            "unparsed": sum(1 for a in answers.values() if a.get("unparsed"))}


def load_answers(path: str) -> dict[str, dict]:
    raw = {json.loads(l)["key"]: json.loads(l) for l in open(path) if l.strip()}
    return {key: parse(r["answer"] if isinstance(r.get("answer"), str) else json.dumps(r.get("answer", r)))
            for key, r in raw.items()}


def first_verdict(answers: dict[str, dict], rank: dict[str, int]) -> dict[str, dict]:
    """One verdict per claim (D-64): from the highest-ranked recalled abstract that gives one; the
    others become NOT_ENOUGH_INFO. Chosen on SciFact's train split, not on dev."""
    out, taken = dict(answers), set()
    for key in sorted((k for k in answers if not k.endswith("|closed")), key=lambda k: (k.split("|")[0], rank.get(k, 99))):
        cid = key.split("|")[0]
        if answers[key]["label"] != "NOT_ENOUGH_INFO":
            if cid in taken:
                out[key] = {**answers[key], "label": "NOT_ENOUGH_INFO", "quotes": []}
            taken.add(cid)
    return out


def verify(n: int, k: int, answers_file: str | None, prompts_file: str | None = None, policy: str = "all") -> dict:
    models: dict[str, int] = {}
    if answers_file:
        answers = load_answers(answers_file)
        models[os.environ.get("KK_EVAL_MODEL", "answers file")] = len(answers)
        items = [{"key": key} for key in answers]
    else:
        items = [json.loads(l) for l in open(prompts_file)] if prompts_file else prompts(n, k)
        answers = {}
        for it in items:
            text, model = ask(it["prompt"])
            answers[it["key"]] = parse(text)
            models[model] = models.get(model, 0) + 1
    missing = [it["key"] for it in items if it["key"] not in answers]
    for key in missing:
        answers[key] = {"label": "NOT_ENOUGH_INFO", "quotes": [], "unparsed": True}
    if policy == "first":
        if not prompts_file:
            sys.exit("--policy first needs --prompts (the recall ranks)")
        rank, seen = {}, {}
        for it in map(json.loads, open(prompts_file)):   # recall order, also for prompt files without "rank"
            cid = it["key"].split("|")[0]
            seen[cid] = seen.get(cid, 0) + 1
            rank[it["key"]] = it.get("rank", seen[cid])
        answers = first_verdict(answers, rank)
    return {"stage": "verify", "k": k, "split": SPLIT, "prompt": os.environ.get("KK_EVAL_PROMPT", "default"),
            "policy": policy, "models": models, "missing_answers": len(missing), **score(sample(n), corpus(), answers),
            "baselines": "compare with the SciFact leaderboard's abstract-level metrics on the dev set; open retrieval"}


# ---------------------------------------------------------------- stage 3: belief
STANCE = {"SUPPORT": "http://purl.org/spar/cito/supports", "CONTRADICT": "http://purl.org/spar/cito/refutes"}
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def proposition(text: str) -> str:
    return "urn:kk:proposition:" + hashlib.sha256(text.encode()).hexdigest()[:32]


def belief(n: int, answers_file: str, wait: float = 600) -> dict:
    """Stage 3: the stage-2 verdicts recorded as claims (`<paper> cito:supports|refutes <proposition>`,
    extracted, with the model's quotes) by the agent; then the belief in each proposition, from the belief
    model, against SciFact's label. A verdict without a quote is not recorded (extracted claims carry one)."""
    import psycopg
    from psycopg.types.json import Jsonb
    answers = load_answers(answers_file)
    claims = [c for c in sample(n) if any(k.startswith(f"{c['id']}|") for k in answers)]
    ops, skipped = [], 0
    for c in claims:
        prop = proposition(c["claim"])
        ops.append({"op": "add_claim", "subject": prop, "predicate": "rdfs:label",
                    "object": {"value": {"@value": c["claim"], "@language": "en"}}, "evidence_type": "testimony",
                    "references": [{"source_uri": f"https://github.com/allenai/scifact#claim-{c['id']}"}]})
        for key, ans in answers.items():
            cid, doc = key.split("|")
            if int(cid) != c["id"] or doc == "closed" or ans["label"] not in STANCE:
                continue
            quotes = [q for q in ans["quotes"] if q.strip()]
            if not quotes:
                skipped += 1
                continue
            paper = f"urn:kk:paper:{SOURCE}:{doc}"
            ops.append({"op": "add_claim", "subject": paper, "predicate": STANCE[ans["label"]], "object": {"entity": prop},
                        "evidence_type": "extracted", "references": [{"source_uri": paper, "quote": q} for q in quotes]})
    tag = hashlib.sha256(Path(answers_file).read_bytes()).hexdigest()[:16]
    with psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True) as db:
        me = db.execute("select kernel.my_agent()").fetchone()[0]
        for i in range(0, len(ops), 200):
            run_id = db.execute("select api.start_run(null, null, '[]'::jsonb, %s)",
                                (Jsonb({"purpose": "scifact stage 3"}),)).fetchone()[0]
            db.execute("select api.submit_change_set(%s)", (Jsonb({
                "idempotency_key": f"scifact-belief:{tag}:{i // 200}", "attributed_to": me, "generated_by": str(run_id),
                "target_scope": "urn:kk:scope:default", "ops": ops[i:i + 200]}),))
        props = {proposition(c["claim"]): c for c in claims}
        deadline, last, stable = time.time() + wait, None, 0
        while time.time() < deadline:   # until every proposition has a belief that no longer changes
            rows = dict(db.execute("select subject, belief from api.claims where predicate = %s and subject = any(%s)",
                                   (LABEL, list(props))).fetchall())
            stable = stable + 1 if rows == last and None not in rows.values() and len(rows) == len(props) else 0
            if stable >= 3:
                break
            last = rows
            time.sleep(5)
    truth = {p: (next(iter(c["evidence"].values()))[0]["label"] if c["evidence"] else "NOT_ENOUGH_INFO")
             for p, c in props.items()}
    sup = [rows[p] for p in props if truth[p] == "SUPPORT" and rows.get(p) is not None]
    con = [rows[p] for p in props if truth[p] == "CONTRADICT" and rows.get(p) is not None]
    nei = [rows[p] for p in props if truth[p] == "NOT_ENOUGH_INFO" and rows.get(p) is not None]
    pairs = [(a > b) + 0.5 * (a == b) for a in sup for b in con]
    return {"stage": "belief", "models": {os.environ.get("KK_EVAL_MODEL", "answers file"): len(answers)},
            "propositions": len(props), "stance_claims": len(ops) - len(props), "verdicts_without_quote": skipped,
            "supported": len(sup), "contradicted": len(con), "no_evidence": len(nei),
            "auc_supported_vs_contradicted": round(sum(pairs) / len(pairs), 4) if pairs else None,
            "sign_accuracy": round((sum(b > 0.5 for b in sup) + sum(b < 0.5 for b in con)) / (len(sup) + len(con)), 4)
                             if sup or con else None,
            "undecided_share": {"supported": round(sum(b == 0.5 for b in sup) / len(sup), 4) if sup else None,
                                "contradicted": round(sum(b == 0.5 for b in con) / len(con), 4) if con else None,
                                "no_evidence": round(sum(b == 0.5 for b in nei) / len(nei), 4) if nei else None},
            "mean_belief": {k: round(statistics.mean(v), 4) if v else None
                            for k, v in (("supported", sup), ("contradicted", con), ("no_evidence", nei))},
            "model": "belief-corroboration, evidence rule (D-62): (works for + 1) / (works for + against + 2)"}


def save(result: dict) -> None:
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    model = "-" + re.sub(r"[^a-z0-9]+", "-", "-".join(result["models"]).lower()).strip("-") if result.get("models") else ""
    model += "".join(f"-{result[k]}" for k in ("split", "prompt", "policy") if result.get(k) not in (None, "dev", "default", "all"))
    recall = str(result.get("recall", ""))
    method = ("-hybrid" if "recall_hybrid" in recall else "-bm25" if "BM25" in recall
              else "-fts" if result["stage"] == "retrieval" else "") + ("-rerank" if "re-ranked" in recall else "")
    path = runs / f"{date.today().isoformat()}-{result['stage']}{method}{model[:60]}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "fetch":
        fetch()
    elif stage == "encode":
        encode()
    elif stage == "retrieval":
        save(retrieval())
    elif stage == "belief":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--n", type=int, default=100)
        ap.add_argument("--answers", required=True, help="stage-2 answers (JSON lines), as for verify --answers")
        a = ap.parse_args(sys.argv[2:])
        save(belief(a.n, a.answers))
    elif stage in ("prompts", "verify"):
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--n", type=int, default=100)
        ap.add_argument("--k", type=int, default=3)
        ap.add_argument("--answers")
        ap.add_argument("--prompts", help="the items from an earlier `prompts` run, so every model sees the same ones")
        ap.add_argument("--policy", choices=("all", "first"), default="all",
                        help="all: every recalled abstract's verdict counts; first: one per claim, the highest-ranked (D-64)")
        a = ap.parse_args(sys.argv[2:])
        if stage == "prompts":
            for it in prompts(a.n, a.k):
                print(json.dumps(it))
        else:
            save(verify(a.n, a.k, a.answers, a.prompts, a.policy))
    else:
        sys.exit(__doc__)
