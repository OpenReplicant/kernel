// The BPM view (M2-T4, D-51): a process map, documented vs observed, with the conformance discrepancies.
// Reads with the signed-in person's MCP tools; every edge and discrepancy explains its claims.
(() => {
  const BBO = 'https://www.irit.fr/recherches/MELODI/ontologies/BBO#';
  const SOSA = 'http://www.w3.org/ns/sosa/';
  const OA = 'http://www.w3.org/ns/oa#';
  const TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';
  const LABEL = 'http://www.w3.org/2000/01/rdf-schema#label';
  const DCT_TYPE = 'http://purl.org/dc/terms/type';
  const DOCUMENTED = new Set(['deterministic', 'extracted']);
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
    const found = {};
    for (const n of Object.values(nodes)) {
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
      if (!targets.length || !targets.every(t => nodes[t])) continue;
      const iri = ops[0].subject;
      if (found[iri]) continue;
      const body = ops.find(o => o.predicate === OA + 'bodyValue');
      const kind = ops.find(o => o.predicate === DCT_TYPE);
      found[iri] = { iri, status: 'awaiting review', changeSet: cs.id, text: body ? body.object.value['@value'] : iri,
                     kind: kind && kind.object.entity, targets };
    }
    // A discrepancy marks the flow it is about: the documented flow for a time limit or a path never
    // taken, otherwise the observed flow the documented process does not have.
    for (const d of Object.values(found)) {
      const t = new Set(d.targets), kind = (d.kind || '').split(':').pop();
      for (const e of Object.values(edges)) {
        if (!t.has(e.a) || !t.has(e.b) || (e.a === e.b) !== (kind === 'rework')) continue;
        const fits = kind === 'limit' ? e.documented.length && e.observed.length
          : kind === 'missing' ? !e.observed.length : e.observed.length && !e.conforms;
        if (fits) e.discrepancies.push(d);
      }
    }
    return { nodes, edges, discrepancies: Object.values(found) };
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
        'Red: confirmed discrepancy. Amber: discrepancy awaiting review. Click a flow or a discrepancy for its claims.' });
      const map = el('div', { id: 'bpm-map', style: 'height:520px;border:1px solid var(--line);border-radius:8px' });
      const info = el('div', { id: 'bpm-edge' });
      const list = el('ul', { id: 'bpm-discrepancies' });
      root.append(el('div', { class: 'row' }, el('strong', { textContent: 'Process ' }), pick), legend, map, info,
                  el('h3', { textContent: 'Discrepancies' }), list);

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
        draw(map, kk, data, showEdge);
        list.replaceChildren();
        for (const d of data.discrepancies.sort((x, y) => (x.status > y.status ? 1 : -1))) {
          const li = el('li', { className: 'discrepancy' });
          li.dataset.status = d.status;
          li.append(el('strong', { textContent: `${(d.kind || '').split(':').pop()} ` }));
          if (d.claim) li.append(link(kk, d.claim.id, d.text), el('span', { class: 'muted', textContent: ` (confirmed, belief ${d.claim.belief ?? '—'})` }));
          else li.append(d.text, el('span', { class: 'muted', textContent: ` (awaiting review in change set ${d.changeSet}; see Review)` }));
          list.append(li);
        }
        if (!data.discrepancies.length) list.append(el('li', { class: 'muted', textContent: 'None found.' }));
      };
      pick.addEventListener('change', () => show().catch(e => info.replaceChildren(el('span', { class: 'bad', textContent: e.message }))));
      await show();
    },
  });
})();
