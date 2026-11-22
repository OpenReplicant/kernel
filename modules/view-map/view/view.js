// The Map view (D-116): the organisation and process maps as one live graph, edited in place.
//   solid edges   stated claims            dotted edges  observed in event logs (beside the stated ones)
//   dashed        proposed, in the review lane, until a person confirms
// Live: `applied_since` (the applied order, D-109/D-112) is read once a second with a cursor; only the entities
// a change set touched are read again and only the elements that changed are redrawn.
// Edits: each one is a change set attributed to the signed-in person, written with submit_change_set.
// A new type or property is looked up in the loaded packs first; a new one goes to the review lane (D-40).
// Every node and edge shows its claim id, belief and what it cites (invariant 6).
(() => {
  const ORG = 'http://www.w3.org/ns/org#', BBO = 'https://www.irit.fr/recherches/MELODI/ontologies/BBO#';
  const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', RDFS = 'http://www.w3.org/2000/01/rdf-schema#';
  const OWL = 'http://www.w3.org/2002/07/owl#', PROV = 'http://www.w3.org/ns/prov#';
  const TYPE = RDF + 'type', LABEL = RDFS + 'label', VOCAB = 'urn:kk:scope:vocab', DEFAULT = 'urn:kk:scope:default';
  const MAP_PREDICATES = [ORG + 'unitOf', ORG + 'subOrganizationOf', ORG + 'postIn', ORG + 'holds', ORG + 'reportsTo',
                          BBO + 'has_nextFlowNode', BBO + 'has_resource'];
  const MAP_TYPES = [ORG + 'FormalOrganization', ORG + 'OrganizationalUnit', ORG + 'Organization', ORG + 'Post',
                     PROV + 'Person', BBO + 'Task'];
  const PROPERTY_TYPES = [OWL + 'ObjectProperty', RDF + 'Property'], CLASS_TYPES = [OWL + 'Class', RDFS + 'Class'];
  const NOT_EDGES = new Set([TYPE, LABEL, BBO + 'has_container']);   // a step's process is not a hand-off
  const POLL_MS = 1000, FRESH_MS = 2500, UNKNOWN_PER_TICK = 40, KNOWN_PER_TICK = 80;
  let running = 0;   // the generation of the view that is polling; a newer render stops the older one

  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (e.className = v) : k === 'data' ? Object.assign(e.dataset, v)
      : k.includes('-') ? e.setAttribute(k, v) : (e[k] = v)));
    e.append(...kids);
    return e;
  };
  const val = c => c && c.object_value && c.object_value['@value'];
  const norm = t => String(t).toLowerCase().replace(/[^a-z0-9]/g, '');
  const slug = t => String(t).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function group(types) {
    const has = (...x) => x.some(t => types.includes(t));
    if (has(ORG + 'FormalOrganization', ORG + 'Organization', ORG + 'OrganizationalUnit')) return 'org';
    if (has(ORG + 'Post')) return 'post';
    if (has(PROV + 'Person')) return 'person';
    if (types.some(t => t.startsWith(BBO) && /Event$/.test(t))) return 'event';
    if (types.some(t => t.startsWith(BBO) && /Gateway$/.test(t))) return 'gateway';
    return types.some(t => t.startsWith(BBO)) ? 'step' : 'other';
  }

  async function render(root, kk) {
    const mine = ++running;
    const style = el('style', {}, `
      .map-bar { display:flex; gap:16px; flex-wrap:wrap; align-items:center; margin:8px 0; font-size:12px; }
      .map-bar svg { vertical-align:middle; margin-right:4px; }
      .map-grid { display:grid; grid-template-columns:minmax(0,1fr) 340px; gap:16px; }
      #map-cy { height:640px; border:1px solid var(--line); border-radius:8px; }
      .map-side { display:flex; flex-direction:column; gap:12px; min-width:0; }
      .map-side section { border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--card); }
      .map-side h3 { margin:0 0 6px; font-size:13px; }
      .map-side .row { margin:4px 0; } .map-side input, .map-side select { min-width:0; }
      #map-pending li { margin-bottom:8px; list-style:none; } #map-pending { padding:0; margin:0; }
      #map-pending li { border-left:3px dashed var(--accent); padding-left:8px; font-size:13px; }
      .map-cites { font-size:12px; margin:4px 0; padding-left:16px; } code { font-size:12px; word-break:break-all; }
      @media (max-width: 900px) { .map-grid { grid-template-columns:1fr; } #map-cy { height:420px; } }`);
    const status = el('div', { id: 'map-status', class: 'muted', role: 'status' }, 'loading…');
    const cyBox = el('div', { id: 'map-cy', data: { ready: '0' } });
    const detail = el('section', { id: 'map-detail' }, el('h3', {}, 'Selected'), el('p', { class: 'muted' }, 'Click a node or an edge.'));
    const forms = el('section', { id: 'map-forms' });
    const pendingBox = el('section', {}, el('h3', {}, 'Awaiting review'), el('ul', { id: 'map-pending' }));
    const legend = el('div', { class: 'map-bar' },
      ...[['stated', ''], ['observed in a log', '2 3'], ['proposed, pending review', '6 4']].map(([t, d]) => el('span', {},
        (() => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('width', 30); s.setAttribute('height', 8);
          const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
          ['x1:0', 'y1:4', 'x2:30', 'y2:4', 'stroke:currentColor', 'stroke-width:2', ...(d ? ['stroke-dasharray:' + d] : [])]
            .forEach(a => l.setAttribute(a.split(':')[0], a.split(':')[1])); s.append(l); return s; })(), t)),
      status);
    root.replaceChildren(style, legend, el('div', { class: 'map-grid' }, cyBox, el('div', { class: 'map-side' }, detail, forms, pendingBox)));

    // ---- state
    const me = await kk.call('whoami');
    const cards = {};              // iri -> entity card (claims about it, claims pointing at it)
    let terms = { property: {}, class: {} };   // iri -> true, the typed terms of the loaded packs
    let queue = [];                // review_queue rows
    let cursor = null, awaitingKey = '', wantEdge = null;
    const fresh = new Set();
    const cy = cytoscape({ container: cyBox, wheelSensitivity: 0.3, elements: [], style: [] });
    cyBox._cy = cy;
    const css = getComputedStyle(document.documentElement), v = n => css.getPropertyValue(n).trim() || '#888';
    const colors = { org: '#7c3aed', post: '#2563eb', person: '#0f766e', step: '#b45309', event: '#57534e', gateway: '#be185d', other: '#a8a29e' };
    cy.style([
      { selector: 'node', style: { 'background-color': n => colors[n.data('group')] || colors.other, label: 'data(label)', 'font-size': 10,
          color: v('--fg'), 'text-valign': 'bottom', 'text-margin-y': 3, 'text-wrap': 'wrap', 'text-max-width': 110, width: 18, height: 18,
          'border-width': 0 } },
      { selector: 'node[group = "org"]', style: { shape: 'round-rectangle', width: 26, height: 20 } },
      { selector: 'node[group = "post"]', style: { shape: 'round-rectangle' } },
      { selector: 'node[group = "person"]', style: { shape: 'ellipse' } },
      { selector: 'node[group = "step"]', style: { shape: 'rectangle' } },
      { selector: 'node[group = "gateway"]', style: { shape: 'diamond' } },
      { selector: 'node.pending', style: { 'border-width': 2, 'border-style': 'dashed', 'border-color': v('--accent'), opacity: 0.7 } },
      { selector: 'edge', style: { 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'line-color': v('--edge'),
          'target-arrow-color': v('--edge'), width: e => 1 + 2 * (e.data('belief') ?? 0.5), label: 'data(label)', 'font-size': 8,
          color: v('--muted'), 'text-rotation': 'autorotate', 'text-background-color': v('--bg'), 'text-background-opacity': 0.8 } },
      { selector: 'edge.observed', style: { 'line-style': 'dotted', 'line-color': '#d97706', 'target-arrow-color': '#d97706', 'line-dash-pattern': [2, 3] } },
      { selector: 'edge.pending', style: { 'line-style': 'dashed', 'line-color': v('--accent'), 'target-arrow-color': v('--accent'), opacity: 0.8 } },
      { selector: '.fresh', style: { 'underlay-color': '#facc15', 'underlay-opacity': 0.55, 'underlay-padding': 8 } },
      { selector: ':selected', style: { 'underlay-color': v('--accent'), 'underlay-opacity': 0.35, 'underlay-padding': 8 } },
    ]);

    // ---- reading
    const typesOf = card => (card.claims || []).filter(c => c.predicate === TYPE && c.object_entity).map(c => c.object_entity);
    const labelOf = (card, iri) => {
      const ls = (card.claims || []).filter(c => c.predicate === LABEL);
      const best = ls.sort((a, b) => (a.belief ?? 0) - (b.belief ?? 0)).pop();
      return best ? String(val(best)) : kk.short(iri);
    };
    const isMapEntity = card => {
      const t = typesOf(card);
      return t.some(x => MAP_TYPES.includes(x) || x.startsWith(BBO)) ||
        (card.claims || []).some(c => MAP_PREDICATES.includes(c.predicate)) || (card.referenced_by || []).some(c => MAP_PREDICATES.includes(c.predicate));
    };
    async function load(iris) {
      const todo = [...iris];
      for (let i = 0; i < todo.length; i += 8) {
        await Promise.all(todo.slice(i, i + 8).map(async iri => { cards[iri] = await kk.call('get_entity', { iri }); }));
      }
    }
    async function loadTerms() {
      const get = async types => (await Promise.all(types.map(t => kk.call('query_claims', { predicate: TYPE, object_entity: t, limit: 1000 }))))
        .flat().filter(c => c.status !== 'deprecated').map(c => c.subject);
      const [p, c] = await Promise.all([get(PROPERTY_TYPES), get(CLASS_TYPES)]);
      terms = { property: Object.fromEntries(p.map(i => [i, true])), class: Object.fromEntries(c.map(i => [i, true])) };
      MAP_PREDICATES.forEach(i => { terms.property[i] = true; });
    }
    async function discover() {
      const found = new Set();
      for (const p of MAP_PREDICATES) {
        for (const c of await kk.call('query_claims', { predicate: p, limit: 1000 })) { found.add(c.subject); if (c.object_entity) found.add(c.object_entity); }
      }
      for (const t of MAP_TYPES) for (const c of await kk.call('query_claims', { predicate: TYPE, object_entity: t, limit: 1000 })) found.add(c.subject);
      await load([...found].slice(0, 400));
    }
    async function loadQueue() {
      try { queue = await kk.call('review_queue', { limit: 200 }); } catch (e) { queue = []; }
    }

    // ---- what the graph should be, from the cards and the review queue
    function desired() {
      const nodes = {}, edges = {};
      for (const [iri, card] of Object.entries(cards)) {
        if (!isMapEntity(card)) continue;
        const t = typesOf(card);
        nodes[iri] = { id: iri, label: labelOf(card, iri), group: group(t), pending: false };
      }
      const observedCount = {};
      for (const card of Object.values(cards)) for (const c of card.claims || []) {
        const q = c.qualifiers || {};
        if (c.predicate === 'http://www.w3.org/ns/sosa/hasSimpleResult' && q['http://www.w3.org/ns/sosa/observedProperty'] === 'urn:kk:property:directly-follows-count') {
          const k = q[BBO + 'has_previousFlowNode'] + ' ' + c.subject;
          observedCount[k] = (observedCount[k] || 0) + (Number(val(c)) || 0);
        }
      }
      for (const [iri, card] of Object.entries(cards)) {
        if (!nodes[iri]) continue;
        for (const c of card.claims || []) {
          if (!c.object_entity || NOT_EDGES.has(c.predicate) || !nodes[c.object_entity]) continue;
          if (c.evidence_type === 'observed') {
            const id = 'obs:' + c.subject + ' ' + c.object_entity + ' ' + c.predicate;
            const e = edges[id] ||= { id, source: c.subject, target: c.object_entity, predicate: c.predicate, kind: 'observed', claims: [], belief: 0,
              count: observedCount[c.subject + ' ' + c.object_entity] };
            e.claims.push(c); e.belief = Math.max(e.belief, c.belief ?? 0);
            e.label = kk.short(c.predicate) + (e.count ? ' ×' + e.count : '');
          } else {
            edges[c.id] = { id: c.id, source: c.subject, target: c.object_entity, predicate: c.predicate, kind: 'stated', claims: [c],
                            belief: c.belief ?? 0, label: kk.short(c.predicate) };
          }
        }
      }
      // proposals in the review lane: dashed nodes and edges
      for (const cs of queue) {
        const ops = (cs.ops || []).filter(o => o.op === 'add_claim');
        const label = {}, typed = {};
        ops.forEach(o => { if (o.predicate === LABEL || o.predicate === RDFS + 'label') label[o.subject] = String(o.object.value && o.object.value['@value']);
                           if (o.predicate === TYPE || o.predicate === 'rdf:type') typed[o.subject] = o.object.entity; });
        ops.forEach((o, i) => {
          if (!o.object || !o.object.entity || [TYPE, LABEL, 'rdf:type', 'rdfs:label'].includes(o.predicate)) return;
          // only what belongs on these maps: a map relation, or both ends already map entities (not an annotation)
          const onMap = x => nodes[x] || (typed[x] && (MAP_TYPES.includes(typed[x]) || typed[x].startsWith(BBO)));
          if (!MAP_PREDICATES.includes(o.predicate) && !(onMap(o.subject) && onMap(o.object.entity))) return;
          const ends = [o.subject, o.object.entity];
          for (const x of ends) if (!nodes[x]) nodes[x] = { id: x, label: label[x] || kk.short(x), group: group(typed[x] ? [typed[x]] : []), pending: true };
          const id = `pending:${cs.id}:${i}`;
          edges[id] = { id, source: o.subject, target: o.object.entity, predicate: o.predicate, kind: 'pending', claims: [], changeSet: cs.id,
                        belief: 0.3, label: kk.short(o.predicate) };
        });
      }
      return { nodes, edges };
    }

    // ---- redraw only what changed
    function paint() {
      const want = desired();
      let added = 0;
      cy.batch(() => {
        cy.edges().forEach(e => { const w = want.edges[e.id()]; if (!w || w.source !== e.data('source') || w.target !== e.data('target')) cy.remove(e); });
        cy.nodes().forEach(n => { if (!want.nodes[n.id()]) cy.remove(n); });
        for (const n of Object.values(want.nodes)) {
          const ex = cy.getElementById(n.id);
          if (ex.length) {
            if (ex.data('label') !== n.label || ex.data('group') !== n.group) { ex.data({ label: n.label, group: n.group }); fresh.add(n.id); }
            ex.toggleClass('pending', n.pending);
          } else {
            cy.add({ group: 'nodes', data: { id: n.id, label: n.label, group: n.group }, classes: n.pending ? 'pending' : '' });
            fresh.add(n.id); added++;
          }
        }
        for (const e of Object.values(want.edges)) {
          const ex = cy.getElementById(e.id);
          if (ex.length) { ex.data({ label: e.label, belief: e.belief }); ex.classes(e.kind === 'stated' ? '' : e.kind); }
          else { cy.add({ group: 'edges', data: { id: e.id, source: e.source, target: e.target, label: e.label, belief: e.belief, kind: e.kind }, classes: e.kind === 'stated' ? '' : e.kind }); fresh.add(e.id); added++; }
        }
      });
      cy.edges().forEach(e => { e.scratch('want', want.edges[e.id()]); });
      if (added) cy.layout({ name: 'cose', animate: false, randomize: false, fit: added > 12 || cy.nodes().length < 14, padding: 30,
                             nodeDimensionsIncludeLabels: true, numIter: 400 }).run();
      fresh.forEach(id => { cy.getElementById(id).addClass('fresh'); });
      const ids = [...fresh]; fresh.clear();
      setTimeout(() => ids.forEach(id => cy.getElementById(id).removeClass('fresh')), FRESH_MS);
      const d = cyBox.dataset;
      d.nodes = cy.nodes().length; d.edges = cy.edges().length; d.stated = cy.edges('[kind = "stated"]').length;
      d.observed = cy.edges('.observed').length; d.pending = cy.edges('.pending').length + cy.nodes('.pending').length; d.cursor = cursor;
      status.textContent = `${d.nodes} nodes, ${d.stated} stated, ${d.observed} observed, ${queue.length} awaiting review`;
      fillSelects(want);
    }

    // ---- the side: what is selected, with its claim, belief and citations (invariant 6)
    const row = (k, ...v) => [el('dt', {}, k), el('dd', {}, ...v)];
    async function claimBlock(id) {
      const ex = await kk.call('explain', { claim_id: id });
      const c = ex.claim || {};
      const cites = (ex.references || []).map(r => el('li', {}, el('code', {}, r.source_uri || (r.artifact ? 'artifact ' + String(r.artifact).slice(0, 12) : '')),
                                                    r.quote ? ` “${r.quote}”` : ''));
      const a = el('a', { href: '#', textContent: 'full explanation' });
      a.addEventListener('click', ev => { ev.preventDefault(); kk.explain(id); });
      return el('dl', { class: 'map-claim', data: { claim: id } },
        ...row('claim', el('code', {}, id)), ...row('belief', c.belief == null ? '—' : Number(c.belief).toFixed(2)),
        ...row('status', `${c.status || ''} · ${c.evidence_type || ''}`), ...row('by', kk.short(c.attributed_to || '')),
        ...row('cites', cites.length ? el('ul', { class: 'map-cites' }, ...cites) : (ex.derived_from || []).length
          ? `${ex.derived_from.length} claim(s): ` + ex.derived_from.map(x => String(x).slice(0, 8)).join(', ') : 'nothing'),
        ...row('', a));
    }
    async function select(ele) {
      detail.replaceChildren(el('h3', {}, 'Selected'));
      if (ele.isNode()) {
        const iri = ele.id(), card = cards[iri];
        detail.append(el('p', {}, el('strong', {}, ele.data('label')), ' ', el('code', {}, iri)));
        if (!card) { detail.append(el('p', { class: 'muted' }, 'Proposed, waiting for review.')); return; }
        const labels = (card.claims || []).filter(c => c.predicate === LABEL);
        if (labels.length) detail.append(await claimBlock(labels.sort((a, b) => (a.belief ?? 0) - (b.belief ?? 0)).pop().id));
        const rename = el('input', { id: 'map-rename-to', type: 'text', value: ele.data('label'), 'aria-label': 'New name' });
        const go = el('button', { id: 'map-rename', textContent: 'Rename' });
        go.addEventListener('click', () => rename.value.trim() && rename.value.trim() !== ele.data('label') && save(`Renamed to ${rename.value.trim()}`, renameOps(iri, rename.value.trim())));
        detail.append(el('div', { class: 'row' }, rename, go));
      } else {
        const w = ele.scratch('want') || {};
        detail.append(el('p', { id: 'map-edge-title' }, `${ele.source().data('label')} → ${ele.data('label')} → ${ele.target().data('label')}`));
        if (w.kind === 'pending') {
          detail.append(el('p', { class: 'muted' }, 'Proposed by an agent; confirm or reject it under Awaiting review.'));
        } else {
          detail.append(await claimBlock(w.claims[0].id));
          if (w.claims.length > 1) detail.append(el('p', { class: 'muted' }, `${w.claims.length} observations, ${w.count || '?'} cases`));
          if (w.kind === 'stated') {
            const rm = el('button', { id: 'map-remove', textContent: 'Remove this edge' });
            rm.addEventListener('click', () => save('Removed an edge', [retract(w.claims[0])], w.claims[0].scope));
            detail.append(rm);
          } else detail.append(el('p', { class: 'muted' }, 'Observed in a log: not edited here.'));
        }
      }
    }
    cy.on('tap', 'node, edge', ev => { ev.target.select(); select(ev.target).catch(e => { status.textContent = e.message; }); });

    // ---- writing: every edit is a change set attributed to the person
    const ref = [{ source_uri: me.agent }];
    const add = (subject, predicate, object, extra = {}) => ({ op: 'add_claim', subject, predicate, object, evidence_type: 'testimony', references: ref, ...extra });
    const retract = (c, reason = 'retracted', by) => ({ op: 'set_status', claim: c.id, status: 'deprecated', reason, expect_status: c.status, ...(by ? { replaced_by: by } : {}) });
    function renameOps(iri, text) {
      const old = (cards[iri].claims || []).filter(c => c.predicate === LABEL);
      return [add(iri, LABEL, { value: { '@value': text } }, { ref: 'new' }), ...old.map(c => retract(c, 'superseded', 'new'))];
    }
    async function save(what, ops, scope = DEFAULT, then) {
      status.textContent = what + '…';
      try {
        const run = await kk.call('start_run', { info: { purpose: 'view-map: ' + what } });
        const cs = await kk.call('submit_change_set', { change_set: { idempotency_key: `view-map:${me.agent}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`,
          attributed_to: me.agent, generated_by: run, target_scope: scope, ops } });
        await kk.call('finish_run', { run_id: run });
        status.textContent = `${what}: ${cs.lane === 'review' ? 'waiting for review' : 'submitted'}`;
        cyBox.dataset.lastChangeSet = cs.id;
        if (then) then(cs);
      } catch (e) { status.textContent = what + ': ' + e.message; }
    }

    // ---- terms: look up in the packs first; a new one is proposed into the review lane (D-40)
    const known = kind => terms[kind];
    const pendingTerms = () => {
      const out = {};
      for (const cs of queue) for (const o of cs.ops || []) {
        if (o.op === 'add_claim' && (o.predicate === TYPE || o.predicate === 'rdf:type') && o.object && o.object.entity)
          out[o.subject] = { cs: cs.id, kind: o.object.entity };
      }
      return out;
    };
    async function lookup(kind, text) {
      const want = norm(kk.short(text));
      const iris = Object.keys(known(kind));
      const exact = iris.find(i => i === text) || iris.find(i => norm(kk.short(i)) === want);
      const near = iris.filter(i => i !== exact && want.length > 2 && (norm(kk.short(i)).includes(want) || (norm(kk.short(i)).length > 3 && want.includes(norm(kk.short(i)))))).slice(0, 6);
      if (!exact) {   // the labels and definitions are searched too
        try {
          for (const h of await kk.call('search', { text, limit: 10 })) if (known(kind)[h.subject] && !near.includes(h.subject)) near.push(h.subject);
        } catch (e) { /* search is not required */ }
      }
      return { exact, near: near.slice(0, 6) };
    }
    const from = el('select', { id: 'map-from', 'aria-label': 'From' }), to = el('select', { id: 'map-to', 'aria-label': 'To' });
    const pred = el('input', { id: 'map-pred', type: 'text', placeholder: 'reports to, hands off to…', 'aria-label': 'Relation' });
    pred.setAttribute('list', 'map-preds');
    const preds = el('datalist', { id: 'map-preds' });
    const termMsg = el('div', { id: 'map-term-msg', class: 'muted', role: 'status' });
    const addEdge = el('button', { id: 'map-add-edge', textContent: 'Add edge' });
    const nlabel = el('input', { id: 'map-new-label', type: 'text', placeholder: 'name', 'aria-label': 'Name' });
    const ntype = el('select', { id: 'map-new-type', 'aria-label': 'Type' },
      ...[['Post', ORG + 'Post'], ['Team', ORG + 'OrganizationalUnit'], ['Organisation', ORG + 'FormalOrganization'], ['Person', PROV + 'Person'], ['Step', BBO + 'Task']]
        .map(([t, i]) => el('option', { value: i, textContent: t })));
    const addNode = el('button', { id: 'map-add-node', textContent: 'Add node' });
    forms.append(el('h3', {}, 'Edit'),
      el('div', { class: 'row' }, from, pred, to, addEdge), preds, termMsg,
      el('div', { class: 'row' }, nlabel, ntype, addNode));

    function fillSelects(want) {
      const keep = [from.value, to.value];
      const opts = Object.values(want.nodes).filter(n => !n.pending).sort((a, b) => a.label.localeCompare(b.label));
      for (const [s, k] of [[from, keep[0]], [to, keep[1]]]) {
        const sig = opts.map(n => n.id + n.label).join('|');
        if (s.dataset.sig === sig) continue;
        s.dataset.sig = sig; s.replaceChildren(...opts.map(n => el('option', { value: n.id, textContent: n.label })));
        if (opts.some(n => n.id === k)) s.value = k;
      }
      const sig = Object.keys(known('property')).length + ':' + (preds.dataset.n || '');
      if (preds.dataset.sig !== sig) {
        preds.dataset.sig = sig;
        const first = MAP_PREDICATES.concat(Object.keys(known('property')).filter(i => !MAP_PREDICATES.includes(i)));
        preds.replaceChildren(...[...new Set(first.map(i => kk.short(i)))].map(s => el('option', { value: s })));
      }
    }
    async function submitEdge(a, p, b) {
      await save(`Added ${kk.short(p)}`, [add(a, p, { entity: b })]);
    }
    addEdge.addEventListener('click', async () => {
      const text = pred.value.trim();
      if (!text || !from.value || !to.value) { termMsg.textContent = 'Pick both ends and a relation.'; return; }
      const found = await lookup('property', text);
      termMsg.replaceChildren();
      if (found.exact) { await submitEdge(from.value, found.exact, to.value); pred.value = ''; return; }
      const proposed = pendingTerms()['urn:kk:property:' + slug(text)];
      if (proposed) { termMsg.textContent = `“${text}” is proposed and waiting for review; the edge is added once it is confirmed.`; wantEdge = { a: from.value, iri: 'urn:kk:property:' + slug(text), b: to.value }; return; }
      termMsg.append(`“${text}” is not in the loaded packs.` + (found.near.length ? ' Similar: ' : ' '));
      found.near.forEach(i => { const b = el('button', { textContent: kk.short(i), type: 'button' }); b.addEventListener('click', () => { pred.value = kk.short(i); termMsg.textContent = ''; }); termMsg.append(b, ' '); });
      const prop = el('button', { id: 'map-propose', textContent: 'Propose it as a new relation', type: 'button' });
      prop.addEventListener('click', () => proposeTerm('property', text, { a: from.value, b: to.value }));
      termMsg.append(prop);
    });
    async function proposeTerm(kind, text, edge) {
      const iri = `urn:kk:${kind === 'property' ? 'property' : 'class'}:${slug(text)}`;
      const type = kind === 'property' ? OWL + 'ObjectProperty' : OWL + 'Class';
      await save(`Proposed the ${kind} “${text}”`, [add(iri, TYPE, { entity: type }), add(iri, LABEL, { value: { '@value': text } })], VOCAB);
      termMsg.textContent = `“${text}” is proposed as a new ${kind}; it is pending until a person confirms it` + (edge ? ', then the edge is added.' : '.');
      if (edge) wantEdge = { a: edge.a, iri, b: edge.b };
    }
    addNode.addEventListener('click', async () => {
      const text = nlabel.value.trim();
      if (!text) return;
      if (Object.values(desired().nodes).some(n => n.label.toLowerCase() === text.toLowerCase())) { termMsg.textContent = `“${text}” is already on the map.`; return; }
      const iri = `urn:kk:map:${slug(text)}`;
      await save(`Added ${text}`, [add(iri, TYPE, { entity: ntype.value }), add(iri, LABEL, { value: { '@value': text } })]);
      nlabel.value = '';
    });

    // ---- the review lane: proposals wait here as dashed elements and as a list
    function drawQueue() {
      const ul = pendingBox.querySelector('ul');
      ul.replaceChildren(...queue.map(cs => {
        const own = cs.attributed_to === me.agent;
        const ok = el('button', { class: 'approve', textContent: 'Confirm', disabled: own, title: own ? 'a person other than the author confirms' : '' });
        const no = el('button', { class: 'reject', textContent: 'Reject', disabled: own });
        const decide = approve => async () => {
          try { await kk.call('review', { change_set_id: cs.id, approve }); status.textContent = approve ? 'Confirmed' : 'Rejected'; }
          catch (e) { status.textContent = e.message; }
        };
        ok.addEventListener('click', decide(true)); no.addEventListener('click', decide(false));
        const lines = (cs.ops || []).filter(o => o.op === 'add_claim').map(o => `${kk.short(o.subject)} ${kk.short(o.predicate)} ${o.object.entity ? kk.short(o.object.entity) : '“' + (o.object.value || {})['@value'] + '”'}`);
        return el('li', { data: { cs: cs.id } }, el('div', {}, lines.join('; ') || 'a change'), el('div', { class: 'muted' }, 'by ' + kk.short(cs.attributed_to)), el('div', { class: 'row' }, ok, no));
      }));
      if (!queue.length) ul.append(el('li', { class: 'muted' }, 'Nothing is waiting.'));
    }

    // ---- start, then follow the applied order
    status.textContent = 'reading the maps…';
    const start = (await kk.call('applied_since', {})).cursor;   // the present; what is applied later is read by cursor
    await Promise.all([loadTerms(), discover(), loadQueue()]);
    cursor = start;
    paint(); drawQueue();
    cyBox.dataset.ready = '1';

    async function tick() {
      const r = await kk.call('applied_since', { cursor });
      const key = (r.awaiting || []).join(',');
      let dirty = false;
      if (r.applied.length) {
        const subjects = new Set(), unknown = new Set();
        let vocab = false;
        for (const cs of r.applied) for (const t of cs.touched) {
          if (t.scope === VOCAB) { vocab = true; continue; }
          // a new entity is looked at as a subject only: an object is often a class or a term, not a map node
          if (t.subject) (cards[t.subject] ? subjects : unknown).add(t.subject);
          if (t.object_entity && cards[t.object_entity]) subjects.add(t.object_entity);
        }
        const bring = [...subjects].slice(0, KNOWN_PER_TICK).concat([...unknown].slice(0, UNKNOWN_PER_TICK));
        await load(bring);
        for (const iri of unknown) if (cards[iri] && !isMapEntity(cards[iri])) delete cards[iri];   // not a map entity: forget it
        if (vocab) await loadTerms();
        dirty = true;
      }
      if (key !== awaitingKey || r.applied.length) { awaitingKey = key; await loadQueue(); dirty = true; }
      cursor = r.cursor;
      if (dirty) { paint(); drawQueue(); }
      if (wantEdge && known('property')[wantEdge.iri]) { const w = wantEdge; wantEdge = null; await submitEdge(w.a, w.iri, w.b); }
      cyBox.dataset.cursor = cursor;
    }
    (async () => {
      while (mine === running && cyBox.isConnected) {
        try { await tick(); } catch (e) { status.textContent = 'live updates paused: ' + e.message; }
        await sleep(POLL_MS);
      }
    })();
  }

  KK.register({ id: 'view-map', title: 'Map', render });
})();
