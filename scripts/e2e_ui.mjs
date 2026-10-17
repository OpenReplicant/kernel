// T20 and gate G3, run by scripts/smoke_kernel.sh against the running `kk serve`: the UI in headless
// Chromium, signed in as the owner with their own token. The self-model renders as a graph from live
// claims and an edge explains its claim; search finds and explains a claim; a change set an agent
// submitted for review is approved in the browser and applied; the Modules view module loads and
// lists the modules; the BPM view (M2-T4) draws the process map, lists the discrepancies and explains
// one, and the person confirms every discrepancy there after seeing its evidence (M2-G1); no page errors and no Content-Security-Policy violations.
// Env: KK_URL (http://127.0.0.1:<port>), KK_OWNER_TOKEN, KK_AGENT_TOKEN; SHOT_DIR (optional screenshots).
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

// An agent proposes a new property; typing vocabulary goes to the review lane.
const agent = process.env.KK_AGENT_TOKEN;
const run = await mcp(agent, 'start_run', { info: { purpose: 'ui smoke test' } });
const prop = `http://example.org/uiSmokeProperty${Date.now()}`;
const sub = await mcp(agent, 'submit_change_set', { change_set: {
  idempotency_key: `ui-smoke:${prop}`, attributed_to: 'urn:kk:agent:claude-code', generated_by: run,
  target_scope: 'urn:kk:scope:default', ops: [{ op: 'add_claim', subject: prop, predicate: 'rdf:type',
  object: { entity: 'owl:DatatypeProperty' }, evidence_type: 'testimony', references: [{ source_uri: 'file:scripts/e2e_ui.mjs' }] }] } });
