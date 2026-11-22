"""One turn of the interview. Pure helpers plus `say`, which talks to the kernel as the module roles.

  say(conversation, author, text)
    1. the person's message is appended to the conversation's chat file and the interaction encoder records it
       (as:Note, quoted by every claim below; the quote verifier checks it, not this module)
    2. what the message says becomes claims quoting it: from the answers file when the text is one of the
       scripted answers (no model; the demo and the tests), else from a model (KK_EXTRACT_URL, KK_EXTRACT_MODEL,
       KK_EXTRACT_KEY: any OpenAI-compatible endpoint, a `:free` small model or a local one), else nothing
    3. the claims are submitted as the interviewer (`kkm_interviewer`), by change set, as extracted claims
    4. the map gap processor runs once; the first gap it lists is the next question, recorded as the agent's message

Env: DATABASE_URL (a login that may SET ROLE to the module roles), KK_ARTIFACTS, KK_CHATS (chat files; default
.chats in the artifact store), KK_INTERVIEW_SOURCE (default the script's: acme).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from kk.lib import connect, run, submit

from . import script as S

AGENT = "urn:kk:agent:interviewer"
ROLE = "kkm_interviewer"
OA = "http://www.w3.org/ns/oa#"
ORG = "http://www.w3.org/ns/org#"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
PREDICATES = ("a", "label", "ORG:holds", "ORG:reportsTo", "ORG:postIn", "ORG:unitOf", "BBO:has_nextFlowNode", "BBO:has_resource",
              "BBO:has_container")
DONE = "Nothing on the maps is missing now. Tell me anything else about how the work runs."


def chats() -> Path:
    return Path(os.environ.get("KK_CHATS") or Path(os.environ.get("KK_ARTIFACTS") or Path.home() / ".local/share/knowledge-kernel/artifacts") / ".chats")


def config(source: str | None = None) -> dict:
    messages, sc, answers = S.load(source or os.environ.get("KK_INTERVIEW_SOURCE"))
    return {"messages": messages, "script": sc, "answers": answers, "source": source or os.environ.get("KK_INTERVIEW_SOURCE") or sc["source"],
            "conversation": messages[0]["conversation"]}


def chat_file(cfg: dict) -> Path:
    return chats() / f"{cfg['conversation']}.jsonl"


def read_chat(cfg: dict) -> list[dict]:
    f = chat_file(cfg)
    if not f.exists():   # a new conversation opens with the interviewer's first question
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cfg["messages"][0]) + "\n")
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()]


def message_iri(cfg: dict, m: dict) -> str:
    return f"urn:kk:message:{cfg['source']}:{m['conversation']}:{m['id']}"


def suggestion(cfg: dict, chat: list[dict]) -> str | None:
    """The next scripted answer not said yet (the demo prefills the box with it)."""
    said = {m["text"] for m in chat}
    return next((a["text"] for a in cfg["answers"] if a["text"] not in said), None)


def state(cfg: dict) -> dict:
    chat = read_chat(cfg)
    return {"conversation": cfg["conversation"], "messages": [{k: m[k] for k in ("id", "author", "kind", "text", "time")} for m in chat],
            "suggestion": suggestion(cfg, chat)}


# ---------------------------------------------------------------- what a message says
def scripted_ops(cfg: dict, text: str, iri: str) -> list[dict]:
    for a in cfg["answers"]:
        if a["text"] == text:
            return [{**op, "references": [{"source_uri": iri, "quote": op["references"][0]["quote"]}]} for op in a["ops"]]
    return []


PROMPT = """You map an organisation and its processes from what a person says. Read the MESSAGE and list what it states \
as a JSON array of [subject, predicate, object, quote] items.
Subjects and objects: org:unit:<slug> (a team), org:post:<slug> (a post such as a buyer), person:<slug> (a named person), \
proc:step:<slug> (a step of the work), proc:node:start, proc:node:end, {org} (the organisation), {proc} (the process). \
Slugs are lower case words joined by -. An object may also be a text in double quotes.
Predicates: a (with ORG:Post, ORG:OrganizationalUnit, prov:Person, BBO:StartEvent, BBO:EndEvent), label (a text), ORG:holds \
(person, post), ORG:reportsTo (post, post), ORG:postIn (post, unit), ORG:unitOf (unit, organisation), BBO:has_nextFlowNode \
(step, next step), BBO:has_resource (step, post), BBO:has_container (step, process).
"quote" is the exact words of the MESSAGE the item rests on, copied character for character. Return only the array; [] if \
nothing is stated.

MESSAGE:
{text}

