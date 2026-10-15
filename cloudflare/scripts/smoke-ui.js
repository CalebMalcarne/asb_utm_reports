import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {buildReport, stats, summaries, exportParts} from '../src/core.js';
import {publicSettings} from '../src/storage.js';
import {config, schema, people, options, bounds} from '../test/helpers.js';

const fixture = buildReport([...people, ...Array.from({length: 40}, (_, i) => ({...people[0], id: 100 + i, name: `Lead ${i + 1}`}))], schema, options, bounds, 'Weekly UTM performance');
const report = {...fixture, summary: summaries(fixture), total: stats(fixture.rows, options)};
let role = 'admin', exportRequest = null;
const requests = [];
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost'); requests.push(url.pathname);
    if (url.pathname.startsWith('/api/')) {
      let result;
      if (url.pathname === '/api/me') result = {role, email: `${role}@asb.test`};
      else if (url.pathname === '/api/admin/settings') result = {settings: publicSettings(config), fields: schema};
      else if (url.pathname === '/api/admin/jobs' || url.pathname === '/api/admin/schedules') result = {results: []};
      else if (url.pathname === '/api/reports') result = {results: [{id: 'fixture', name: fixture.name, created_at: fixture.meta.fetched_at, people: fixture.rows.length}]};
      else if (url.pathname === '/api/reports/fixture') result = report;
      else if (url.pathname.endsWith('/export')) {
        let body = ''; for await (const chunk of req) body += chunk;
        exportRequest = JSON.parse(body);
        res.setHeader('Content-Type', 'text/csv'); res.end(exportParts(report, exportRequest.groups, exportRequest.matched_only)['people.csv']); return;
      } else { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(result)); return;
    }
    const path = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    if (!['index.html', 'app.js', 'style.css'].includes(path)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(await readFile(new URL(`../public/${path}`, import.meta.url)));
  } catch (e) { res.writeHead(500); res.end(e.message); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const url = `http://127.0.0.1:${server.address().port}`;
  await page.goto(url); await page.waitForSelector('#report-content:not([hidden])');
  assert.equal(await page.locator('.nav[data-page="settings"]').isVisible(), true);
  assert.equal(await page.locator('#utm-tabs [role="tab"]').count(), 3);
  await page.evaluate(() => window.scrollTo(0, 300));
  const before = await page.evaluate(() => window.scrollY);
  await page.locator('#utm-tabs button').nth(1).click();
  const after = await page.evaluate(() => window.scrollY);
  assert.ok(after > 100 && Math.abs(before - after) < 50, `Scroll jumped from ${before} to ${after}`);
  assert.ok(!(await page.locator('#people-table').textContent()).includes('Other source'));
  await page.locator('[data-export="people"]').click();
  await page.waitForTimeout(200);
  assert.deepEqual(exportRequest.groups, [fixture.rows[0].group_key]);
  await page.locator('#utm-tabs button').first().click();
  await page.locator('[data-export="people"]').click(); await page.waitForTimeout(200);
  assert.equal(exportRequest.groups, null);
  await page.evaluate(() => window.scrollTo(0, 0));
  await mkdir(new URL('../dist/', import.meta.url), {recursive: true});
  await page.screenshot({path: new URL('../dist/reports-desktop.png', import.meta.url).pathname, fullPage: false});
  await page.locator('[data-page="settings"]').click();
  assert.equal(await page.locator('#settings-form').isVisible(), true);
  assert.equal(await page.locator('#settings-form [name="sendgrid_key"]').inputValue(), '');
  assert.equal(await page.locator('#qualified-tags').inputValue(), 'id:1');
  await page.locator('[data-page="emails"]').click();
  assert.equal(await page.locator('#test-form').isVisible(), true);
  assert.equal(await page.locator('#schedule-form [name="enabled"]').isChecked(), false);
  role = 'user'; requests.length = 0;
  await page.goto(url); await page.waitForSelector('#report-content:not([hidden])');
  assert.equal(await page.locator('[data-page="settings"]').isVisible(), false);
  assert.equal(await page.locator('#generate-panel').isVisible(), false);
  assert.ok(!requests.some(p => p.startsWith('/api/admin/')));
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.screenshot({path: new URL('../dist/reports-mobile.png', import.meta.url).pathname, fullPage: false});
  assert.deepEqual(errors, []);
  console.log('UI smoke checks passed: admin/user screens, tab scroll, scoped export, blank secrets, and mobile layout.');
} finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
