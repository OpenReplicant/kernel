"""BPMN 2.0 XML -> the documented process as claims (D-49). Pure functions; no database.

  <process>  a bbo:Process ; rdfs:label
  <node>     a bbo:<kind> ; rdfs:label ; bbo:has_container <process>
  <a>        bbo:has_nextFlowNode <b>     (a sequence flow; its name, e.g. a gateway branch, as the
                                           qualifier rdfs:label)
  <role>     a bbo:Role ; rdfs:label       (a lane)       <task> bbo:has_resource <role>

Tasks are `<process>:step:<slug of the name>` (the event-log encoder's step IRIs, D-48 b); other nodes
and unnamed tasks are `<process>:node:<BPMN id>`; lanes are `<process>:role:<slug>`. Every claim is
deterministic and references the model artifact with a FragmentSelector naming the BPMN element id.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
NEXT, CONTAINER, RESOURCE = BBO + "has_nextFlowNode", BBO + "has_container", BBO + "has_resource"
TASKS = {"task": "Task", "userTask": "UserTask", "serviceTask": "ServiceTask", "manualTask": "ManualTask",
         "sendTask": "Task", "receiveTask": "Task", "scriptTask": "Task", "businessRuleTask": "Task"}
OTHER = {"exclusiveGateway": "ExclusiveGateway", "parallelGateway": "ParallelGateway",
         "inclusiveGateway": "InclusiveGateway", "eventBasedGateway": "Gateway", "complexGateway": "Gateway",
         "startEvent": "StartEvent", "endEvent": "EndEvent", "intermediateCatchEvent": "Event",
         "intermediateThrowEvent": "Event", "boundaryEvent": "Event", "subProcess": "Activity",
         "callActivity": "Activity"}

def slug(text: str) -> str:
    """Same as the event-log encoder's (D-48 b): documented and observed steps meet on one IRI."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed"


def pick_process(root: ET.Element, process_id: str | None) -> ET.Element:
    procs = root.findall(f"{BPMN}process")
    if process_id:
        procs = [p for p in procs if p.get("id") == process_id]
    procs = [p for p in procs if any(c.tag[len(BPMN):] in TASKS for c in p)]
    if len(procs) != 1:
        raise ValueError(f"found {len(procs)} BPMN processes with tasks; name one with --bpmn-process ID")
    return procs[0]


def encode(data: bytes, process: str, sha: str, process_id: str | None = None) -> list[dict]:
    """The add_claim ops for one model (the caller adds the artifact op)."""
    root = ET.fromstring(data)
    proc = pick_process(root, process_id)
    ops: list[dict] = []

    def claim(s, p, o, element_id, qualifiers=None):
        ops.append({"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": "deterministic",
                    **({"qualifiers": qualifiers} if qualifiers else {}),
                    "references": [{"artifact": sha, "span": {"type": "FragmentSelector", "value": element_id}}]})

    pid = proc.get("id")
    claim(process, RDF_TYPE, {"entity": BBO + "Process"}, pid)
    if proc.get("name"):
        claim(process, LABEL, {"value": {"@value": proc.get("name")}}, pid)

    iri: dict[str, str] = {}
    for el in proc:
        kind = el.tag[len(BPMN):] if el.tag.startswith(BPMN) else None
        if kind not in TASKS and kind not in OTHER:
            continue
        name, eid = (el.get("name") or "").strip(), el.get("id")
        node = f"{process}:step:{slug(name)}" if kind in TASKS and name else f"{process}:node:{eid}"
        if node in iri.values():
            raise ValueError(f"two tasks named {name!r}: name them apart or the steps would merge")
        iri[eid] = node
        claim(node, RDF_TYPE, {"entity": BBO + (TASKS.get(kind) or OTHER[kind])}, eid)
        if name:
            claim(node, LABEL, {"value": {"@value": name}}, eid)
        claim(node, CONTAINER, {"entity": process}, eid)

    for flow in proc.iter(f"{BPMN}sequenceFlow"):
        src, tgt = iri.get(flow.get("sourceRef")), iri.get(flow.get("targetRef"))
        if not src or not tgt:
            raise ValueError(f"sequence flow {flow.get('id')} connects an element this encoder does not know")
        name = (flow.get("name") or "").strip()
        claim(src, NEXT, {"entity": tgt}, flow.get("id"), {LABEL: name} if name else None)

    for lane in proc.iter(f"{BPMN}lane"):
        name = (lane.get("name") or lane.get("id")).strip()
        role = f"{process}:role:{slug(name)}"
        claim(role, RDF_TYPE, {"entity": BBO + "Role"}, lane.get("id"))
        claim(role, LABEL, {"value": {"@value": name}}, lane.get("id"))
        for ref in lane.iter(f"{BPMN}flowNodeRef"):
            node = iri.get((ref.text or "").strip())
            if node and ":step:" in node:
                claim(node, RESOURCE, {"entity": role}, lane.get("id"))
    return ops
