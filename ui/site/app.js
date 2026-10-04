// The explorer: a read-only view of one kernel database. Every call goes to /api, which
// Caddy forwards to PostgREST running as the reader role (ui/Caddyfile, ADR 0016). Pages
// are hash routes; each route has a loader that returns the page's data.

const API = "/api";
const ENDPOINTS =
  "from:nodes!edges_from_id_fkey(id,name,type,kind),to:nodes!edges_to_id_fkey(id,name,type,kind)";
const NODE_COLUMNS =
  "id,type,kind,namespace,name,aliases,identity,props,trust_level,status,belief_status,belief_score," +
  "belief_for,belief_against,superseded_by,redacted,claim_id,created_offset,updated_offset,created_at,updated_at";
const EDGE_COLUMNS =
  "id,edge,kind,props,valid_from,valid_to,window_agreed,belief_status,belief_score,belief_for," +
  "belief_against,sources_for,sources_against,contested_with,claim_id,created_offset,updated_offset";
const EDGE_ROW = `id,edge,kind,valid_from,valid_to,belief_status,belief_score,${ENDPOINTS}`;
const NODE_TYPES = ["Entity", "Agent", "Claim", "Event"];
const PAGE = 25;
const GRAPH_LIMIT = 400;

// --- API ---------------------------------------------------------------------------------

