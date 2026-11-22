// D-116 (extends M5-T5), run by scripts/smoke_kernel.sh after the interviewer and the BPM checks, against the
// running `kk serve`: the Map view in headless Chromium, signed in as the owner.
//   - a mined log's observed edges are drawn beside the stated ones (order-to-cash, from e2e_bpm.py)
//   - a scripted conversation (scripts/e2e_map_feed.py: the interaction encoder and the agent's claims) grows the
//     map while the page is open, each change within 2 s of being applied
//   - the agent's proposal waits in the review lane, drawn dashed, and a click confirms it
//   - a person renames a node and removes an edge; the edits are change sets attributed to them
//   - a new relation is looked up in the packs, proposed to the review lane, shown pending, and once a second
//     person confirms it the person's edge with it is added
//   - a node and an edge show their claim id, belief and what they cite
// Env: KK_URL, KK_OWNER_TOKEN, KK_EDITOR_TOKEN (a second person who may review), PYTHON; SHOT_DIR (optional screenshots).
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';

const base = process.env.KK_URL;
const checks = [];
const check = (name, ok, detail = '') => { checks.push(!!ok); console.log(ok ? 'ok  ' : 'FAIL', name, detail); };

async function mcp(token, name, args = {}) {
  const r = await fetch(base + '/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream', Authorization: 'Bearer ' + token },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }) });
  const res = (await r.json()).result;
  if (res.isError) throw new Error(res.content[0].text);
  const sc = res.structuredContent;
  return sc && 'result' in sc ? sc.result : sc ?? JSON.parse(res.content[0].text);
}
const slug = `live${Date.now()}`;
const feed = (...args) => JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['scripts/e2e_map_feed.py', slug, ...args],
  { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim().split('\n').pop());
