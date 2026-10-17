// M2-T7, run by scripts/e2e_devops.sh after the MAPE-K run: the Devops view in headless Chromium, signed
// in as the owner, against the compose stack behind Caddy. The services show their latest observations;
// the timeline of the adaptation shows all six stages (observed, analysed, planned, approved, executed,
// observed again) with the claims and the run behind them; the anomaly explains what it was derived from.
// Env: KK_URL, KK_OWNER_TOKEN; SHOT_DIR (optional screenshots).
import { chromium } from 'playwright';

const checks = [];
const check = (name, ok, detail = '') => { checks.push(!!ok); console.log(ok ? 'ok  ' : 'FAIL', name, detail); };
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (/Content.Security.Policy|Refused to/i.test(m.text())) errors.push(m.text()); });

await page.goto(process.env.KK_URL + '/');
await page.getByLabel('Token').fill(process.env.KK_OWNER_TOKEN);
await page.getByLabel('Token').press('Enter');
await page.getByLabel('Token').blur();
await page.waitForFunction(() => document.querySelector('.who .muted')?.textContent.includes('urn:kk:agent:person:owner'), null, { timeout: 15000 });
await page.getByRole('button', { name: 'Devops' }).click();
await page.waitForSelector('#devops-services tr[data-service]', { timeout: 30000 });
const services = await page.$$eval('#devops-services tr[data-service]', rows => rows.map(r => r.dataset.service));
check('M2-T7 the observed services are listed with their latest observations',
      services.includes('urn:kk:node:shop') && services.includes('urn:kk:node:prometheus'), services.join(', '));

await page.waitForFunction(() => [...document.querySelectorAll('ol.timeline')].some(ol =>
  /succeeded/.test((ol.querySelector('li[data-stage="executed"]') || {}).textContent || '')), null, { timeout: 60000 });
const stages = await page.$$eval('ol.timeline', ols => {
  const ol = ols.find(o => /succeeded/.test(o.querySelector('li[data-stage="executed"]').textContent));
  return [...ol.querySelectorAll('li')].map(li => ({ stage: li.dataset.stage, text: li.textContent, links: li.querySelectorAll('a').length }));
});
const by = Object.fromEntries(stages.map(s => [s.stage, s]));
check('M2-T7 the timeline has the six MAPE-K stages in order',
      stages.map(s => s.stage).join(',') === 'observed,analysed,planned,approved,executed,observed again', stages.map(s => s.stage).join(','));
check('M2-T7 every stage cites its evidence: claims, the decision, the run',
      by.observed.links > 0 && by.analysed.links > 0 && by.planned.links > 0 && /decided by person_owner/.test(by.approved.text)
      && /1 → 3 replicas/.test(by.executed.text) && by['observed again'].links > 0,
      stages.map(s => `${s.stage}: ${s.text.replace(/\s+/g, ' ').slice(0, 70)}`).join(' | '));
if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/devops.png`, fullPage: true });
await page.click('ol.timeline li[data-stage="analysed"] a');
await page.waitForFunction(() => /derived_from/.test((document.querySelector('aside.detail pre') || {}).textContent || ''),
                           null, { timeout: 15000 });
const explained = JSON.parse(await page.textContent('aside.detail pre'));
check('M2-T7 the anomaly explains: the observation and the limit it was derived from',
      (explained.derived_from || []).length === 2, `${(explained.derived_from || []).length} premises`);
check('no page errors or CSP violations', errors.length === 0, errors.join(' | ').slice(0, 200));
await browser.close();
console.log('M2-T7 devops UI', checks.every(Boolean) ? 'passed' : 'FAILED', `(${checks.filter(Boolean).length}/${checks.length})`);
process.exit(checks.every(Boolean) ? 0 : 1);
