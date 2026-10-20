"""A process on the map -> BPMN 2.0 XML, with a lane per post and a left-to-right diagram, for any BPMN tool.

Steps become tasks, `next` sequence flows, `starts_with` and `ends_after` a start and an end event. Each
task's documentation quotes what was said about it, so the model in a BPMN editor still cites the interview.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
      'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI"')


def ident(key: str) -> str:
    return "id_" + "".join(c if c.isalnum() else "_" for c in key)


def bpmn(process: str, entities: dict[str, dict], statements: list[dict]) -> str:
    rel = lambda r: [s for s in statements if s["relation"] == r]  # noqa: E731
    steps = sorted({s["s"] for s in rel("step_of") if s["o"] == process})
    flows = [(s["s"], s["o"]) for s in rel("next") if s["s"] in steps and s["o"] in steps]
    first = [s["o"] for s in rel("starts_with") if s["s"] == process and s["o"] in steps]
    last = [s["o"] for s in rel("ends_after") if s["s"] == process and s["o"] in steps]
    start, end = f"{process}:start", f"{process}:end"
    flows += [(start, s) for s in first] + [(s, end) for s in last]
    nodes = ([start] if first else []) + steps + ([end] if last else [])
    performer = {s["s"]: s["o"] for s in rel("performed_by") if s["s"] in steps}
    quotes = {}
    for s in statements:
        if s["s"] in steps or s["o"] in steps:
            quotes.setdefault(s["s"] if s["s"] in steps else s["o"], []).append(f"{s.get('said_by', '')}: “{s['quote']}”")

    # Columns by longest path from the start, so the diagram reads left to right.
    col = {n: 0 for n in nodes}
    for _ in nodes:
        for a, b in flows:
            col[b] = max(col[b], col[a] + 1)
    lanes = sorted({performer[s] for s in steps if s in performer}) + (["(nobody yet)"] if any(s not in performer for s in steps) else [])
    row = lambda n: lanes.index(performer.get(n, "(nobody yet)")) if n in steps else 0  # noqa: E731
    name = lambda k: entities.get(k, {}).get("name", k)  # noqa: E731

    pid, out, di = ident(process), [], []
    out.append(f'<?xml version="1.0" encoding="UTF-8"?>\n<bpmn:definitions {NS} id="defs" targetNamespace="urn:process-interview">')
    out.append(f'  <bpmn:process id={quoteattr(pid)} name={quoteattr(name(process))} isExecutable="false">')
    if lanes:
        out.append('    <bpmn:laneSet id="lanes">')
        for i, lane in enumerate(lanes):
            refs = "".join(f"<bpmn:flowNodeRef>{ident(n)}</bpmn:flowNodeRef>" for n in nodes if row(n) == i)
            out.append(f'      <bpmn:lane id="lane_{i}" name={quoteattr(name(lane))}>{refs}</bpmn:lane>')
        out.append('    </bpmn:laneSet>')
    for n in nodes:
        tag = "startEvent" if n == start else "endEvent" if n == end else "task"
        doc = "".join(f"<bpmn:documentation>{escape(q)}</bpmn:documentation>" for q in quotes.get(n, []))
        out.append(f'    <bpmn:{tag} id={quoteattr(ident(n))}{"" if tag != "task" else " name=" + quoteattr(name(n))}>{doc}</bpmn:{tag}>')
    for i, (a, b) in enumerate(flows):
        out.append(f'    <bpmn:sequenceFlow id="flow_{i}" sourceRef="{ident(a)}" targetRef="{ident(b)}"/>')
    out.append('  </bpmn:process>')

    W, H, LANE = 100, 80, 120
    x = lambda n: 160 + col[n] * 160  # noqa: E731
    y = lambda n: 20 + row(n) * LANE + (LANE - (36 if n in (start, end) else H)) // 2  # noqa: E731
    size = lambda n: (36, 36) if n in (start, end) else (W, H)  # noqa: E731
    di.append(f'  <bpmndi:BPMNDiagram id="diagram"><bpmndi:BPMNPlane id="plane" bpmnElement={quoteattr(pid)}>')
    width = 160 + (max(col.values(), default=0) + 1) * 160
    for i, _ in enumerate(lanes):
        di.append(f'    <bpmndi:BPMNShape id="lane_{i}_di" bpmnElement="lane_{i}" isHorizontal="true"><dc:Bounds x="100" y="{20 + i * LANE}" width="{width}" height="{LANE}"/></bpmndi:BPMNShape>')
    for n in nodes:
        w, h = size(n)
        di.append(f'    <bpmndi:BPMNShape id="{ident(n)}_di" bpmnElement="{ident(n)}"><dc:Bounds x="{x(n)}" y="{y(n)}" width="{w}" height="{h}"/></bpmndi:BPMNShape>')
    for i, (a, b) in enumerate(flows):
        (aw, ah), (_, bh) = size(a), size(b)
        di.append(f'    <bpmndi:BPMNEdge id="flow_{i}_di" bpmnElement="flow_{i}"><di:waypoint x="{x(a) + aw}" y="{y(a) + ah // 2}"/>'
                  f'<di:waypoint x="{x(b)}" y="{y(b) + bh // 2}"/></bpmndi:BPMNEdge>')
    di.append('  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>')
    return "\n".join(out + di + ["</bpmn:definitions>", ""])