const post = name => `urn:kk:org:${slug}:post:${name}`;

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2 });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (/Content.Security.Policy|Refused to/i.test(m.text())) errors.push(m.text()); });
const shot = async name => { if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${name}.png` }); };

await page.goto(base + '/');
await page.getByLabel('Token').fill(process.env.KK_OWNER_TOKEN);
await page.getByLabel('Token').press('Enter');
await page.getByLabel('Token').blur();
await page.waitForFunction(() => document.querySelector('.who .muted')?.textContent.includes('urn:kk:agent:person:owner'), null, { timeout: 15000 });
await page.getByRole('button', { name: 'Map', exact: true }).click();
await page.waitForSelector('#map-cy[data-ready="1"]', { timeout: 60000 });
const stat = () => page.$eval('#map-cy', e => ({ ...e.dataset }));
const num = async k => Number((await stat())[k]);
const until = async (fn, arg, ms = 2000) => { const t = Date.now(); await page.waitForFunction(fn, arg, { timeout: ms, polling: 100 }); return Date.now() - t; };

// A mined log: observed edges beside stated ones between the same steps.
const first = await stat();
const both = await page.evaluate(() => {
  const cy = document.getElementById('map-cy')._cy, pairs = {};
  cy.edges().forEach(e => { (pairs[e.source().id() + ' ' + e.target().id()] ||= new Set()).add(e.data('kind')); });
  return Object.values(pairs).filter(s => s.has('observed') && s.has('stated')).length;
});
check('the mined log\'s observed edges are drawn beside the stated ones', Number(first.observed) >= 8 && both >= 3,
      `${first.observed} observed, ${both} steps joined both ways`);
const style = await page.evaluate(() => { const cy = document.getElementById('map-cy')._cy;
  return [cy.edges('.observed')[0].style('line-style'), cy.edges('[kind = "stated"]')[0].style('line-style')]; });
check('observed edges are styled apart from stated ones', style[0] === 'dotted' && style[1] === 'solid', style.join(' / '));

// A conversation grows the map while the page is open.
let nodes = await num('nodes'), ms = [];
for (const n of [0, 1, 2]) {
  feed('answer', String(n));
  const t = await until(k => Number(document.getElementById('map-cy').dataset.nodes) > k, nodes);
  ms.push(t);
  nodes = await num('nodes');
  if (n === 1) await shot('live-map-1');
}
const grown = await page.evaluate(s => { const cy = document.getElementById('map-cy')._cy;
  return ['post:buyer', 'post:procurement-lead', 'post:finance-director'].every(x => cy.getElementById(`urn:kk:org:${s}:${x}`).length)
    && cy.getElementById(`urn:kk:org:${s}:post:buyer`).data('label') === 'Buyer' && cy.edges(`[source = "urn:kk:org:${s}:post:buyer"]`).length >= 2; }, slug);
check('a scripted conversation grows the map while the page is open, each answer within 2 s',
      grown && ms.every(t => t < 2000), `${ms.join(', ')} ms; ${nodes} nodes`);
check('the nodes the conversation added carry the claim id, belief and the quote they cite', await (async () => {
  let d = {};
  for (let i = 0; i < 20 && !/belief\s*[01]\.\d\d/.test(d.text || ''); i++) {   // the belief model scores a moment after the apply
    await page.evaluate(id => { const cy = document.getElementById('map-cy')._cy; cy.getElementById(id).emit('tap'); }, post('buyer'));
    await page.waitForSelector('#map-detail .map-claim', { timeout: 5000 });
    d = await page.$eval('#map-detail', e => ({ id: e.querySelector('.map-claim').dataset.claim, text: e.textContent,
      cites: [...e.querySelectorAll('.map-cites li')].map(l => l.textContent) }));
    if (!/belief\s*[01]\.\d\d/.test(d.text)) await page.waitForTimeout(500);
  }
  return /^[0-9a-f-]{36}$/.test(d.id) && /belief\s*[01]\.\d\d/.test(d.text) && d.cites.some(c => c.includes('urn:kk:message:' + slug) && c.includes('“'));
})());
feed('answer', '3'); feed('answer', '4');
await page.waitForFunction(s => document.getElementById('map-cy')._cy.getElementById(`urn:kk:org:${s}:unit:finance`).length, slug, { timeout: 5000 });
const stated = await num('stated');

// The agent's proposal waits in the review lane, dashed, and the person confirms it in place.
const proposal = feed('proposal');
const dashed = await until(id => document.getElementById('map-cy')._cy.edges().filter(e => e.id().startsWith('pending:' + id)).length >= 1, proposal.id).catch(() => -1);
check('an agent\'s proposal in the review lane is drawn dashed as pending', proposal.status === 'awaiting_review'
  && await page.$eval('#map-cy', e => e._cy.edges('.pending').length >= 1 && e._cy.nodes('.pending').length >= 1), `${dashed} ms`);
await page.waitForSelector(`#map-pending li[data-cs="${proposal.id}"] button.approve:not([disabled])`, { timeout: 3000 });
await shot('live-map-2');
await page.click(`#map-pending li[data-cs="${proposal.id}"] button.approve`);
const confirmed = await until(([s, id]) => { const cy = document.getElementById('map-cy')._cy;   // other proposals may still wait
  return cy.edges().filter(e => e.id().startsWith('pending:' + id)).length === 0 && cy.getElementById(`urn:kk:org:${s}:post:receiving-clerk`).length === 1
    && !cy.getElementById(`urn:kk:org:${s}:post:receiving-clerk`).hasClass('pending'); }, [slug, proposal.id], 3000).catch(() => -1);
check('confirming it makes the edge a stated one', confirmed >= 0 && await num('stated') === stated + 1,
      `${confirmed} ms; ${JSON.stringify(await stat())} ${await page.textContent('#map-status')}`);

// The person edits in place: rename a node.
const at = id => page.evaluate(i => { const n = document.getElementById('map-cy')._cy.getElementById(i), p = n.renderedPosition(),
  b = document.getElementById('map-cy').getBoundingClientRect(); return { x: b.x + p.x, y: b.y + p.y }; }, id);
let p = await at(post('buyer'));
await page.mouse.click(p.x, p.y);
await page.waitForSelector('#map-rename-to', { timeout: 5000 });
await page.fill('#map-rename-to', 'Purchasing buyer');
await page.click('#map-rename');
const renamed = await until(i => document.getElementById('map-cy')._cy.getElementById(i).data('label') === 'Purchasing buyer', post('buyer'))
  .catch(() => -1);
const labels = await mcp(process.env.KK_OWNER_TOKEN, 'query_claims', { subject: post('buyer'), predicate: 'rdfs:label', include_deprecated: true });
const live = labels.filter(c => c.status !== 'deprecated'), old = labels.filter(c => c.status === 'deprecated');
check('a person renames a node; it shows within 2 s as a change set attributed to them',
      renamed >= 0 && live.length === 1 && live[0].attributed_to === 'urn:kk:agent:person:owner' && old.length === 1 && old[0].attributed_to !== live[0].attributed_to,
      `${renamed} ms; ${live.map(c => c.attributed_to)}`);

// A relation the packs have: look up, add (hands off to is new; org:reportsTo is not).
await page.selectOption('#map-from', post('receiving-clerk'));
await page.selectOption('#map-to', post('procurement-lead'));
await page.fill('#map-pred', 'reports to');
const before = await num('stated');
await page.click('#map-add-edge');
const added = await until(k => Number(document.getElementById('map-cy').dataset.stated) > k, before).catch(() => -1);
const rep = await mcp(process.env.KK_OWNER_TOKEN, 'query_claims', { subject: post('receiving-clerk'), predicate: 'http://www.w3.org/ns/org#reportsTo' });
check('a person adds an edge with a relation the packs have; it shows within 2 s, attributed to them',
      added >= 0 && rep.some(c => c.object_entity === post('procurement-lead') && c.attributed_to === 'urn:kk:agent:person:owner'), `${added} ms`);

// Remove it again: select the edge, read its claim, remove.
await page.evaluate(([s, t]) => { const cy = document.getElementById('map-cy')._cy;
  cy.edges(`[source = "${s}"][target = "${t}"][kind = "stated"]`)[0].emit('tap'); }, [post('receiving-clerk'), post('procurement-lead')]);
await page.waitForSelector('#map-remove', { timeout: 5000 });
const edgeClaim = await page.$eval('#map-detail .map-claim', e => ({ id: e.dataset.claim, text: e.textContent }));
const gone = (await (async () => { await page.click('#map-remove');
  return until(([s, t]) => document.getElementById('map-cy')._cy.edges(`[source = "${s}"][target = "${t}"][kind = "stated"]`).length === 0, [post('receiving-clerk'), post('procurement-lead')]).catch(() => -1); })());
check('an edge shows its claim id and belief, and a person removes it (retracted, not deleted)',
      /belief/.test(edgeClaim.text) && gone >= 0 && (await mcp(process.env.KK_OWNER_TOKEN, 'explain', { claim_id: edgeClaim.id })).claim.status === 'deprecated', `${gone} ms`);

// A new relation: looked up first, proposed into the review lane, pending until a second person confirms it.
await page.selectOption('#map-from', post('buyer'));
await page.selectOption('#map-to', post('receiving-clerk'));
await page.fill('#map-pred', 'hands off to');
await page.click('#map-add-edge');
await page.waitForSelector('#map-propose', { timeout: 5000 });
check('a relation not in the loaded packs is looked up first and offered as a new term',
      /not in the loaded packs/.test(await page.textContent('#map-term-msg')));
await page.click('#map-propose');
const term = 'urn:kk:property:hands-off-to';
await page.waitForSelector('#map-pending li[data-cs]:has-text("hands-off-to")', { timeout: 5000 });
const pendingTerm = await page.$eval('#map-pending li:has-text("hands-off-to")', e => ({ cs: e.dataset.cs, disabled: e.querySelector('button.approve').disabled }));
check('the new relation shows pending, and its author cannot confirm it', pendingTerm.disabled);
const hands = (await mcp(process.env.KK_EDITOR_TOKEN, 'review_queue', {})).find(c => c.id === pendingTerm.cs);
const known = await mcp(process.env.KK_OWNER_TOKEN, 'query_claims', { subject: term, predicate: 'rdf:type' });
check('it is in the review lane as typing claims, not yet in the kernel', !!hands && known.length === 0);
const stated2 = await num('stated');
await mcp(process.env.KK_EDITOR_TOKEN, 'review', { change_set_id: pendingTerm.cs, approve: true });
const lifted = await until(i => !document.querySelector(`#map-pending li[data-cs="${i}"]`), pendingTerm.cs, 3000).catch(() => -1);
const withTerm = await until(k => Number(document.getElementById('map-cy').dataset.stated) > k, stated2, 6000).catch(() => -1);
const edge = await mcp(process.env.KK_OWNER_TOKEN, 'query_claims', { subject: post('buyer'), predicate: term });
check('once a second person confirms the term, it is no longer pending and the person\'s edge with it is added',
      lifted >= 0 && withTerm >= 0 && edge.length === 1 && edge[0].object_entity === post('receiving-clerk') && edge[0].attributed_to === 'urn:kk:agent:person:owner',
      `${lifted} ms to leave the list, ${withTerm} ms to the edge`);
await shot('live-map-3');

check('no page errors or CSP violations', errors.length === 0, errors.join(' | ').slice(0, 300));
await browser.close();
console.log('D-116 live map', checks.every(Boolean) ? 'passed' : 'FAILED', `(${checks.filter(Boolean).length}/${checks.length})`);
process.exit(checks.every(Boolean) ? 0 : 1);
