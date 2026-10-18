// The UI shell (D-44). KK is the API that view modules use: they call MCP tools as the signed-in person
// and register themselves; the shell lists them from /ui/views.json and loads their script on first use.
let cy = null;  // the Cytoscape instance stays outside Alpine's reactive state

const KK = {
  token: '',
  views: {},
  onRegister: null,

  // One MCP tool call (streamable HTTP, stateless, JSON response) with the person's own token.
  async call(name, args = {}) {
    const r = await fetch('/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
                 ...(KK.token ? { Authorization: 'Bearer ' + KK.token } : {}) },
      body: JSON.stringify({ jsonrpc: '2.0', id: Date.now(), method: 'tools/call', params: { name, arguments: args } }),
    });
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()) || r.statusText}`);
    const msg = await r.json();
    if (msg.error) throw new Error(msg.error.message);
    const res = msg.result, text = res.content && res.content[0] ? res.content[0].text : '';
    if (res.isError) throw new Error(text);
    if (res.structuredContent) return 'result' in res.structuredContent ? res.structuredContent.result : res.structuredContent;
    return text ? JSON.parse(text) : null;
  },

  register(view) {  // view: {id, title, render(element, KK)}
    KK.views[view.id] = view;
    if (KK.onRegister) KK.onRegister(view);
  },

  short(iri) {
    if (!iri) return '';
    const m = String(iri).match(/[#/:]([^#/:]+)\/?$/);
    return m ? m[1] : iri;
  },

  explain: null,  // set by the shell: KK.explain(claimId) shows a claim's sources and history
};

function shell() {
  return {
    tab: 'search', token: '', name: '', password: '', me: null, error: '', rows: [], query: '', searched: false, detail: null,
    notes: {}, views: [], graphBy: '', graphOn: '', graphInfo: '', modules: [], slots: [],

    async init() {
      try { this.token = sessionStorage.getItem('kk_token') || ''; } catch (e) { /* storage blocked */ }
      KK.token = this.token;
      KK.explain = id => this.explain(id);
      try { this.views = await (await fetch('/ui/views.json')).json(); } catch (e) { this.views = []; }
      if (this.token) await this.signIn();
    },
    tabs() {
      return [{ id: 'search', title: 'Search' }, { id: 'review', title: 'Review' }, { id: 'changes', title: 'Change sets' },
              { id: 'graph', title: 'Graph' }, { id: 'me', title: 'Me' },
              ...this.views.map(v => ({ id: 'view:' + v.name, title: v.title }))];
    },
    async signIn() {
      KK.token = this.token;
      try { sessionStorage.setItem('kk_token', this.token); } catch (e) { /* storage blocked */ }
      await this.run(async () => { this.me = await KK.call('whoami'); });
      if (this.me) this.open(this.tab);
    },
    async login() {
      await this.run(async () => {
        const r = await fetch('/ui/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                             body: JSON.stringify({ name: this.name, password: this.password }) });
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || r.statusText);
        this.token = body.token;
      });
      this.password = '';
      if (this.token) await this.signIn();
    },
    signOut() {
      this.token = ''; this.me = null; this.rows = []; this.detail = null; KK.token = '';
      try { sessionStorage.removeItem('kk_token'); } catch (e) { /* storage blocked */ }
    },
    async run(fn) {
      this.error = '';
      try { await fn(); } catch (e) { this.error = e.message; }
    },
    async open(tab) {
      this.tab = tab; this.rows = []; this.detail = null;
      if (tab === 'review') await this.run(async () => { this.rows = await KK.call('review_queue'); });
      if (tab === 'changes') await this.run(async () => { this.rows = await KK.call('list_change_sets', { limit: 100 }); });
      if (tab === 'me') await this.run(async () => { this.me = await KK.call('whoami'); });
      if (tab === 'graph') await this.run(async () => {
        if (!this.modules.length) {
          this.modules = (await KK.call('list_modules')).map(m => ({ id: m.iri, label: KK.short(m.iri) }));
          this.slots = (await KK.call('list_slots')).map(s => ({ id: s.iri, label: KK.short(s.iri) }));
        }
        await this.drawGraph();
      });
      if (tab.startsWith('view:')) await this.run(() => this.showView(tab.slice(5)));
    },
    async find() {
      await this.run(async () => { this.rows = await KK.call('search', { text: this.query, limit: 50 }); this.searched = true; });
    },
    async explain(id) {
      await this.run(async () => { this.detail = { id, body: await KK.call('explain', { claim_id: id }) }; });
    },
    async decide(id, approve) {
      await this.run(async () => {
        await KK.call('review', { change_set_id: id, approve, note: this.notes[id] || null });
        this.rows = await KK.call('review_queue');
      });
    },
    choices() { return this.graphBy === 'module' ? this.modules : this.graphBy === 'slot' ? this.slots : []; },
    async drawGraph() {
      if (this.graphBy && !this.graphOn) return;
      const g = await KK.call('graph', this.graphBy ? { [this.graphBy]: this.graphOn } : {});
      const css = getComputedStyle(document.documentElement), v = name => css.getPropertyValue(name).trim();
      if (cy) cy.destroy();
      cy = cytoscape({
        container: document.getElementById('cy'), elements: g, wheelSensitivity: 0.3,
        layout: { name: this.graphBy ? 'breadthfirst' : 'cose', directed: true, padding: 20, animate: false },
        style: [
          { selector: 'node', style: { 'background-color': n => v('--k-' + n.data('kind')) || v('--k-entity'),
              label: 'data(label)', 'font-size': 9, color: v('--fg'), 'text-wrap': 'ellipsis', 'text-max-width': 120,
              width: 14, height: 14 } },
          { selector: 'node[kind = "module"]', style: { width: 22, height: 22, 'font-size': 11 } },
          { selector: 'edge', style: { 'curve-style': 'bezier', 'target-arrow-shape': 'triangle',
              'line-color': v('--edge'), 'target-arrow-color': v('--edge'),
              width: e => 0.5 + 3 * (e.data('belief') ?? 0), opacity: e => 0.3 + 0.7 * (e.data('belief') ?? 0),
              label: 'data(label)', 'font-size': 8, color: v('--muted'), 'text-rotation': 'autorotate' } },
          { selector: 'edge[belief < 0.5]', style: { 'line-style': 'dashed' } },
        ],
      });
      cy.on('tap', 'edge', ev => this.explain(ev.target.id()));
      const el = document.getElementById('cy');
      el.dataset.nodes = cy.nodes().length; el.dataset.edges = cy.edges().length;
      this.graphInfo = `${cy.nodes().length} entities, ${cy.edges().length} claims`;
    },
    async showView(name) {
      const meta = this.views.find(v => v.name === name), el = document.getElementById('view');
      if (!KK.views[name]) {
        await new Promise((resolve, reject) => {
          KK.onRegister = view => { if (view.id === name) resolve(); };
          const s = document.createElement('script');
          s.src = meta.src; s.onerror = () => reject(new Error('could not load view ' + name));
          document.head.appendChild(s);
        });
      }
      el.replaceChildren();
      await KK.views[name].render(el, KK);
    },
    short: iri => KK.short(iri),
    belief: b => (b === null || b === undefined ? '—' : Number(b).toFixed(2)),
    objectOf(c) {
      if (c.object_entity) return KK.short(c.object_entity);
      const v = c.object_value && c.object_value['@value'];
      return v === undefined ? JSON.stringify(c.object_value) : String(v);
    },
    opText(op) {
      if (op.op === 'add_claim') {
        const o = op.object || {};
        return `${op.subject} ${op.predicate} ${o.entity || JSON.stringify(o.value && o.value['@value'] !== undefined ? o.value['@value'] : o.value)}`;
      }
      return op.claim ? `${op.claim} → ${op.status || ''} ${op.reason || ''}` : JSON.stringify(op);
    },
  };
}
