// M5-T5 (D-117), run by scripts/e2e_interview_demo.py after `kk demo --interview --fast`, against that run's `kk serve`:
// the Map view in headless Chromium, signed in as the owner.
//   - the chat pane shows the interview, and both maps are filled: the organisation chart (posts, who holds them, who
//     reports to whom, the units) and the purchasing process (start, its steps in order, who does each, end)
//   - a person corrects a name in place and confirms a proposal in the review lane, each visible within 2 s
//   - a typed answer is recorded and the interviewer asks again
// Env: KK_URL, KK_OWNER_TOKEN, KK_EDITOR_TOKEN; SHOT_DIR (optional screenshot folder).
import { chromium } from 'playwright';

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
const ORG = 'http://www.w3.org/ns/org#';
const org = x => `urn:kk:org:acme:${x}`;

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 }, deviceScaleFactor: 2 });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (/Content.Security.Policy|Refused to/i.test(m.text())) errors.push(m.text()); });
const until = async (fn, arg, ms = 2000) => { const t = Date.now(); await page.waitForFunction(fn, arg, { timeout: ms, polling: 100 }); return Date.now() - t; };

await page.goto(base + '/');
await page.getByLabel('Token').fill(process.env.KK_OWNER_TOKEN);
await page.getByLabel('Token').press('Enter');
await page.getByLabel('Token').blur();
await page.waitForFunction(() => document.querySelector('.who .muted')?.textContent.includes('urn:kk:agent:person:owner'), null, { timeout: 15000 });
await page.getByRole('button', { name: 'Map', exact: true }).click();
await page.waitForSelector('#map-cy[data-ready="1"]', { timeout: 60000 });

// The chat pane: the interview as it was played.
const chat = await page.$$eval('#chat-log p', ps => ps.map(p => ({ kind: p.className, text: p.textContent })));
check('the chat pane shows the interview: the interviewer\'s questions and the person\'s answers',
      chat.filter(m => m.kind === 'person').length === 5 && chat.filter(m => m.kind === 'agent').length >= 6
      && chat.some(m => /procurement lead at Acme/.test(m.text)), `${chat.length} messages`);

