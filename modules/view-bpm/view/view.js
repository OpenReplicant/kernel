// The BPM view (M2-T4, D-51): a process map, documented vs observed, with the conformance discrepancies,
// the revisions the process learner proposes (D-76) and the documented process as BPMN.
// Reads with the signed-in person's MCP tools; every edge and discrepancy explains its claims.
(() => {
  const BBO = 'https://www.irit.fr/recherches/MELODI/ontologies/BBO#';
  const SOSA = 'http://www.w3.org/ns/sosa/';
  const OA = 'http://www.w3.org/ns/oa#';
  const TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';
  const LABEL = 'http://www.w3.org/2000/01/rdf-schema#label';
  const DCT_TYPE = 'http://purl.org/dc/terms/type';
  const DOCUMENTED = new Set(['deterministic', 'extracted', 'inferred']);   // inferred: learned (D-76)
  let cyView = null;

  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (e.className = v) : k.includes('-') ? e.setAttribute(k, v) : (e[k] = v)));
    e.append(...kids);
    return e;
  };
  const val = c => c.object_value && c.object_value['@value'];
  const link = (kk, id, text) => {
    const a = el('a', { href: '#', textContent: text, title: `claim ${id}` });
    a.addEventListener('click', ev => { ev.preventDefault(); kk.explain(id); });
    return a;
  };

  // Everything about one process: nodes, documented and observed edges, discrepancies.
  async function load(kk, process) {
    const members = await kk.call('query_claims', { predicate: BBO + 'has_container', object_entity: process, limit: 1000 });
    const nodes = {};
    for (const iri of [...new Set(members.map(c => c.subject))]) {
      const card = await kk.call('get_entity', { iri });
      const claims = card.claims || [];
      const type = claims.find(c => c.predicate === TYPE && c.object_entity && c.object_entity.startsWith(BBO));
      const label = claims.find(c => c.predicate === LABEL);
      nodes[iri] = { iri, label: label ? String(val(label)) : kk.short(iri), kind: type ? type.object_entity.slice(BBO.length) : 'Task',
                     claims, targetedBy: (card.referenced_by || []).filter(c => c.predicate === OA + 'hasTarget') };
    }
    const edges = {};
    const edge = (a, b) => (edges[a + ' ' + b] ||= { a, b, documented: [], observed: [], count: 0, discrepancies: [] });
    for (const n of Object.values(nodes)) {
      for (const c of n.claims) {
        if (c.predicate === BBO + 'has_nextFlowNode' && nodes[c.object_entity]) {
          edge(n.iri, c.object_entity)[DOCUMENTED.has(c.evidence_type) ? 'documented' : 'observed'].push(c);
        }
        const q = c.qualifiers || {};
        if (c.predicate === SOSA + 'hasSimpleResult' && q[SOSA + 'observedProperty'] === 'urn:kk:property:directly-follows-count'
            && nodes[q[BBO + 'has_previousFlowNode']]) {
          const e = edge(q[BBO + 'has_previousFlowNode'], n.iri);
          e.count += Number(val(c)) || 0;
          e.observed.push(c);
        }
      }
    }
    // An observed flow conforms when the documented process reaches b from a through gateways and
    // events only (as the conformance processor reads it, D-50).
    const isTask = iri => nodes[iri] && nodes[iri].kind.endsWith('Task');
    const reaches = (a, b) => {
      const todo = [a], seen = new Set();
      while (todo.length) {
        const n = todo.pop();
        for (const e of Object.values(edges)) {
          if (e.a !== n || !e.documented.length) continue;
          if (e.b === b) return true;
          if (!isTask(e.b) && !seen.has(e.b)) { seen.add(e.b); todo.push(e.b); }
        }
      }
      return false;
    };
    for (const e of Object.values(edges)) e.conforms = !!(e.documented.length || (e.observed.length && reaches(e.a, e.b)));
    // Confirmed discrepancies are claims; pending ones are change sets in the review lane.
    // A `rare` discrepancy targets the process itself (D-75).
    const found = {}, whole = await kk.call('get_entity', { iri: process });
    const onProcess = (whole.referenced_by || []).filter(c => c.predicate === OA + 'hasTarget');
    for (const n of [...Object.values(nodes), { targetedBy: onProcess }]) {
      for (const t of n.targetedBy) {
        if (!found[t.subject]) {
          const card = await kk.call('get_entity', { iri: t.subject });
          const cs = card.claims || [];
          const body = cs.find(c => c.predicate === OA + 'bodyValue');
          found[t.subject] = { iri: t.subject, status: 'confirmed', claim: body, text: body ? String(val(body)) : t.subject,
                               kind: (cs.find(c => c.predicate === DCT_TYPE) || {}).object_entity,
                               targets: cs.filter(c => c.predicate === OA + 'hasTarget').map(c => c.object_entity) };
        }
      }
    }
    let queue = [];
    try { queue = await kk.call('review_queue', { limit: 200 }); } catch (e) { /* not a reviewer */ }
    for (const cs of queue) {
      const ops = (cs.ops || []).filter(o => o.op === 'add_claim');
      const targets = ops.filter(o => o.predicate === OA + 'hasTarget').map(o => o.object.entity);
      if (!targets.length || !targets.every(t => nodes[t] || t === process)) continue;
      const iri = ops[0].subject;
      if (found[iri]) continue;
      const body = ops.find(o => o.predicate === OA + 'bodyValue');
      const kind = ops.find(o => o.predicate === DCT_TYPE);
      found[iri] = { iri, status: 'awaiting review', changeSet: cs.id, text: body ? body.object.value['@value'] : iri,
                     kind: kind && kind.object.entity, targets, premises: (body || ops[0]).derived_from || [] };
    }
    // Revisions the process learner proposes (D-76) are annotations too; they are listed on their own.
    for (const d of Object.values(found)) d.revision = (d.kind || '').startsWith('urn:kk:revision-kind:');
    // A discrepancy marks the flow it is about: the documented flow for a time limit or a path never
    // taken, otherwise the observed flow the documented process does not have.
    for (const d of Object.values(found).filter(d => !d.revision)) {
      const t = new Set(d.targets), kind = (d.kind || '').split(':').pop();
      for (const e of Object.values(edges)) {
        if (!t.has(e.a) || !t.has(e.b) || (e.a === e.b) !== (kind === 'rework')) continue;
        const fits = kind === 'limit' ? e.documented.some(c => c.evidence_type !== 'inferred') && e.observed.length   // not a learned path
          : kind === 'missing' ? !e.observed.length : e.observed.length && !e.conforms;
        if (fits) e.discrepancies.push(d);
      }
    }
    const all = Object.values(found);
    return { nodes, edges, discrepancies: all.filter(d => !d.revision), revisions: all.filter(d => d.revision) };
  }

  // The documented process (BPMN, D-41 encoder; learned paths, D-76) as BPMN 2.0 XML with a simple layered
  // layout, so it opens in any BPMN editor. A task with more than one documented successor gets an
  // exclusive gateway in front of them (BPMN would otherwise take every outgoing flow).
  function toBpmn(data, process) {
    const TAGS = new Set(['Task', 'UserTask', 'ServiceTask', 'ManualTask', 'ScriptTask', 'SendTask', 'ReceiveTask',
      'BusinessRuleTask', 'StartEvent', 'EndEvent', 'IntermediateCatchEvent', 'IntermediateThrowEvent',
      'ExclusiveGateway', 'ParallelGateway', 'InclusiveGateway', 'EventBasedGateway']);
    const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const flows = Object.values(data.edges).filter(e => e.documented.length).map(e => [e.a, e.b]);
    const ids = {}, nodes = [];
    const node = (iri, kind, label) => { if (!ids[iri]) { ids[iri] = 'n' + (nodes.length + 1); nodes.push({ iri, kind, label }); } return ids[iri]; };
    for (const [a, b] of flows) for (const x of [a, b]) {
      const n = data.nodes[x];
      node(x, TAGS.has(n.kind) ? n.kind : 'Task', n.label);
    }
    const out = {};
    for (const [a, b] of flows) (out[a] ||= []).push(b);
    const edges = [];
    for (const [a, targets] of Object.entries(out)) {
      if (targets.length > 1 && data.nodes[a].kind.endsWith('Task')) {
        const g = node(a + '#choice', 'ExclusiveGateway', '');
        edges.push([ids[a], g]);
        targets.forEach(b => edges.push([g, ids[b]]));
      } else targets.forEach(b => edges.push([ids[a], ids[b]]));
    }
    // Layers by breadth-first search from the nodes nothing flows into (or the first node).
    const into = new Set(edges.map(e => e[1])), next = {};
    edges.forEach(([a, b]) => (next[a] ||= []).push(b));
    const layer = {}, todo = nodes.map(n => ids[n.iri]).filter(id => !into.has(id));
    if (!todo.length && nodes.length) todo.push(ids[nodes[0].iri]);
    todo.forEach(id => (layer[id] = 0));
    for (let i = 0; i < todo.length; i++) for (const b of next[todo[i]] || []) if (!(b in layer)) { layer[b] = layer[todo[i]] + 1; todo.push(b); }
    const size = k => (k.endsWith('Event') ? [36, 36] : k.endsWith('Gateway') ? [50, 50] : [100, 80]);
    const box = {}, rows = {};
    for (const n of nodes) {
      const id = ids[n.iri], l = layer[id] ?? 0, r = (rows[l] = (rows[l] ?? -1) + 1), [w, h] = size(n.kind);
      box[id] = { x: 60 + l * 170 + (100 - w) / 2, y: 60 + r * 120 + (80 - h) / 2, w, h };
    }
    const lc = k => k[0].toLowerCase() + k.slice(1);
    const pid = 'Process_' + process.replace(/[^A-Za-z0-9_]/g, '_');
    const flowId = i => 'f' + (i + 1);
    const xml = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
      '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
      `  id="Definitions_1" targetNamespace="${esc(process)}" exporter="Knowledge Kernel" exporterVersion="view-bpm 0.2.0">`,
      `  <bpmn:process id="${pid}" name="${esc(process)}" isExecutable="false">`,
      ...nodes.map(n => `    <bpmn:${lc(n.kind)} id="${ids[n.iri]}" name="${esc(n.label)}"/>`),
      ...edges.map(([a, b], i) => `    <bpmn:sequenceFlow id="${flowId(i)}" sourceRef="${a}" targetRef="${b}"/>`),
      '  </bpmn:process>',
      `  <bpmndi:BPMNDiagram id="Diagram_1"><bpmndi:BPMNPlane id="Plane_1" bpmnElement="${pid}">`,
      ...nodes.map(n => { const b = box[ids[n.iri]]; return `    <bpmndi:BPMNShape id="${ids[n.iri]}_di" bpmnElement="${ids[n.iri]}"><dc:Bounds x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}"/></bpmndi:BPMNShape>`; }),
      ...edges.map(([a, b], i) => { const s = box[a], t = box[b]; return `    <bpmndi:BPMNEdge id="${flowId(i)}_di" bpmnElement="${flowId(i)}"><di:waypoint x="${s.x + s.w}" y="${s.y + s.h / 2}"/><di:waypoint x="${t.x}" y="${t.y + t.h / 2}"/></bpmndi:BPMNEdge>`; }),
      '  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>',
      '</bpmn:definitions>', ''];
    return xml.join('\n');
  }

  function draw(container, kk, data, onEdge) {
    const css = getComputedStyle(document.documentElement), v = n => css.getPropertyValue(n).trim();
    const shape = k => (k.endsWith('Gateway') ? 'diamond' : k.endsWith('Event') ? 'ellipse' : 'round-rectangle');
    const elements = [
      ...Object.values(data.nodes).map(n => ({ data: { id: n.iri, label: n.label, shape: shape(n.kind),
        task: n.kind.endsWith('Task') ? 1 : 0 } })),
      ...Object.values(data.edges).map(e => ({ data: {
        id: e.a + ' ' + e.b, source: e.a, target: e.b,
        label: e.count ? String(e.count) : '',
        kind: e.observed.length ? (e.conforms ? 'both' : 'observed') : 'documented',
        width: e.count ? 1 + Math.min(6, Math.log2(1 + e.count)) : 1.5,
        flag: e.discrepancies.some(d => d.status === 'confirmed') ? 'confirmed' : e.discrepancies.length ? 'pending' : '' } })),
    ];
    if (cyView) cyView.destroy();
    cyView = cytoscape({
      container, elements, wheelSensitivity: 0.3,
      layout: { name: 'breadthfirst', directed: true, padding: 24, spacingFactor: 1.1, animate: false },
      style: [
        { selector: 'node', style: { shape: 'data(shape)', label: 'data(label)', 'font-size': 10, color: v('--fg'),
            'text-valign': 'center', 'text-wrap': 'wrap', 'text-max-width': 90, width: 100, height: 38,
            'background-color': v('--card'), 'border-width': 1.5, 'border-color': v('--edge') } },
        { selector: 'node[task = 0]', style: { width: 30, height: 30, 'font-size': 8, 'text-valign': 'bottom' } },
        { selector: 'edge', style: { 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', width: 'data(width)',
            label: 'data(label)', 'font-size': 9, color: v('--muted'), 'text-background-color': v('--bg'),
            'text-background-opacity': 1, 'line-color': v('--k-node'), 'target-arrow-color': v('--k-node') } },
        { selector: 'edge[kind = "documented"]', style: { 'line-style': 'dashed', 'line-color': v('--edge'), 'target-arrow-color': v('--edge') } },
        { selector: 'edge[kind = "both"]', style: { 'line-color': v('--accent'), 'target-arrow-color': v('--accent') } },
        { selector: 'edge[flag = "pending"]', style: { 'line-color': v('--k-slot'), 'target-arrow-color': v('--k-slot') } },
        { selector: 'edge[flag = "confirmed"]', style: { 'line-color': v('--bad'), 'target-arrow-color': v('--bad') } },
      ],
    });
    cyView.on('tap', 'edge', ev => onEdge(data.edges[ev.target.id()]));
    container.dataset.nodes = cyView.nodes().length;
    container.dataset.edges = cyView.edges().length;
  }

  KK.register({
    id: 'view-bpm',
    title: 'Processes',
    async render(root, kk) {
      const processes = await kk.call('query_claims', { predicate: 'rdf:type', object_entity: BBO + 'Process', limit: 1000 });
      if (!processes.length) {
        root.append(el('p', { class: 'muted', textContent: 'No documented process yet: encode a BPMN model (encoder-bpmn).' }));
        return;
      }
      const pick = el('select', { 'aria-label': 'Process' });
      processes.forEach(c => pick.append(el('option', { value: c.subject, textContent: kk.short(c.subject) })));
      const legend = el('p', { class: 'muted', textContent:
        'Dashed: documented, not observed. Teal: observed as documented (directly or through a gateway). ' +
        'Blue: observed, not documented (width and number: cases). ' +
        'Red: confirmed discrepancy. Amber: discrepancy awaiting review — confirm or reject it below, after its evidence. ' +
        'Click a flow, a discrepancy or a piece of evidence for its claims.' });
      const map = el('div', { id: 'bpm-map', style: 'height:520px;border:1px solid var(--line);border-radius:8px' });
      const info = el('div', { id: 'bpm-edge' });
      const list = el('ul', { id: 'bpm-discrepancies' });
      const learned = el('ul', { id: 'bpm-revisions' });
      const download = el('button', { textContent: 'Download BPMN', title: 'The documented process, learned paths included, as BPMN 2.0' });
      root.append(el('div', { class: 'row' }, el('strong', { textContent: 'Process ' }), pick, download), legend, map, info,
                  el('h3', { textContent: 'Discrepancies' }), list,
                  el('h3', { textContent: 'Learned from practice' }),
                  el('p', { class: 'muted', textContent: 'Paths people confirmed as deviations that practice takes often: ' +
                    'approve one to make it part of the documented process; the next conformance pass then stops flagging it.' }),
                  learned);
      let current = null;
      download.addEventListener('click', () => {
        if (!current) return;
        const a = el('a', { href: URL.createObjectURL(new Blob([toBpmn(current, pick.value)], { type: 'application/xml' })),
                            download: kk.short(pick.value) + '.bpmn' });
        document.body.append(a); a.click(); a.remove();
      });

      const showEdge = e => {
        info.replaceChildren(el('strong', { textContent: `${kk.short(e.a)} → ${kk.short(e.b)}: ` }));
        const part = (title, claims) => {
          if (!claims.length) return;
          info.append(el('span', { textContent: ` ${title} ` }));
          claims.slice(0, 6).forEach((c, i) => { if (i) info.append(', '); info.append(link(kk, c.id, `${c.evidence_type} (belief ${c.belief ?? '—'})`)); });
        };
        part('documented:', e.documented);
        part(`observed (${e.count} cases):`, e.observed);
        e.discrepancies.forEach(d => info.append(el('div', { class: 'bad', textContent: `${d.status}: ${d.text}` })));
      };
      const show = async () => {
        const data = await load(kk, pick.value);
        current = data;
        draw(map, kk, data, showEdge);
        list.dataset.ready = '0';
        list.replaceChildren();
        learned.replaceChildren();
        for (const d of [...data.discrepancies, ...data.revisions].sort((x, y) => (x.status > y.status ? 1 : -1))) {
          const li = el('li', { className: d.revision ? 'revision' : 'discrepancy' });
          const status = d.revision ? (d.claim ? 'learned' : 'proposed') : d.status;
          li.dataset.status = status;
          li.append(el('strong', { textContent: `${(d.kind || '').split(':').pop()} ` }));
          if (d.claim) li.append(link(kk, d.claim.id, d.text), el('span', { class: 'muted', textContent: ` (${d.revision ? 'learned' : 'confirmed'}, belief ${d.claim.belief ?? '—'})` }));
          else li.append(d.text, el('span', { class: 'muted', textContent: d.revision ? ' (proposed)' : ' (awaiting review)' }));
          // The evidence: the claims it was derived from, each with its evidence type and belief.
          const premises = d.claim ? (await kk.call('explain', { claim_id: d.claim.id })).derived_from || [] : d.premises;
          const ev = el('div', { class: 'evidence' }, el('span', { class: 'muted', textContent: 'evidence: ' }));
          for (const [i, id] of premises.entries()) {
            const c = (await kk.call('explain', { claim_id: id })).claim;
            if (i) ev.append(', ');
            ev.append(c ? link(kk, id, `${c.evidence_type} ${kk.short(c.subject)} (belief ${c.belief ?? '—'})`) : id);
          }
          li.append(ev);
          if (!d.claim) {
            const decide = approve => async () => {
              await kk.call('review', { change_set_id: d.changeSet, approve,
                                        note: `${approve ? (d.revision ? 'approved' : 'confirmed') : 'rejected'} in the process view` });
              await show();
            };
            const yes = el('button', { className: 'approve', textContent: d.revision ? 'Approve' : 'Confirm' });
            const no = el('button', { textContent: 'Reject' });
            yes.addEventListener('click', () => decide(true)().catch(e => info.replaceChildren(el('span', { class: 'bad', textContent: e.message }))));
            no.addEventListener('click', () => decide(false)().catch(e => info.replaceChildren(el('span', { class: 'bad', textContent: e.message }))));
            li.append(el('div', { class: 'row' }, yes, no));
          }
          (d.revision ? learned : list).append(li);
        }
        if (!data.discrepancies.length) list.append(el('li', { class: 'muted', textContent: 'None found.' }));
        if (!data.revisions.length) learned.append(el('li', { class: 'muted', textContent: 'Nothing proposed yet.' }));
        list.dataset.ready = '1';
      };
      pick.addEventListener('change', () => show().catch(e => info.replaceChildren(el('span', { class: 'bad', textContent: e.message }))));
      await show();
    },
  });
})();
