"""Chain claims -> RuleGo DSL (RuleGo-Server v0.38.0 components). Pure functions.

Compile rule (spec §7): a chain is runnable when every step maps to a RuleGo component, every
subchain it calls is runnable, and its trigger has a RuleGo endpoint. Otherwise it stays described,
not runnable, and the reasons are returned (they become kk:notRunnableBecause claims).

  http       restApiCall; calls to the kernel API carry ${global.KK_TOKEN_<AGENT>} for runs_as
  subchain   flow (targetId), or for (range, do: chain:<id>, mode 1) when for_each is set
  log        log
  filter / switch / transform   jsFilter / jsSwitch / jsTransform, JavaScript only
  schedule "every Ns" trigger   embedded schedule endpoint (6-field cron with seconds)

A step that writes to the kernel (non-GET to ${KK_API}) gets the same run semantics as the Python
runner (D-25): start_run with plan_step = the step IRI, the call with ${run.id}, then finish_run.
"""
from __future__ import annotations

import json
import re

KK = "https://w3id.org/knowledge-kernel/ns#"
PPLAN_STEP_OF = "http://purl.org/net/p-plan#isStepOfPlan"
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
LABELS = {"success": "Success", "failure": "Failure", "true": "True", "false": "False"}


class Unmappable(Exception):
    pass


def slug(chain_iri: str) -> str:
    return chain_iri.rsplit(":", 1)[-1]


def token_var(agent_iri: str) -> str:
    return "KK_TOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", agent_iri.rsplit(":", 1)[-1]).upper()


# ------------------------------------------------------------------ claims -> chain model
def chains_from_claims(rows: list[dict]) -> dict:
    """rows: api.claims rows (id, subject, predicate, object_entity, object_value, qualifiers, status)."""
    rows = [r for r in rows if r["status"] != "deprecated"]
    val = lambda r: (r.get("object_value") or {}).get("@value")  # noqa: E731
    chains = {r["subject"]: {"iri": r["subject"], "steps": {}, "claims": [r["id"]]} for r in rows
              if r["predicate"].endswith("#type") and r["object_entity"] == KK + "Chain"}
    step_of = {r["subject"]: r["object_entity"] for r in rows
               if r["predicate"] == PPLAN_STEP_OF and r["object_entity"] in chains}
    for r in rows:
        s, p = r["subject"], r["predicate"]
        if s in chains:
            c = chains[s]
            c["claims"].append(r["id"])
            if p == KK + "runsAs":
                c["runs_as"] = r["object_entity"]
            elif p == KK + "trigger":
                c["trigger"] = val(r)
        elif s in step_of:
            c = chains[step_of[s]]
            c["claims"].append(r["id"])
            st = c["steps"].setdefault(s.rsplit("/", 1)[1], {"next": {}})
            if p == KK + "stepType":
                st["do"] = val(r)
            elif p == KK + "stepConfig":
                st["with"] = json.loads(val(r))
            elif p == KK + "next":
                label = (r.get("qualifiers") or {}).get("kk:onLabel") or (r.get("qualifiers") or {}).get(KK + "onLabel")
                st["next"].setdefault(label, []).append(r["object_entity"].rsplit("/", 1)[1])
    for c in chains.values():
        c["claims"] = sorted(set(c["claims"]))
    return chains


def entry_step(chain: dict) -> str:
    targets = {t for st in chain["steps"].values() for ts in st["next"].values() for t in ts}
    entries = sorted(set(chain["steps"]) - targets)
    if len(entries) != 1:
        raise Unmappable(f"cannot tell the first step (candidates: {entries or 'none'})")
    return entries[0]


# ------------------------------------------------------------------ templates
def url_template(text: str) -> str:
    def sub(m):
        path = m.group(1)
        if path == "KK_API":
            return "${global.KK_API}"
        if path == "run.id":
            return "${metadata.run_id}"
        if path == "msg" or path.startswith("msg."):
            return "${" + path + "}"
        raise Unmappable(f"unknown placeholder ${{{path}}}")
    return PLACEHOLDER.sub(sub, text)