async function failure(res) {
  try {
    const body = await res.json();
    return body.message || body.detail || `${res.status} ${res.statusText}`;
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

async function get(path) {
  const res = await fetch(`${API}/${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(await failure(res));
  return res.json();
}

async function count(path) {
  const sep = path.includes("?") ? "&" : "?";
  const select = /[?&]select=/.test(path) ? "" : "select=id&";
  const res = await fetch(`${API}/${path}${sep}${select}limit=0`, {
    headers: { Accept: "application/json", Prefer: "count=exact" },
  });
  if (!res.ok) throw new Error(await failure(res));
  return Number((res.headers.get("Content-Range") || "/0").split("/")[1]);
}

async function rpc(name, args = {}) {
  const res = await fetch(`${API}/rpc/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(await failure(res));
  return res.json();
}

const enc = encodeURIComponent;
const inList = (ids) => enc(`in.(${[...new Set(ids)].map((id) => `"${id}"`).join(",")})`);
const one = (rows, what) => {
  if (!rows.length) throw new Error(`No ${what} with that id.`);
  return rows[0];
};

async function namesOf(ids) {
  const wanted = [...new Set(ids.filter((id) => /^(ent|agt|clm|evt)_/.test(id)))];
  if (!wanted.length) return {};
  const rows = await get(`nodes?select=id,name,type&id=${inList(wanted)}`);
  return Object.fromEntries(rows.map((n) => [n.id, n]));
}

async function titlesOf(ids) {
  const wanted = [...new Set(ids.filter((id) => id && id.startsWith("src_")))];
  if (!wanted.length) return {};
  const rows = await get(`sources?select=id,title&id=${inList(wanted)}`);
  return Object.fromEntries(rows.map((s) => [s.id, s.title]));
}

function opIds(op) {
  return ["id", "node_id", "node", "from", "to", "claim"].map((k) => op[k]).filter((v) => typeof v === "string");
}

// --- Loaders -----------------------------------------------------------------------------

const LOADERS = {
  async overview() {
    const [version, head, packs, contested, log, ...counts] = await Promise.all([
      rpc("version"),
      rpc("head_offset"),
      get("packs?select=name,version,install_seq,installed_at&order=install_seq"),
      get(`edges?select=${EDGE_ROW}&belief_status=eq.contested&order=updated_offset.desc&limit=20`),
      rpc("query_log", { p_filter: { limit: 8 } }),
      ...NODE_TYPES.map((t) => count(`nodes?type=eq.${t}`)),
      count("edges"),
      count("sources"),
      count("claims_view"),
      count("claims_view?resolution=eq.unresolved"),
    ]);
    const [entity, agent, claim, event, edges, sources, claims, unresolved] = counts;
    const names = await namesOf(log.entries.flatMap((e) => e.ops.flatMap(opIds)));
    return {
      version,
      head,
      packs,
      contested,
      log: log.entries,
      names,
      counts: { entity, agent, claim, event, edges, sources, claims, unresolved },
    };
  },

  async search(term) {
    if (/^(ent|agt|clm|evt|edg|src)_[0-9A-Z]+$/.test(term)) {
      location.replace(href(term));
      return { term, nodes: [], sources: [] };
    }
    if (!term) return { term, nodes: [], sources: [] };
    const like = enc(`*${term}*`);
    const [nodes, sources] = await Promise.all([
      get(`nodes?select=id,type,kind,namespace,name,status,belief_status&name=ilike.${like}&order=type,name&limit=100`),
      get(`sources?select=id,title,uri,media_type,recorded_at&title=ilike.${like}&order=recorded_at.desc&limit=25`),
    ]);
    return { term, nodes, sources };
  },

  async node(id) {
    const [rows, out, inc, log] = await Promise.all([
      get(`nodes?select=${NODE_COLUMNS}&id=eq.${enc(id)}`),
      get(`edges?select=${EDGE_ROW}&from_id=eq.${enc(id)}&order=edge,kind,created_offset`),
      get(`edges?select=${EDGE_ROW}&to_id=eq.${enc(id)}&order=edge,kind,created_offset`),
      rpc("query_log", { p_filter: { node_id: id, limit: 50 } }),
    ]);
    const node = one(rows, "node");
    const claim = node.claim_id
      ? (await get(`claims_view?select=id,text,source_id,chunk_id,basis,modality,log_offset&id=eq.${enc(node.claim_id)}`))[0]
      : null;
    const titles = await titlesOf([claim?.source_id, ...log.entries.map((e) => e.claim.source_id)]);
    return { node, out, inc, log: log.entries, claim, titles, names: await namesOf(log.entries.flatMap((e) => e.ops.flatMap(opIds))) };
  },

  async edge(id) {
    const [rows, assertions, conflicts, head] = await Promise.all([
      get(`edges?select=${EDGE_COLUMNS},${ENDPOINTS}&id=eq.${enc(id)}`),
      get(
        `assertions?select=id,log_offset,agent_id,source_key,polarity,valid_from,valid_to,basis,modality,confidence,weight,recorded_at,` +
          `claim:claims_view(id,text,source_id,chunk_id,trust,redacted)&target_type=eq.edge&target_id=eq.${enc(id)}&order=log_offset`,
      ),
      get(`conflicts?or=${enc(`(edge_a.eq.${id},edge_b.eq.${id})`)}&order=log_offset`),
      rpc("head_offset"),
    ]);
    const edge = one(rows, "edge");
    const counted = new Set(
      await get(`rpc/counted_assertions?p_target_type=edge&p_target_id=${enc(id)}&p_offset=${head}`),
    );
    const others = conflicts.map((c) => (c.edge_a === id ? c.edge_b : c.edge_a));
    const [rivals, titles] = await Promise.all([
      others.length ? get(`edges?select=${EDGE_ROW}&id=${inList(others)}`) : [],
      titlesOf(assertions.map((a) => a.claim?.source_id)),
    ]);
    // Belief as of each offset where something about this edge was learned.
    const offsets = [...new Set([...assertions.map((a) => a.log_offset), ...conflicts.map((c) => c.log_offset)])]
      .sort((a, b) => a - b)
      .slice(-20);
    const states = await Promise.all(
      offsets.map((o) => get(`rpc/edge_state_as_of?p_edge_id=${enc(id)}&p_offset=${o}`)),
    );
    return {
      edge,
      head,
      assertions: assertions.map((a) => ({ ...a, counted: counted.has(a.id) })),
      conflicts,
      rivals: Object.fromEntries(rivals.map((r) => [r.id, r])),
      titles,
      timeline: offsets.map((offset, i) => ({ offset, ...states[i] })),
    };
  },

  async source(id) {
    const [rows, chunks, claims] = await Promise.all([
      get(`sources?select=id,title,uri,collection,media_type,author_agent_id,agent_id,metadata,content_hash,recorded_at&id=eq.${enc(id)}`),
      get(`chunks?select=id,seq,page,heading,text&source_id=eq.${enc(id)}&order=seq`),
      get(`claims_view?select=id,text,chunk_id,basis,modality,confidence,trust,resolution,log_offset,redacted&source_id=eq.${enc(id)}&order=log_offset`),
    ]);
    const source = one(rows, "source");
    const backed = claims.length
      ? await get(`assertions?select=claim_id,target_type,target_id,polarity&claim_id=${inList(claims.slice(0, 200).map((c) => c.id))}`)
      : [];
    const edgeIds = backed.filter((a) => a.target_type === "edge").map((a) => a.target_id);
    const edges = edgeIds.length ? await get(`edges?select=${EDGE_ROW}&id=${inList(edgeIds.slice(0, 300))}`) : [];
    const byId = Object.fromEntries(edges.map((e) => [e.id, e]));
    const byChunk = {};
    for (const c of claims) {
      c.edges = backed.filter((a) => a.claim_id === c.id && byId[a.target_id]).map((a) => ({ ...byId[a.target_id], polarity: a.polarity }));
      (byChunk[c.chunk_id || ""] ||= []).push(c);
    }
    return { source, chunks, claims, byChunk };
  },

  async log(before) {
    const filter = { limit: PAGE };
    if (before) filter.before_offset = Number(before);
    const page = await rpc("query_log", { p_filter: filter });
    const contested = page.entries.flatMap((e) => (e.conflicts || []).flatMap((c) => [c.edge_a, c.edge_b]));
    const [names, titles, edges] = await Promise.all([
      namesOf(page.entries.flatMap((e) => e.ops.flatMap(opIds))),
      titlesOf(page.entries.map((e) => e.claim.source_id)),
      contested.length ? get(`edges?select=${EDGE_ROW}&id=${inList(contested)}`) : [],
    ]);
    const last = page.entries.at(-1);
    return {
      entries: page.entries,
      head: page.head_offset,
      names,
      titles,
      edges: Object.fromEntries(edges.map((e) => [e.id, e])),
      next: last && last.offset > 1 ? last.offset : null,
    };
  },

  async evidence() {
    const claims = await get(
      `nodes?select=id,name,kind,status,belief_status,created_offset,` +
        `sup:edges!edges_to_id_fkey(count),con:edges!edges_to_id_fkey(count),` +
        `about:edges!edges_from_id_fkey(to:nodes!edges_to_id_fkey(id,name)),` +
        `by:edges!edges_to_id_fkey(from:nodes!edges_from_id_fkey(id,name))` +
        `&sup.edge=eq.supports&con.edge=eq.contradicts&about.edge=eq.about&by.edge=eq.responsible_for` +
        `&type=eq.Claim&order=created_offset.desc&limit=500`,
    );
    return {
      claims: claims.map((c) => ({ ...c, sup: c.sup[0]?.count ?? 0, con: c.con[0]?.count ?? 0 })),
    };
  },

  // The graph around a node (`<id>[/<hops>]`), of one area (`ns:<namespace>`) or, capped, of all.
  async graph(arg) {
    const [target, hopsText] = arg.split("/");
    const hops = Math.min(Math.max(Number(hopsText) || 2, 1), 3);
    let edges = [];
    let nodes = [];
    let title = "The graph";
    let center = null;
    if (!target) {
      edges = await get(`edges?select=${EDGE_ROW}&order=updated_offset.desc&limit=${GRAPH_LIMIT}`);
      title = "The whole graph";
    } else if (target.startsWith("ns:")) {
      const ns = enc(target.slice(3));
      const inner = (end) =>
        `edges?select=id,edge,kind,valid_from,valid_to,belief_status,belief_score,` +
        `from:nodes!edges_from_id_fkey${end === "from" ? "!inner" : ""}(id,name,type,kind,namespace),` +
        `to:nodes!edges_to_id_fkey${end === "to" ? "!inner" : ""}(id,name,type,kind,namespace)` +
        `&${end}.namespace=eq.${ns}&limit=${GRAPH_LIMIT}`;
      const [out, inc, own] = await Promise.all([
        get(inner("from")),
        get(inner("to")),
        get(`nodes?select=id,name,type,kind&namespace=eq.${ns}&limit=${GRAPH_LIMIT}`),
      ]);
      edges = [...new Map([...out, ...inc].map((e) => [e.id, e])).values()];
      nodes = own;
      title = `The ${target.slice(3)} area`;
    } else {
      center = target;
      const node = one(await get(`nodes?select=id,name,type,kind&id=eq.${enc(target)}`), "node");
      nodes = [node];
      const found = new Map();
      const seen = new Set([target]);
      let frontier = [target];
      for (let i = 0; i < hops && frontier.length && found.size < GRAPH_LIMIT; i++) {
        const ids = frontier.map((id) => `"${id}"`).join(",");
        const rows = await get(
          `edges?select=${EDGE_ROW}&or=${enc(`(from_id.in.(${ids}),to_id.in.(${ids}))`)}&limit=${GRAPH_LIMIT}`,
        );
        const next = [];
        for (const e of rows) {
          found.set(e.id, e);
          for (const end of [e.from.id, e.to.id]) {
            if (!seen.has(end)) {
              seen.add(end);
              next.push(end);
            }
          }
        }
        frontier = next.slice(0, 150);
      }
      edges = [...found.values()];
      title = `${node.name}, ${hops} hop${hops > 1 ? "s" : ""} out`;
    }
    const byId = new Map(nodes.map((n) => [n.id, n]));
    for (const e of edges) {
      byId.set(e.from.id, e.from);
      byId.set(e.to.id, e.to);
    }
    return {
      title,
      target: target || "",
      center,
      hops,
      edges: edges.map((e) => ({ ...e, label: edgeName(e) })),
      nodes: [...byId.values()],
      truncated: edges.length >= GRAPH_LIMIT,
    };
  },

  // The index of models: the whole graph, each area (namespace) and each system with its parts.
  async models() {
    const [namespaces, packs, head, nodes, edges, sources, contested, unresolved, open, systems] =
      await Promise.all([
        get("namespaces?order=name"),
        get("packs?select=name,version&order=install_seq"),
        rpc("head_offset"),
        count("nodes"),
        count("edges"),
        count("sources"),
        count("edges?belief_status=eq.contested"),
        count("claims_view?resolution=eq.unresolved"),
        count("nodes?type=eq.Claim&kind=eq.hypothetical&status=eq.open"),
        get(
          "nodes?select=id,name,parts:edges!edges_to_id_fkey(id,belief_status,belief_score,belief_for," +
            "from:nodes!edges_from_id_fkey(id,name,type,kind))" +
            "&parts.edge=eq.part_of&parts.kind=is.null&type=eq.Entity&kind=eq.system&order=name",
        ),
      ]);
    const versions = Object.fromEntries(packs.map((p) => [p.name, p.version]));
    const areas = await Promise.all(
      namespaces.map(async (ns) => {
        const q = enc(ns.name);
        const fromHere = `edges?select=id,from:nodes!edges_from_id_fkey!inner(namespace)&from.namespace=eq.${q}`;
        const [size, edgeCount, disputed, latest, kinds] = await Promise.all([
          count(`nodes?namespace=eq.${q}`),
          count(fromHere),
          count(`${fromHere}&belief_status=eq.contested`),
          get(`nodes?select=updated_offset&namespace=eq.${q}&order=updated_offset.desc&limit=1`),
          get(`nodes?select=kind&namespace=eq.${q}&limit=1000`),
        ]);
        const tally = {};
        for (const { kind } of kinds) tally[kind] = (tally[kind] || 0) + 1;
        return {
          ...ns,
          version: versions[ns.defined_by],
          nodes: size,
          edges: edgeCount,
          contested: disputed,
          latest: latest[0]?.updated_offset ?? null,
          kinds: Object.entries(tally).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
        };
      }),
    );
    for (const system of systems) {
      system.parts.sort(
        (a, b) =>
          (b.belief_score ?? 0) - (a.belief_score ?? 0) ||
          (b.belief_for ?? 0) - (a.belief_for ?? 0) ||
          a.from.name.localeCompare(b.from.name),
      );
    }
    return {
      head,
      totals: { nodes, edges, sources, contested, unresolved, open },
      areas: areas.sort((a, b) => b.nodes - a.nodes || a.name.localeCompare(b.name)),
      systems,
    };
  },

  async ontology() {
    const [namespaces, kinds, edgeKinds, rules, edgeTypes, nodeTypes, packs] = await Promise.all([
      get("namespaces?order=name"),
      get("kinds?order=node_type,name"),
      get("edge_kinds?order=edge,name"),
      get("rules?order=id"),
      get("edge_types?order=name"),
      get("node_types?order=name"),
      get("packs?select=name,version,kernel_range,install_seq,installed_at,updated_at&order=install_seq"),
    ]);
    return { namespaces, kinds, edgeKinds, rules, edgeTypes, nodeTypes, packs };
  },
};

// --- Formatting --------------------------------------------------------------------------

function href(id) {
  if (!id) return "#/";
  if (id.startsWith("edg_")) return `#/edge/${id}`;
  if (id.startsWith("src_")) return `#/source/${id}`;
  return `#/node/${id}`;
}

function day(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const iso = d.toISOString();
  return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

function windowOf(x) {
  if (!x.valid_from && !x.valid_to) return "";
  return `${x.valid_from ? day(x.valid_from) : "…"} → ${x.valid_to ? day(x.valid_to) : "now"}`;
}

const edgeName = (e) => (e.kind ? e.kind.replaceAll("_", " ") : e.edge.replaceAll("_", " "));
const score = (x) => (x === null || x === undefined ? "–" : Number(x).toFixed(2));
const json = (x) => JSON.stringify(x, null, 2);
const isEmpty = (x) => x === null || x === undefined || (typeof x === "object" && !Object.keys(x).length);

// --- Component ---------------------------------------------------------------------------

document.addEventListener("alpine:init", () => {
  Alpine.data("explorer", () => ({
    view: "loading",
    arg: "",
    data: {},
    error: "",
    loading: false,
    query: "",
    agents: {},
    seq: 0,
    filter: "",
    showRetracted: false,
    picked: null,
    hover: null,

    async init() {
      window.addEventListener("hashchange", () => this.route());
      await this.route();
    },

    async route() {
      const [view, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
      const page = view || "overview";
      const arg = decodeURIComponent(rest.join("/"));
      const seq = ++this.seq;
      this.loading = true;
      delete document.body.dataset.state;
      if (page === "search") this.query = arg;
      try {
        const load = LOADERS[page];
        if (!load) throw new Error(`There is no page called ${page}.`);
        const [data, agents] = await Promise.all([load(arg), get("nodes?select=id,name,kind&type=eq.Agent")]);
        if (seq !== this.seq) return;
        if (page !== "search") this.query = "";
        if (this.view === "graph") window.WmkGraph.destroy();
        this.picked = null;
        this.hover = null;
        this.agents = Object.fromEntries(agents.map((a) => [a.id, a]));
        this.data = data;
        this.arg = arg;
        this.filter = "";
        this.view = page;
        this.error = "";
        document.title = `${this.title()} · World Model Kernel`;
        window.scrollTo(0, 0);
      } catch (err) {
        if (seq !== this.seq) return;
        this.error = err.message;
        this.view = "error";
        document.title = "Error · World Model Kernel";
      } finally {
        if (seq === this.seq) this.loading = false;
      }
      // Rendered: ui/smoke.py waits for these.
      await this.$nextTick();
      if (seq === this.seq) {
        if (this.view === "graph") this.drawGraph();
        document.body.dataset.route = location.hash || "#/";
        document.body.dataset.state = this.view === "error" ? "error" : "ready";
      }
    },

    title() {
      const d = this.data;
      return (
        {
          overview: "Overview",
          search: `Search: ${this.arg}`,
          node: d.node?.name,
          edge: d.edge && `${d.edge.from.name} ${edgeName(d.edge)} ${d.edge.to.name}`,
          source: d.source?.title,
          log: "Log",
          evidence: "Evidence",
          ontology: "Ontology",
          graph: d.title,
          models: "Models",
        }[this.view] || "Explorer"
      );
    },

    search() {
      const term = this.query.trim();
      location.hash = term ? `#/search/${enc(term)}` : "#/";
    },

    // Template helpers.
    href,
    day,
    windowOf,
    edgeName,
    score,
    json,
    isEmpty,
    agentName(id) {
      return this.agents[id]?.name || id || "";
    },
    nodeName(id) {
      return this.data.names?.[id]?.name || this.agents[id]?.name || id;
    },
    sourceLabel(key, titles) {
      if (!key) return "";
      if (key.startsWith("agent:")) return `${this.agentName(key.slice(6))} (own observation)`;
      return titles?.[key] || key;
    },
    opLine(op) {
      const n = (id) => this.nodeName(id);
      switch (op.op) {
        case "create":
          return `create ${op.type}${op.kind ? ` (${op.kind})` : ""}: ${op.name ?? op.id}`;
        case "assert":
        case "link":
        case "unlink": {
          const sign = op.polarity === -1 ? "not " : "";
          const valid = windowOf(op);
          return (
            `${op.op} ${n(op.from)} ${sign}${op.kind || op.edge} ${n(op.to)}` +
            `${valid ? ` [${valid}]` : ""}${op.new_edge ? " (new)" : ""}`
          );
        }
        case "promote":
          return `promote ${op.ref || ""} to ${n(op.node_id)}`;
        case "transition":
          return `transition ${n(op.node || op.node_id)} to ${op.status}`;
        case "redact":
          return `redact ${op.claim ? `claim ${op.claim}` : `${(op.fields || []).join(", ")} of ${n(op.node)}`}`;
        default:
          return op.op;
      }
    },
    edgeLine(id) {
      const e = this.data.edges?.[id];
      return e ? `${e.from.name} ${edgeName(e)} ${e.to.name}` : id;
    },
    opTarget(op) {
      return op.edge_id || op.id || op.node_id || op.node || op.claim || "";
    },
    claimsShown() {
      const f = this.filter.toLowerCase();
      return (this.data.claims || []).filter(
        (c) => !f || c.name.toLowerCase().includes(f) || (c.kind || "").includes(f),
      );
    },
    // The graph view.
    drawGraph() {
      const el = document.getElementById("graph");
      if (!el) return;
      const g = Alpine.raw(this.data);
      const edges = g.edges.filter((e) => this.showRetracted || e.belief_status !== "rejected");
      window.WmkGraph.draw(
        el,
        { nodes: g.nodes, edges },
        { center: g.center, onPick: (p) => (this.picked = p), onHover: (h) => (this.hover = h) },
      );
    },
    fitGraph() {
      window.WmkGraph.fit();
    },
    graphNode(id) {
      return this.data.nodes?.find((n) => n.id === id);
    },
    graphEdge(id) {
      return this.data.edges?.find((e) => e.id === id);
    },
    describe(p) {
      if (!p) return "";
      if (p.kind === "node") {
        const n = this.graphNode(p.id);
        return n ? `${n.name} (${n.kind || n.type})` : "";
      }
      const e = this.graphEdge(p.id);
      return e ? `${e.from.name} ${edgeName(e)} ${e.to.name}: ${e.belief_status || "unknown"}` : "";
    },
    retractedCount() {
      return (this.data.edges || []).filter((e) => e.belief_status === "rejected").length;
    },
    countsByType() {
      const c = this.data.counts || {};
      return [
        ["Entities", c.entity],
        ["Agents", c.agent],
        ["Claim nodes", c.claim],
        ["Events", c.event],
        ["Edges", c.edges],
        ["Sources", c.sources],
        ["Claims logged", c.claims],
        ["Unresolved claims", c.unresolved],
      ];
    },
  }));
});