for (let i = 0; i < 60; i++) {
  if ((await mcp(agent, 'get_change_set', { change_set_id: sub.id })).status !== 'submitted') break;
  await new Promise(r => setTimeout(r, 500));
}

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (/Content.Security.Policy|Refused to/i.test(m.text())) errors.push(m.text()); });
const shot = async name => { if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${name}.png` }); };

await page.goto(base + '/');
await page.getByLabel('Token').fill(process.env.KK_OWNER_TOKEN);
await page.getByLabel('Token').press('Enter');
await page.getByLabel('Token').blur();
await page.waitForFunction(() => document.querySelector('.who .muted')?.textContent.includes('urn:kk:agent:person:owner'), null, { timeout: 15000 });
check('signs in with the person\'s own token', true);

await page.getByRole('button', { name: 'Graph' }).click();
await page.waitForFunction(() => +document.getElementById('cy').dataset.nodes > 0, null, { timeout: 30000 });
const whole = await page.$eval('#cy', el => ({ nodes: +el.dataset.nodes, edges: +el.dataset.edges }));
check('G3: the self-model renders as a graph from live claims', whole.nodes > 20 && whole.edges > 20, JSON.stringify(whole));
await shot('graph');
await page.selectOption('select[aria-label="Show"]', 'module');
await page.selectOption('select[aria-label="Which"]', 'urn:kk:module:validator-shacl');
await page.waitForFunction(n => +document.getElementById('cy').dataset.nodes !== n, whole.nodes, { timeout: 30000 });
const one = await page.$eval('#cy', el => ({ nodes: +el.dataset.nodes, edges: +el.dataset.edges }));
check('one module\'s neighbourhood', one.nodes > 1 && one.nodes < whole.nodes, JSON.stringify(one));
const box = await page.$eval('#cy', el => el.getBoundingClientRect().toJSON());
const mid = await page.evaluate(() => cy.edges()[0].renderedMidpoint());
await page.mouse.click(box.x + mid.x, box.y + mid.y);
await page.waitForSelector('aside.detail pre:not(:empty)', { timeout: 15000 });
check('clicking an edge explains its claim', (await page.textContent('aside.detail pre')).includes('status'));

await page.getByRole('button', { name: 'Search' }).click();
await page.getByLabel('Search', { exact: true }).fill('validator shapes');
await page.getByRole('button', { name: 'Search', exact: true }).last().click();
await page.waitForSelector('section tr.click', { timeout: 15000 });
const hits = await page.$$eval('section tr.click', rows => rows.length);
await page.click('section tr.click');
await page.waitForSelector('aside.detail pre:not(:empty)', { timeout: 15000 });
check('search finds claims and explains one', hits > 0, `${hits} hits`);

await page.getByRole('button', { name: 'Review' }).click();
const card = `article.card[data-id="${sub.id}"]`;
await page.waitForSelector(card, { timeout: 15000 });
await shot('review');
await page.click(`${card} button.approve`);
await page.waitForSelector(card, { state: 'detached', timeout: 15000 });
const after = await mcp(agent, 'get_change_set', { change_set_id: sub.id });
check('a person approves a review-lane change set in the browser and it is applied', after.status === 'applied', after.status);

await page.getByRole('button', { name: 'Modules' }).click();
await page.waitForSelector('#view tr[data-module]', { timeout: 15000 });
const modules = await page.$$eval('#view tr[data-module]', rows => rows.map(r => r.dataset.module));
check('the Modules view module loads and lists the modules', modules.includes('urn:kk:module:validator-shacl'), `${modules.length} modules`);
await shot('modules');

// M2-T4: the BPM view (after scripts/e2e_bpm.py): the process map renders, documented and observed;
// the discrepancies are listed (one confirmed, the others awaiting review); a confirmed one explains.
await page.getByRole('button', { name: 'Processes' }).click();
await page.waitForFunction(() => Number((document.querySelector('#bpm-map') || {dataset: {}}).dataset.edges) > 0,
                           null, { timeout: 30000 });
const ready = () => page.waitForSelector('#bpm-discrepancies[data-ready="1"]', { timeout: 30000 });
await ready();
const map = await page.$eval('#bpm-map', e => ({ nodes: Number(e.dataset.nodes), edges: Number(e.dataset.edges) }));
check('M2-T4 the process map renders documented and observed flows', map.nodes === 10 && map.edges >= 14,
      `${map.nodes} nodes, ${map.edges} flows`);
const listed = await page.$$eval('#bpm-discrepancies li.discrepancy', ls => ls.map(l => l.dataset.status));
check('M2-T4 the discrepancies are listed, confirmed and awaiting review',
      listed.filter(s => s === 'confirmed').length === 1 && listed.filter(s => s === 'awaiting review').length === 5,
      listed.join(', '));
await shot('processes');
await page.click('#bpm-discrepancies li[data-status="confirmed"] a');
await page.waitForFunction(() => /derived_from/.test((document.querySelector('aside.detail pre') || {}).textContent || ''),
                           null, { timeout: 15000 });
const explained = JSON.parse(await page.textContent('aside.detail pre'));
check('M2-T4 a discrepancy explains: the claims it was derived from', (explained.derived_from || []).length > 0,
      `${(explained.derived_from || []).length} premises`);

// M2-G1: the person reviews every planted discrepancy in the process view, each with its evidence and
// belief, and confirms it; all six end confirmed.
const evidence = await page.$$eval('#bpm-discrepancies li.discrepancy', ls => ls.map(l =>
  [...l.querySelectorAll('.evidence a')].map(a => a.textContent)));
check('M2-G1 every discrepancy shows its evidence with belief',
      evidence.length === 6 && evidence.every(e => e.length > 0 && e.every(t => /belief/.test(t))
        && e.some(t => /^observed/.test(t)) && e.some(t => /^(deterministic|extracted)/.test(t))),
      evidence.map(e => e.length).join(','));
for (let i = 0; i < 5; i++) {
  const before = await page.$$eval('#bpm-discrepancies li[data-status="awaiting review"]', ls => ls.length);
  if (!before) break;
  await page.click('#bpm-discrepancies li[data-status="awaiting review"] button.approve');
  await page.waitForFunction(n => document.querySelector('#bpm-discrepancies').dataset.ready === '1'
    && document.querySelectorAll('#bpm-discrepancies li[data-status="awaiting review"]').length < n, before, { timeout: 30000 });
}
await ready();
const reviewed = await page.$$eval('#bpm-discrepancies li.discrepancy', ls => ls.map(l => l.dataset.status));
check('M2-G1 the person reviews every planted discrepancy in the UI; all six are confirmed',
      reviewed.length === 6 && reviewed.every(s => s === 'confirmed'), reviewed.join(', '));
await shot('processes-reviewed');

check('no page errors or CSP violations', errors.length === 0, errors.join(' | ').slice(0, 200));
await browser.close();
console.log('T20, M2-T4 and M2-G1 UI', checks.every(Boolean) ? 'passed' : 'FAILED', `(${checks.filter(Boolean).length}/${checks.length})`);
process.exit(checks.every(Boolean) ? 0 : 1);