JSON array:"""


def ask_model(prompt: str) -> str:
    url, key = os.environ["KK_EXTRACT_URL"].rstrip("/"), os.environ.get("KK_EXTRACT_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    body = json.dumps({"model": os.environ.get("KK_EXTRACT_MODEL", "liquid/lfm-2.5-2.6b:free"), "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url + "/chat/completions", body, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def parse_model(answer: str, cfg: dict, text: str, iri: str) -> list[dict]:
    """The items of the model's array that name known terms and quote the message; the rest are dropped."""
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    m = re.search(r"\[.*\]", answer, flags=re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    sc, ops = cfg["script"], []
    for it in items:
        if not (isinstance(it, list) and len(it) == 4 and all(isinstance(x, str) for x in it)):
            continue
        s, p, o, quote = it
        if p not in PREDICATES or quote not in text or not re.match(r"^(org:|person:|proc:|urn:kk:)", s):
            continue
        obj = {"value": {"@value": o[1:-1]}} if o.startswith('"') else {"entity": S.iri(o, sc)}
        ops.append({"op": "add_claim", "subject": S.iri(s, sc), "predicate": S.iri(p, sc), "object": obj,
                    "evidence_type": "extracted", "references": [{"source_uri": iri, "quote": quote}]})
    return ops


def extract(cfg: dict, text: str, iri: str) -> tuple[list[dict], str]:
    ops = scripted_ops(cfg, text, iri)
    if ops:
        return ops, "scripted"
    if os.environ.get("KK_EXTRACT_URL"):
        return parse_model(ask_model(PROMPT.format(text=text, org=cfg["script"]["organisation"], proc=cfg["script"]["process"])), cfg, text, iri), "model"
    return [], "none"


# ---------------------------------------------------------------- the kernel
def module(args: list[str], role: str) -> dict:
    out = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, env={**os.environ, "KK_ROLE": role})
    if out.returncode:
        raise RuntimeError(f"{args[0]} failed: {(out.stderr or out.stdout)[-600:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def settled(role: str, seconds: float = 30) -> None:
    """Wait until the validator has decided every change set `role` submitted."""
    with connect(role) as db:
        end = time.time() + seconds
        while time.time() < end and db.execute("select count(*) from api.change_sets where status in ('submitted', 'valid')").fetchone()[0]:
            time.sleep(0.2)


def record(cfg: dict) -> None:
    module(["interaction_encoder", str(chat_file(cfg)), "--source", cfg["source"]], "kkm_encoder_interaction")
    settled("kkm_encoder_interaction")


def gaps(cfg: dict) -> list[str]:
    """The questions the maps still raise about this organisation and process, in a steady order."""
    sc = cfg["script"]
    with connect(ROLE) as db:
        rows = db.execute("""select b.object_value->>'@value' from api.claims g
                join api.claims t on t.subject = g.subject and t.predicate = %s and t.status <> 'deprecated'
                join api.claims b on b.subject = g.subject and b.predicate = %s and b.status <> 'deprecated'
               where g.predicate = 'http://purl.org/dc/terms/type' and g.subject like 'urn:kk:gap:%%' and g.status <> 'deprecated'
                 and (t.object_entity like %s or t.object_entity like %s) order by 1""",
                          (OA + "hasTarget", OA + "bodyValue", sc["organisation"] + ":%", sc["process"] + ":%")).fetchall()
    return [r[0] for r in rows]


def say(cfg: dict, author: str, text: str, kind: str = "person") -> dict:
    """One turn. Returns the state of the conversation plus what was recorded."""
    chat = read_chat(cfg)
    if any(m["kind"] == "person" and m["text"] == text for m in chat) and kind == "person":
        return {**state(cfg), "extracted": "already said", "claims": 0}
    nxt = str(max(int(m["id"]) for m in chat) + 1)
    msg = {"id": nxt, "conversation": cfg["conversation"], "author": author, "kind": kind,
           "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "text": text}
    with open(chat_file(cfg), "a") as f:
        f.write(json.dumps(msg) + "\n")
    record(cfg)
    ops, how = extract(cfg, text, message_iri(cfg, msg))
    claims, status = 0, None
    if ops:
        with connect(ROLE) as conn, conn.cursor() as cur:
            with run(cur, None, {"skill": "interview", "message": message_iri(cfg, msg)}) as (run_id, _):
                cs = submit(cur, {"idempotency_key": f"interview:{cfg['script']['organisation']}|{message_iri(cfg, msg)}",
                                  "attributed_to": AGENT, "target_scope": "urn:kk:scope:default", "ops": ops}, run_id)
            for _ in range(240):
                status = conn.execute("select status from api.change_sets where id = %s", (cs["id"],)).fetchone()[0]
                if status not in ("submitted", "valid"):
                    break
                time.sleep(0.25)
        claims = len(ops) if status == "applied" else 0
    module(["map_gaps", "--once"], "kkm_map_gaps")
    settled("kkm_map_gaps")
    asked = {m["text"] for m in read_chat(cfg)}
    question = next((q for q in gaps(cfg) if q not in asked), DONE)
    if question != DONE or not any(m["text"] == DONE for m in read_chat(cfg)[-1:]):
        with open(chat_file(cfg), "a") as f:
            f.write(json.dumps({"id": str(int(nxt) + 1), "conversation": cfg["conversation"], "author": "interviewer", "kind": "agent",
                                "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "text": question}) + "\n")
        record(cfg)
    return {**state(cfg), "extracted": how, "claims": claims, "status": status}
