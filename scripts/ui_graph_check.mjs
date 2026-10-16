// Gate G3: the self-model renders as a graph in the UI from live claims.
// Opens the UI through Caddy in headless Chromium, signs in with a token, and checks the Graph tab
// for the whole self-model and for one module; clicking an edge must explain its claim.
// Usage: KK_TOKEN=<jwt> [KK_UI=http://127.0.0.1:8080/] [SHOT=whole.png] [SHOT_MODULE=module.png] \
//          node scripts/ui_graph_check.mjs
import { chromium } from 'playwright';

const ui = process.env.KK_UI || 'http://127.0.0.1:8080/';
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await page.addInitScript(t => sessionStorage.setItem('kk_token', t), process.env.KK_TOKEN);
await page.goto(ui);
await page.getByRole('button', { name: 'Graph' }).click();
await page.waitForFunction(() => +document.getElementById('cy').dataset.nodes > 0, null, { timeout: 30000 });
const whole = await page.$eval('#cy', el => ({ nodes: +el.dataset.nodes, edges: +el.dataset.edges }));
if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT });

await page.selectOption('select[aria-label="Show"]', 'module');
await page.selectOption('select[aria-label="Which"]', 'urn:kk:module:validator-shacl');
await page.waitForFunction(n => +document.getElementById('cy').dataset.nodes !== n, whole.nodes, { timeout: 30000 });
const one = await page.$eval('#cy', el => ({ nodes: +el.dataset.nodes, edges: +el.dataset.edges }));
if (process.env.SHOT_MODULE) await page.screenshot({ path: process.env.SHOT_MODULE });

// Click the first edge's midpoint through Cytoscape's own geometry, then wait for the explain view.
const box = await page.$eval('#cy', el => el.getBoundingClientRect().toJSON());
const mid = await page.evaluate(() => { const e = cy.edges()[0]; return e.renderedMidpoint(); });
await page.mouse.click(box.x + mid.x, box.y + mid.y);
await page.waitForSelector('pre', { timeout: 15000 });
const explained = await page.$eval('pre', el => el.textContent.includes('"references"'));
await browser.close();

console.log(`whole self-model: ${whole.nodes} entities, ${whole.edges} claims; validator-shacl: ${one.nodes} entities, ${one.edges} claims; edge click explains its claim: ${explained}; page errors: ${errors.length}`);
const ok = whole.nodes >= 150 && whole.edges >= 150 && one.nodes >= 5 && one.nodes < 40 && explained && !errors.length;
console.log(`G3 ${ok ? 'passed' : 'FAILED'}${errors.length ? ': ' + errors.join('; ') : ''}`);
process.exit(ok ? 0 : 1);
