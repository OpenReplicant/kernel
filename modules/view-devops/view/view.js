// The devops view (M2-T7, D-54): services with their latest observations and anomalies, and the MAPE-K
// timeline of each adaptation, built from derivations (explain), approvals (get_change_set) and the
// actuator's runs (list_runs). Reads with the signed-in person's MCP tools only.
(() => {
  const SOSA = 'http://www.w3.org/ns/sosa/';
  const OA = 'http://www.w3.org/ns/oa#';
  const PPLAN = 'http://purl.org/net/p-plan#';
  const TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';
  const DCT_TYPE = 'http://purl.org/dc/terms/type';
  const NODE_TEMPLATE = 'https://w3id.org/knowledge-kernel/tosca#NodeTemplate';
  const PROPS = { 'replicas-up': 'replicas', 'request-rate': 'requests/s', 'latency-seconds-mean': 'latency (s)' };

  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (e.className = v) : k.includes('-') ? e.setAttribute(k, v) : (e[k] = v)));
    e.append(...kids);
    return e;
  };
  const val = c => c && c.object_value && c.object_value['@value'];
  const prop = c => ((c.qualifiers || {})[SOSA + 'observedProperty'] || '').split(':').pop();
  const time = t => (t ? new Date(t).toLocaleTimeString() : '—');
  const link = (kk, c, text) => {
    const a = el('a', { href: '#', textContent: text, title: `claim ${c.id}, belief ${c.belief ?? '—'}` });
    a.addEventListener('click', ev => { ev.preventDefault(); kk.explain(c.id); });
    return a;
  };
  const observations = claims => claims.filter(c => c.predicate === SOSA + 'hasSimpleResult' && c.evidence_type === 'observed' && PROPS[prop(c)]);
  const latest = (obs, p) => obs.filter(c => prop(c) === p).sort((a, b) => (a.valid_from < b.valid_from ? 1 : -1))[0];

  async function services(kk) {
    const nodes = await kk.call('query_claims', { predicate: TYPE, object_entity: NODE_TEMPLATE, limit: 500 });
    const out = [];
    for (const iri of [...new Set(nodes.map(c => c.subject))].sort()) {
      const card = await kk.call('get_entity', { iri });
      const obs = observations(card.claims || []);
      if (!obs.length) continue;
      out.push({ iri, obs, anomalies: (card.referenced_by || []).filter(c => c.predicate === OA + 'hasTarget' && c.subject.startsWith('urn:kk:anomaly:')) });
    }
    return out;
  }

  // One adaptation: walk back from the plan step through its derivations, forward through its runs.
  async function timeline(kk, step) {
    const card = await kk.call('get_entity', { iri: step });
    const claims = card.claims || [];
    const get = p => claims.find(c => c.predicate === p);
    const action = get(DCT_TYPE), target = get(SOSA + 'hasFeatureOfInterest'), n = get(SOSA + 'hasSimpleResult');
    const stages = [];
    const planned = await kk.call('explain', { claim_id: (action || claims[0]).id });   // full row: change set, time
    const why = planned.derived_from || [];
    const anomalyClaims = await Promise.all(why.map(async id => (await kk.call('explain', { claim_id: id })).claim));
    const anomaly = anomalyClaims.find(c => c && c.predicate === OA + 'bodyValue') || anomalyClaims[0];
    let seen = [];
    if (anomaly) {
      const premises = (await kk.call('explain', { claim_id: anomaly.id })).derived_from || [];
      seen = (await Promise.all(premises.map(async id => (await kk.call('explain', { claim_id: id })).claim)))
        .filter(c => c && c.evidence_type === 'observed');
    }
    stages.push({ stage: 'observed', at: seen[0] && seen[0].valid_from,
      parts: seen.map(c => [c, `${PROPS[prop(c)] || prop(c)} ${val(c)}`]), empty: 'no observation cited' });
    stages.push({ stage: 'analysed', at: anomaly && anomaly.valid_from,
      parts: anomaly ? [[anomaly, String(val(anomaly) || anomaly.subject)]] : [], empty: 'no anomaly cited' });
    const what = action ? action.object_entity.split(':').pop() : '?';
    stages.push({ stage: 'planned', at: planned.claim && planned.claim.recorded_at,
      parts: action ? [[action, `${what} ${target ? target.object_entity.split(':').pop() : ''}${n ? ' to ' + val(n) : ''}`]] : [],
      empty: 'no action' });
    let decision = null;
    try { decision = await kk.call('get_change_set', { change_set_id: planned.claim.change_set }); } catch (e) { /* not a reviewer */ }
    stages.push({ stage: 'approved', at: decision && decision.decided_at,
      text: !decision ? 'decision visible to reviewers'
        : decision.lane !== 'review' ? 'skipped the review lane: the actuator will not carry it out'
        : decision.decided_by ? `${decision.status} — decided by ${decision.decided_by}` : `${decision.status}, waiting for a person in Review` });
    const runs = await kk.call('list_runs', { plan_step: step, limit: 5 });
    const done = runs.find(r => r.status !== 'running');
    stages.push({ stage: 'executed', at: done && done.ended_at,
      text: done ? `${done.status}${done.outputs && done.outputs.after !== undefined ? `: ${done.outputs.before} → ${done.outputs.after} replicas` : ''}${done.outputs && done.outputs.refused ? `: refused — ${done.outputs.refused}` : ''} (run ${done.id.slice(0, 8)}, by ${done.agent.split(':').pop()})`
        : 'not carried out yet' });
    let after = [];
    if (done && target) {
      const node = await kk.call('get_entity', { iri: target.object_entity });
      const obs = observations(node.claims || []).filter(c => c.valid_from >= done.ended_at);
      const first = obs.map(c => c.valid_from).sort()[0];
      after = obs.filter(c => c.valid_from === first);
    }
    stages.push({ stage: 'observed again', at: after[0] && after[0].valid_from,
      parts: after.map(c => [c, `${PROPS[prop(c)] || prop(c)} ${val(c)}`]), empty: 'no observation since' });
    const part = get(PPLAN + 'isStepOfPlan');
    const plan = part ? await kk.call('get_entity', { iri: part.object_entity }) : null;
    const label = plan && (plan.claims || []).find(c => c.predicate === 'http://www.w3.org/2000/01/rdf-schema#label');
    return { step, label: label ? String(val(label)) : `${what} (${step})`, stages };
  }

  KK.register({
    id: 'view-devops',
    title: 'Devops',
    async render(root, kk) {
      const svc = await services(kk);
      root.append(el('h3', { textContent: 'Services' }));
      if (!svc.length) root.append(el('p', { class: 'muted', textContent: 'No observations yet: install the metrics observer.' }));
      else {
        const table = el('table', { id: 'devops-services' });
        table.innerHTML = '<thead><tr><th>Service</th><th>Replicas</th><th>Requests/s</th><th>Latency (s)</th><th>Anomalies</th></tr></thead>';
        const body = table.createTBody();
        for (const s of svc) {
          const row = body.insertRow();
          row.dataset.service = s.iri;
          row.insertCell().textContent = kk.short(s.iri);
          for (const p of ['replicas-up', 'request-rate', 'latency-seconds-mean']) {
            const c = latest(s.obs, p), td = row.insertCell();
            if (c) td.append(link(kk, c, String(val(c))));
          }
          const td = row.insertCell();
          td.textContent = s.anomalies.length ? `${s.anomalies.length}` : '—';
          if (s.anomalies.length) td.className = 'bad';
        }
        root.append(el('p', { class: 'muted', textContent: 'Latest window of each service. Click a value for its claim.' }), table);
      }
      root.append(el('h3', { textContent: 'Adaptations (MAPE-K)' }));
      const steps = await kk.call('query_claims', { predicate: PPLAN + 'isStepOfPlan', limit: 200 });
      if (!steps.length) { root.append(el('p', { class: 'muted', textContent: 'No adaptation planned yet.' })); return; }
      for (const s of steps.slice(-5).reverse()) {
        const t = await timeline(kk, s.subject);
        const ol = el('ol', { class: 'timeline' });
        ol.dataset.step = t.step;
        for (const st of t.stages) {
          const li = el('li', {}, el('strong', { textContent: `${st.stage} ` }), el('span', { class: 'muted', textContent: `${time(st.at)} · ` }));
          li.dataset.stage = st.stage;
          if (st.parts) {
            if (!st.parts.length) li.append(el('span', { class: 'bad', textContent: st.empty }));
            st.parts.forEach(([c, text], i) => { if (i) li.append(', '); li.append(link(kk, c, text)); });
          } else li.append(st.text);
          ol.append(li);
        }
        root.append(el('h4', { textContent: t.label, title: t.step }), ol);
      }
    },
  });
})();
