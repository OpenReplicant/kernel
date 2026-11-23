// The UI shell (D-44), as a desktop (D-127). KK is the API that view modules use: they call MCP tools as the signed-in
// person and register themselves; the shell lists them from /ui/views.json and loads their script on first use.
// Framed apps come from /ui/apps.json when the deployment's front door serves one (none otherwise); each is another
// web UI on an origin of its own, in a sandboxed frame that never sees the token. The bridge takes two messages from
// a framed app, and only from its own frame and origin: {kk: 'title', text} and {kk: 'open', id} (a launcher entry).
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

const CORE = [{ id: 'search', title: 'Search' }, { id: 'review', title: 'Review' }, { id: 'changes', title: 'Change sets' },
              { id: 'graph', title: 'Graph' }, { id: 'me', title: 'Me' }];
const SIZE = { search: [760, 520], review: [720, 560], changes: [860, 480], graph: [900, 680], me: [420, 260],
               explain: [520, 520] };
const LAYOUT = 'kk_layout';   // window places in localStorage (never the token)

// The apps a deployment lists: [{id, title, url, frame?}]; kept only when the url is http(s) on another origin.
function apps(list) {
  const out = [];
  for (const a of Array.isArray(list) ? list : []) {
    try {
      const u = new URL(a.url, location.href);
      if (!/^https?:$/.test(u.protocol) || u.origin === location.origin || !/^[a-z0-9-]+$/.test(a.id)) continue;
      out.push({ name: a.id, title: String(a.title || a.id), url: u.href, origin: u.origin, frame: a.frame !== false });
    } catch (e) { /* not a URL */ }
  }
  return out;
}