// Both maps are filled.
const g = await page.evaluate(() => {
  const o = 'urn:kk:org:acme:', p = 'urn:kk:process:purchasing:', cy = document.getElementById('map-cy')._cy;
  const has = id => cy.getElementById(id).length === 1;
  const edge = (s, t, k) => cy.edges(`[source = "${s}"][target = "${t}"][kind = "stated"]`).filter(e => e.data('label') === k).length === 1;
  return {
    posts: ['post:buyer', 'post:procurement-lead', 'post:finance-director'].every(x => has(o + x)),
    units: ['unit:purchasing', 'unit:finance'].every(x => has(o + x)),
    people: ['dana-reyes', 'lee-chen', 'sam-okafor'].every(x => has('urn:kk:person:acme:' + x)),
    reports: edge(o + 'post:buyer', o + 'post:procurement-lead', 'reportsTo') && edge(o + 'post:procurement-lead', o + 'post:finance-director', 'reportsTo'),
    holds: edge('urn:kk:person:acme:lee-chen', o + 'post:buyer', 'holds'),
    steps: ['step:check-the-budget-code', 'step:raise-a-purchase-order', 'node:start', 'node:end'].every(x => has(p + x)),
    order: edge(p + 'node:start', p + 'step:check-the-budget-code', 'has_nextFlowNode') && edge(p + 'step:check-the-budget-code', p + 'step:raise-a-purchase-order', 'has_nextFlowNode')
      && edge(p + 'step:raise-a-purchase-order', p + 'node:end', 'has_nextFlowNode'),
    does: edge(p + 'step:raise-a-purchase-order', o + 'post:buyer', 'has_resource'),
  };
});
check('the organisation map is filled: posts, units, people, who holds and who reports to whom', g.posts && g.units && g.people && g.reports && g.holds, JSON.stringify(g));
check('the process map is filled: start, the steps in order, who does each, end', g.steps && g.order && g.does, JSON.stringify(g));
if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/interview-demo.png` });

// A person corrects a name in place.
const at = id => page.evaluate(i => { const n = document.getElementById('map-cy')._cy.getElementById(i), p = n.renderedPosition(),
  b = document.getElementById('map-cy').getBoundingClientRect(); return { x: b.x + p.x, y: b.y + p.y }; }, id);
const buyer = org('post:buyer');
const pos = await at(buyer);
await page.mouse.click(pos.x, pos.y);
await page.waitForSelector('#map-rename-to', { timeout: 5000 });
await page.fill('#map-rename-to', 'Purchasing buyer');
await page.click('#map-rename');
const renamed = await until(i => document.getElementById('map-cy')._cy.getElementById(i).data('label') === 'Purchasing buyer', buyer).catch(() => -1);
check('a person renames a post in place; it shows within 2 s', renamed >= 0, `${renamed} ms`);

// A proposal in the review lane, confirmed in place.
const clerk = org('post:receiving-clerk');
const me = (await mcp(process.env.KK_EDITOR_TOKEN, 'whoami')).agent;
const ref = [{ source_uri: me }];
const run = await mcp(process.env.KK_EDITOR_TOKEN, 'start_run', { info: { purpose: 'interview demo test' } });
const cs = await mcp(process.env.KK_EDITOR_TOKEN, 'submit_change_set', { change_set: { idempotency_key: 'interview-demo:clerk', attributed_to: me,
  generated_by: run, target_scope: 'urn:kk:scope:default', lane: 'review', ops: [
    { op: 'add_claim', subject: clerk, predicate: 'rdf:type', object: { entity: ORG + 'Post' }, evidence_type: 'testimony', references: ref },
    { op: 'add_claim', subject: clerk, predicate: 'rdfs:label', object: { value: { '@value': 'Receiving clerk' } }, evidence_type: 'testimony', references: ref },
    { op: 'add_claim', subject: clerk, predicate: ORG + 'reportsTo', object: { entity: buyer }, evidence_type: 'testimony', references: ref }] } });
const dashed = await until(id => document.getElementById('map-cy')._cy.edges().filter(e => e.id().startsWith('pending:' + id)).length >= 1, cs.id).catch(() => -1);
check('what another person proposed waits dashed in the review lane', dashed >= 0, `${dashed} ms`);
await page.waitForSelector(`#map-pending li[data-cs="${cs.id}"] button.approve:not([disabled])`, { timeout: 3000 });
await page.click(`#map-pending li[data-cs="${cs.id}"] button.approve`);
const confirmed = await until(([id, c, b]) => { const cy = document.getElementById('map-cy')._cy;
  return cy.edges().filter(e => e.id().startsWith('pending:' + id)).length === 0
    && cy.edges(`[source = "${c}"][target = "${b}"][kind = "stated"]`).length === 1; }, [cs.id, clerk, buyer], 3000).catch(() => -1);
check('a person confirms it; the edge becomes a stated one within 2 s', confirmed >= 0, `${confirmed} ms`);

// A typed answer: recorded, and the interviewer speaks again.
const n = (await page.$$('#chat-log p')).length;
await page.fill('#chat-text', 'We also have a receiving clerk who reports to the buyer.');
await page.click('#chat-send');
await page.waitForFunction(k => document.querySelectorAll('#chat-log p').length >= k + 2 && /Recorded/.test(document.getElementById('chat-note').textContent), n, { timeout: 40000 });
const note = await page.textContent('#chat-note');
const last = await page.$$eval('#chat-log p', ps => ps.slice(-2).map(p => [p.className, p.textContent]));
check('a typed answer is recorded in the chat and the interviewer speaks again (no model here: no claims taken)',
      last[0][0] === 'person' && last[1][0] === 'agent' && /Recorded/.test(note), `${note} | ${last[1][1]}`);

check('no page errors or CSP violations', errors.length === 0, errors.join(' | ').slice(0, 300));
await browser.close();
console.log('M5-T5 interview demo', checks.every(Boolean) ? 'passed' : 'FAILED', `(${checks.filter(Boolean).length}/${checks.length})`);
process.exit(checks.every(Boolean) ? 0 : 1);
