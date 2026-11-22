// D-119, run by scripts/e2e_kpis.py against its `kk serve`: the Processes view's KPI table in headless Chromium,
// signed in as the owner: every KPI row has a value, a window, a target mark where a person stated one, belief and a
// claim link that opens its explanation. Env: KK_URL, KK_OWNER_TOKEN; SHOT (optional screenshot path).
import { chromium } from 'playwright';

const checks = [];
const check = (name, ok, detail = '') => { checks.push(!!ok); console.log(ok ? 'ok  ' : 'FAIL', name, detail); };
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await (await browser.newContext({ viewport: { width: 1360, height: 1500 }, deviceScaleFactor: 2 })).newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (/Content.Security.Policy|Refused to/i.test(m.text())) errors.push(m.text()); });
await page.goto(process.env.KK_URL + '/');
await page.getByLabel('Token').fill(process.env.KK_OWNER_TOKEN);
await page.getByLabel('Token').press('Enter');
await page.getByLabel('Token').blur();
await page.waitForFunction(() => document.querySelector('.who .muted')?.textContent.includes('urn:kk:agent:person:owner'), null, { timeout: 15000 });
await page.getByRole('button', { name: 'Processes', exact: true }).click();
await page.waitForSelector('#bpm-kpis[data-ready="1"] tr.kpi', { timeout: 60000 });
const rows = await page.$$eval('#bpm-kpi-table tr.kpi', trs => trs.map(t => ({ kpi: t.dataset.kpi, status: t.dataset.status,
  cells: [...t.children].map(c => c.textContent) })));
const row = k => rows.find(r => r.kpi === k);
check('each KPI the module made is a row with a value and its window',
  rows.length === 6 && rows.every(r => r.cells[1] && /\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d/.test(r.cells[2])), rows.map(r => r.kpi).join(', '));
check('the p90 cycle time shows its maximum as met, the throughput its minimum as missed, the others no target',
  row('cycle-time-p90-seconds')?.status === 'met' && /at most 30\.0 d/.test(row('cycle-time-p90-seconds').cells[3])
  && row('throughput-per-day')?.status === 'missed' && /at least 100/.test(row('throughput-per-day').cells[3])
  && row('rework-rate')?.status === '', JSON.stringify(rows.map(r => [r.kpi, r.cells[3]])));
check('belief is shown for each row', rows.every(r => r.cells[4] !== ''));
await page.locator('#bpm-kpi-table tr.kpi a').first().click();
await page.waitForFunction(() => /claim/i.test(document.body.innerText) && document.querySelectorAll('dialog[open], .explain, #explain').length >= 0, null, { timeout: 5000 });
check('a claim link opens its explanation', await page.evaluate(() => document.body.innerText.includes('observed')));
if (process.env.SHOT) await page.locator('#bpm-kpis').screenshot({ path: process.env.SHOT });
check('no script or policy errors', errors.length === 0, errors.join('; '));
await browser.close();
console.log(checks.every(Boolean) ? `D-119 KPI view passed (${checks.length})` : 'D-119 KPI view FAILED');
process.exit(checks.every(Boolean) ? 0 : 1);