function shell() {
  return {
    token: '', name: '', password: '', me: null, error: '', query: '', searched: false, detail: null,
    found: [], queue: [], changes: [], notes: {}, views: [], apps: [], graphBy: '', graphOn: '', graphInfo: '',
    modules: [], slots: [], open: [], top: '', z: 10, drag: null, saved: {},

    async init() {
      try { this.token = sessionStorage.getItem('kk_token') || ''; } catch (e) { /* storage blocked */ }
      try { this.saved = JSON.parse(localStorage.getItem(LAYOUT) || '{}'); } catch (e) { this.saved = {}; }
      KK.token = this.token;
      KK.explain = id => this.explain(id);
      try { this.views = await (await fetch('/ui/views.json')).json(); } catch (e) { this.views = []; }
      try {
        const r = await fetch('/ui/apps.json');
        this.apps = r.ok ? apps(await r.json()) : [];
      } catch (e) { this.apps = []; }
      window.addEventListener('message', e => this.bridge(e));
      window.addEventListener('pointermove', e => this.move(e));
      window.addEventListener('pointerup', () => this.drop());
      new ResizeObserver(() => { if (cy) cy.resize(); }).observe(document.getElementById('cy'));
      if (this.token) await this.signIn();
    },

    // ---- the launcher
    groups() {
      const review = this.me && this.me.awaiting_review;
      return [
        { title: 'Kernel', items: CORE.map(t => ({ ...t, badge: t.id === 'review' && review ? String(review) : '' })) },
        ...(this.views.length ? [{ title: 'Views', items: this.views.map(v => ({ id: 'view:' + v.name, title: v.title })) }] : []),
        ...(this.apps.length ? [{ title: 'Apps', items: this.apps.map(a => ({ id: 'app:' + a.name, title: a.title })) }] : []),
      ];
    },
    entry(id) {
      if (id.startsWith('view:')) {
        const v = this.views.find(x => 'view:' + x.name === id);
        return v && { id, kind: 'view', name: v.name, title: v.title };
      }
      if (id.startsWith('app:')) {
        const a = this.apps.find(x => 'app:' + x.name === id);
        return a && { id, kind: 'app', ...a };
      }
      const c = CORE.find(x => x.id === id) || (id === 'explain' ? { id, title: 'Claim' } : null);
      return c && { ...c, kind: 'core' };
    },
    // Open its window, bring it to the front and refresh what it shows; an app that refuses framing opens on its own.
    async launch(id) {
      const e = this.entry(id);
      if (!e) return;
      if (e.kind === 'app' && !e.frame) { window.open(e.url, '_blank', 'noopener,noreferrer'); return; }
      let w = this.win(id);
      if (!w) {
        const n = this.open.length, [dw, dh] = SIZE[id] || [820, 600], s = this.saved[id] || {};
        w = { ...e, x: s.x ?? 16 + 28 * (n % 8), y: s.y ?? 12 + 28 * (n % 8), w: s.w ?? dw, h: s.h ?? dh, max: s.max ?? e.kind === 'view', min: false, z: 0 };   // a view fills the desk at first
        this.open.push(w);
        w = this.win(id);
      }
      w.min = false;
      this.focus(id);
      this.remember();
      await this.$nextTick();
      await this.load(w);
    },
    async load(w) {
      this.error = '';
      if (w.id === 'review') await this.run(async () => { this.queue = await KK.call('review_queue'); });
      if (w.id === 'changes') await this.run(async () => { this.changes = await KK.call('list_change_sets', { limit: 100 }); });
      if (w.id === 'me') await this.run(async () => { this.me = await KK.call('whoami'); });
      if (w.id === 'graph') await this.run(async () => {
        if (!this.modules.length) {
          this.modules = (await KK.call('list_modules')).map(m => ({ id: m.iri, label: KK.short(m.iri) }));
          this.slots = (await KK.call('list_slots')).map(s => ({ id: s.iri, label: KK.short(s.iri) }));
        }
        await this.drawGraph();
      });
      if (w.kind === 'view') await this.run(() => this.showView(w.name));
    },

    // ---- windows
    win(id) { return this.open.find(w => w.id === id); },
    extra() { return this.open.filter(w => w.kind !== 'core'); },
    isOpen(id) { return !!this.win(id); },
    isMin(id) { const w = this.win(id); return !!(w && w.min); },
    shown(id) { const w = this.win(id); return !!(w && !w.min); },
    // An object, not a string: Alpine sets a string as the whole style attribute, which would wipe x-show's display:none.
    place(id) {
      const w = this.win(id);
      if (!w) return {};
      return w.max ? { left: '0', top: '0', width: '100%', height: '100%', zIndex: w.z }
                   : { left: w.x + 'px', top: w.y + 'px', width: w.w + 'px', height: w.h + 'px', zIndex: w.z };
    },
    focus(id) {
      const w = this.win(id);
      if (!w || (this.top === id && w.z === this.z)) return;
      w.z = ++this.z; this.top = id;
    },
    restore(id) { const w = this.win(id); if (w) { w.min = false; this.focus(id); } },
    minimise(id) { const w = this.win(id); if (w) w.min = true; },
    toggleMax(id) { const w = this.win(id); if (w) { w.max = !w.max; this.remember(); } },
    close(id) {
      this.open = this.open.filter(w => w.id !== id);
      if (id === 'explain') this.detail = null;
      if (id === 'graph' && cy) { cy.destroy(); cy = null; }
      this.remember();
    },
    grab(ev, id) {
      const w = this.win(id);
      if (!w || w.max || ev.button !== 0 || ev.target.closest('button, a, select, input')) return;
      this.focus(id);
      this.drag = { id, dx: ev.clientX - w.x, dy: ev.clientY - w.y };
      ev.preventDefault();
    },
    move(ev) {
      if (!this.drag) return;
      const w = this.win(this.drag.id);
      if (w) { w.x = Math.max(0, ev.clientX - this.drag.dx); w.y = Math.max(0, ev.clientY - this.drag.dy); }
    },
    drop() {   // after a move or a resize (the CSS handle): keep what the person set before Alpine redraws the window
      this.drag = null;
      if (this.open.length) this.remember();
    },
    // Sizes set by the person (the windows' CSS resize handle) and places, kept for next time.
    remember() {
      for (const w of this.open) {
        const el = document.querySelector(`[data-window="${w.id}"]`);
        if (el && !w.max && !w.min && el.offsetWidth) { w.w = el.offsetWidth; w.h = el.offsetHeight; }
        this.saved[w.id] = { x: w.x, y: w.y, w: w.w, h: w.h, max: w.max };
      }
      try { localStorage.setItem(LAYOUT, JSON.stringify(this.saved)); } catch (e) { /* storage blocked */ }
    },

    // ---- the bridge: a framed app may set its window's title or open a launcher entry; nothing else crosses
    bridge(e) {
      const w = this.open.find(x => x.kind === 'app' && x.origin === e.origin
        && document.querySelector(`iframe[data-app="${x.name}"]`)?.contentWindow === e.source);
      const m = e.data;
      if (!w || !m || typeof m !== 'object') return;
      if (m.kk === 'title' && typeof m.text === 'string') w.title = m.text.slice(0, 80) || w.title;
      if (m.kk === 'open' && typeof m.id === 'string' && this.entry(m.id)) this.launch(m.id);
    },

    // ---- signing in
    async signIn() {
      KK.token = this.token;
      try { sessionStorage.setItem('kk_token', this.token); } catch (e) { /* storage blocked */ }
      await this.run(async () => { this.me = await KK.call('whoami'); });
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
      this.token = ''; this.me = null; this.found = []; this.queue = []; this.changes = []; this.detail = null; KK.token = '';
      this.open = []; if (cy) { cy.destroy(); cy = null; }
      try { sessionStorage.removeItem('kk_token'); } catch (e) { /* storage blocked */ }
    },
    async run(fn) {
      this.error = '';
      try { await fn(); } catch (e) { this.error = e.message; }
    },

    // ---- the kernel's own tools
    async find() {
      await this.run(async () => { this.found = await KK.call('search', { text: this.query, limit: 50 }); this.searched = true; });
    },
    async explain(id) {
      await this.run(async () => {
        const body = await KK.call('explain', { claim_id: id });
        if (!this.win('explain')) await this.launch('explain'); else this.restore('explain');
        this.detail = { id, body };
      });
    },
    async decide(id, approve) {
      await this.run(async () => {
        await KK.call('review', { change_set_id: id, approve, note: this.notes[id] || null });
        this.queue = await KK.call('review_queue');
        this.me = await KK.call('whoami');
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
      const meta = this.views.find(v => v.name === name), el = document.querySelector(`.view[data-view="${name}"]`);
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
