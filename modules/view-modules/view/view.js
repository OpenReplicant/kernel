// A view module (D-44): registers itself with the UI shell and renders with MCP tools, as the signed-in
// person. Every value links to the claim behind it (invariant 6: cite claim ids, show belief).
KK.register({
  id: 'view-modules',
  title: 'Modules',
  async render(el, kk) {
    const modules = await kk.call('list_modules');
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Module</th><th>Version</th><th>Kind</th><th>Fills</th><th>Runs as</th></tr></thead>';
    const body = table.createTBody();
    const cell = (row, claims) => {
      const td = row.insertCell();
      claims.forEach((c, i) => {
        const a = document.createElement('a');
        a.href = '#'; a.title = `claim ${c.id}, belief ${c.belief ?? '—'}`;
        a.textContent = c.object_entity ? kk.short(c.object_entity) : String(c.object_value && c.object_value['@value']);
        a.addEventListener('click', ev => { ev.preventDefault(); kk.explain(c.id); });
        if (i) td.append(', ');
        td.append(a);
      });
    };
    const pick = (m, local) => m.claims.filter(c => c.predicate.endsWith(local));
    for (const m of modules) {
      const row = body.insertRow();
      row.dataset.module = m.iri;
      row.insertCell().textContent = kk.short(m.iri);
      cell(row, pick(m, '#versionInfo'));
      cell(row, pick(m, '#moduleKind'));
      cell(row, pick(m, '#fills'));
      cell(row, pick(m, '#runsAs'));
    }
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = `${modules.length} modules in the self-model. Click a value to see its claim.`;
    el.append(note, table);
  },
});