def js_expr(value) -> str:
    """A step's body template as a JavaScript expression over msg and metadata."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {js_expr(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(js_expr(v) for v in value) + "]"
    if not isinstance(value, str):
        return json.dumps(value)

    def path_expr(path):
        if path == "run.id":
            return "metadata.run_id"
        if path == "msg" or path.startswith("msg."):
            return path
        raise Unmappable(f"placeholder ${{{path}}} has no JavaScript equivalent")

    whole = PLACEHOLDER.fullmatch(value)
    if whole:
        return path_expr(whole.group(1))
    parts, last = [], 0
    for m in PLACEHOLDER.finditer(value):
        parts += [json.dumps(value[last:m.start()]), f"String({path_expr(m.group(1))})"]
        last = m.end()
    parts.append(json.dumps(value[last:]))
    return " + ".join(parts)


def transform(script: str) -> dict:
    return {"type": "jsTransform", "configuration": {"jsScript": script}}


def ret(msg_expr: str) -> str:
    return f"return {{'msg': {msg_expr}, 'metadata': metadata, 'msgType': msgType}};"


# ------------------------------------------------------------------ steps
def compile_step(chain: dict, sid: str, step: dict, module: str) -> tuple[list, list, dict]:
    """-> (nodes, internal connections, {'in': node id, 'out': {label: node id}})."""
    kind, cfg = step.get("do"), step.get("with", {})
    if kind == "http":
        method = cfg.get("method", "GET").upper()
        url = url_template(cfg["url"])
        kernel = cfg["url"].startswith("${KK_API}")
        headers = {"Content-Type": "application/json"}
        if kernel:
            headers["Authorization"] = "Bearer ${global." + token_var(chain["runs_as"]) + "}"
        call = {"id": sid, "type": "restApiCall", "configuration": {
            "restEndpointUrlPattern": url, "requestMethod": method, "headers": headers,
            "withoutRequestBody": method == "GET", "readTimeoutMs": 30000}}
        if cfg.get("body") == "${msg}":
            cfg = {k: v for k, v in cfg.items() if k != "body"}       # the message is the default body
        body = js_expr(cfg["body"]) if "body" in cfg else "msg"
        if not (kernel and method != "GET"):
            if "body" not in cfg:
                return [call], [], {"in": sid, "out": {"success": sid, "failure": sid}}
            prep = {"id": f"{sid}__body", **transform(ret(body))}
            return [prep, call], [(prep["id"], sid, "Success")], {"in": prep["id"], "out": {"success": sid, "failure": sid}}
        api, auth = "${global.KK_API}", dict(headers)
        rest = lambda nid, path: {"id": nid, "type": "restApiCall", "configuration": {  # noqa: E731
            "restEndpointUrlPattern": api + path, "requestMethod": "POST", "headers": auth,
            "withoutRequestBody": False, "readTimeoutMs": 30000}}
        step_iri = f"{chain['iri']}/{sid}"
        nodes = [
            {"id": f"{sid}__run", **transform("metadata.kk_msg = JSON.stringify(msg); "
                                               + ret(json.dumps({"module": module, "plan_step": step_iri})))},
            rest(f"{sid}__start", "/rpc/start_run"),
            {"id": f"{sid}__body", **transform("metadata.run_id = String(msg); var msg = JSON.parse(metadata.kk_msg); "
                                                + ret(body))},
            call,
            {"id": f"{sid}__ok", **transform("metadata.kk_out = JSON.stringify(msg); "
                                              + ret("{'run_id': metadata.run_id, 'status': 'succeeded', 'cost': {}}"))},
            rest(f"{sid}__finish", "/rpc/finish_run"),
            {"id": f"{sid}__done", **transform(ret("JSON.parse(metadata.kk_out)"))},
            {"id": f"{sid}__failed", **transform(ret("{'run_id': metadata.run_id, 'status': 'failed', 'cost': {}}"))},
            rest(f"{sid}__finish_failed", "/rpc/finish_run"),
        ]
        links = [(f"{sid}__run", f"{sid}__start", "Success"), (f"{sid}__start", f"{sid}__body", "Success"),
                 (f"{sid}__body", sid, "Success"), (sid, f"{sid}__ok", "Success"),
                 (f"{sid}__ok", f"{sid}__finish", "Success"), (f"{sid}__finish", f"{sid}__done", "Success"),
                 (sid, f"{sid}__failed", "Failure"), (f"{sid}__failed", f"{sid}__finish_failed", "Success")]
        return nodes, links, {"in": f"{sid}__run", "out": {"success": f"{sid}__done", "failure": f"{sid}__finish_failed"}}
    if kind == "subchain":
        target = slug(cfg["chain"])
        if "for_each" in cfg:
            fe = cfg["for_each"]
            m = PLACEHOLDER.fullmatch(fe)
            if not m or not (m.group(1) == "msg" or m.group(1).startswith("msg.")):
                raise Unmappable(f"for_each {fe!r} is not a msg path")
            rng = "" if m.group(1) == "msg" else m.group(1)
            node = {"id": sid, "type": "for", "configuration": {"range": rng, "do": f"chain:{target}", "mode": 1}}
        else:
            node = {"id": sid, "type": "flow", "configuration": {"targetId": target}}
        return [node], [], {"in": sid, "out": {"success": sid, "failure": sid}}
    if kind == "log":
        node = {"id": sid, "type": "log", "configuration": {
            "jsScript": f"return {json.dumps(chain['iri'] + '/' + sid + ': ')} + JSON.stringify(msg);"}}
        return [node], [], {"in": sid, "out": {"success": sid, "failure": sid}}
    if kind in ("filter", "switch", "transform"):
        lang = cfg.get("lang", "js")
        if lang != "js" or "source" not in cfg:
            raise Unmappable(f"{kind} step in {lang}" + ("" if "source" in cfg else " from a file")
                             + ": RuleGo runs inline JavaScript only")
        src = cfg["source"]
        if kind == "filter":
            node = {"id": sid, "type": "jsFilter", "configuration": {"jsScript": src}}
        elif kind == "switch":
            node = {"id": sid, "type": "jsSwitch", "configuration": {
                "jsScript": f"var __r = (function(){{ {src} }})(); return [String(__r)];"}}
        else:
            node = {"id": sid, **transform(f"var __r = (function(){{ {src} }})(); " + ret("__r"))}
        return [node], [], {"in": sid, "out": {lab: sid for lab in ["success", "failure", "true", "false"]}}
    raise Unmappable(f"step type {kind} has no RuleGo component")


def compile_chain(chain: dict, module: str, runnable: dict[str, bool]) -> tuple[dict | None, list[str]]:
    """-> (RuleGo rule chain, []) or (None, reasons). runnable: verdicts for other chains so far."""
    reasons = []
    trig = (chain.get("trigger") or "").split()
    endpoints = []
    if trig[:1] == ["schedule"] and len(trig) == 3 and trig[1] == "every":
        m = re.fullmatch(r"(\d+)s", trig[2])
        if not m or not 0 < int(m.group(1)) < 60:
            reasons.append(f"trigger: schedule {trig[2]} is not a whole number of seconds under a minute")
        else:
            endpoints.append({"id": "schedule", "type": "schedule", "name": "schedule",
                              "routers": [{"from": {"path": f"*/{m.group(1)} * * * * *"}}]})
    elif trig[:1] != ["chain"]:
        reasons.append(f"trigger: {' '.join(trig) or 'none'} has no RuleGo endpoint mapping yet")
    if not chain.get("runs_as"):
        reasons.append("no runs_as agent")
    try:
        first = entry_step(chain)
    except Unmappable as e:
        reasons.append(str(e))
        first = None
    nodes, links, ports = [], [], {}
    for sid in sorted(chain["steps"]):
        st = chain["steps"][sid]
        if st.get("do") == "subchain":
            target = st.get("with", {}).get("chain")
            if not runnable.get(target, False):
                reasons.append(f"{sid}: calls {target}, which is not runnable")
        try:
            n, l, p = compile_step(chain, sid, st, module)
            nodes += n
            links += l
            ports[sid] = p
        except (Unmappable, KeyError, ValueError) as e:
            reasons.append(f"{sid}: {e}")
    if reasons:
        return None, reasons
    for sid, st in sorted(chain["steps"].items()):
        for label, targets in st["next"].items():
            out = ports[sid]["out"].get(label, ports[sid]["out"]["success"])
            for t in targets:
                links.append((out, ports[t]["in"], LABELS.get(label, label)))
    first_node = ports[first]["in"]
    nodes.sort(key=lambda n: (n["id"] != first_node, n["id"]))
    for n in nodes:
        n.setdefault("name", n["id"])
    return {"ruleChain": {"id": slug(chain["iri"]), "name": slug(chain["iri"]), "root": True,
                          "additionalInfo": {"kk_chain": chain["iri"], "kk_runs_as": chain["runs_as"]}},
            "metadata": {"firstNodeIndex": 0, "endpoints": endpoints, "nodes": nodes,
                         "connections": [{"fromId": a, "toId": b, "type": t} for a, b, t in sorted(set(links))]}}, []


def compile_all(chains: dict, module_of: dict[str, str]) -> dict[str, tuple[dict | None, list[str]]]:
    """Compile every chain; subchain dependencies are resolved by fixed point."""
    verdict = {iri: True for iri in chains}
    while True:
        results = {iri: compile_chain(c, module_of.get(iri, ""), verdict) for iri, c in sorted(chains.items())}
        new = {iri: r[0] is not None for iri, r in results.items()}
        if new == verdict:
            return results
        verdict = new


def render(rule_chain: dict) -> bytes:
    return (json.dumps(rule_chain, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
