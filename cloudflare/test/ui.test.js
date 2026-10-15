import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Window} from 'happy-dom';
import {buildReport, summaries, stats} from '../src/core.js';
import {publicSettings} from '../src/storage.js';
import {config, schema, people, bounds, options} from './helpers.js';

const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8');
const script = await readFile(new URL('../public/app.js', import.meta.url), 'utf8');
const fixture = buildReport(people, schema, options, bounds, 'UI fixture');
const report = {...fixture, summary: summaries(fixture), total: stats(fixture.rows, options)};
async function settle() { for (let i = 0; i < 10; i++) await new Promise(resolve => setTimeout(resolve, 5)); }
async function open(role) {
  const window = new Window({url: 'https://reports.asb.test', settings: {disableJavaScriptFileLoading: true, disableCSSFileLoading: true}});
  // Happy DOM does not expose the browser's Option convenience constructor.
  window.Option = function(text, value, defaultSelected = false, selected = false) {
    const option = window.document.createElement('option'); option.textContent = text; option.value = value;
    option.defaultSelected = defaultSelected; option.selected = selected; return option;
  };
  const calls = [], posts = [];
  window.fetch = async (path, init = {}) => {
    calls.push(path);
    if (init.body) posts.push({path, body: JSON.parse(init.body)});
    let result;
    if (path === '/api/me') result = {email: `${role}@asb.test`, role};
    else if (path === '/api/admin/settings') result = {settings: publicSettings(config), fields: schema};
    else if (path.startsWith('/api/reports?')) result = {results: [{id: 'fixture', name: report.name, created_at: report.meta.fetched_at, people: report.rows.length}]};
    else if (path === '/api/reports/fixture') result = report;
    else result = {results: [], message: 'Queued'};
    return new window.Response(JSON.stringify(result), {headers: {'Content-Type': 'application/json'}});
  };
  window.document.write(html); window.eval(script); await settle();
  return {window, document: window.document, calls, posts};
}
test('admin UI initializes with blank secret fields and populates Pipedrive tags', async () => {
  const {window, document} = await open('admin');
  try {
    assert.equal(document.querySelector('#notice').textContent, '');
    assert.equal(document.querySelector('[data-page="settings"]').hidden, false);
    assert.equal(document.querySelector('#generate-panel').hidden, false);
    assert.equal(document.querySelector('#settings-form [name="sendgrid_key"]').value, '');
    assert.equal(document.querySelector('#qualified-tags').value, 'id:1');
    document.querySelector('[data-page="settings"]').click();
    assert.equal(document.querySelector('#page-settings').hidden, false);
    assert.equal(document.querySelector('#page-reports').hidden, true);
    document.querySelector('[data-page="emails"]').click(); await settle();
    assert.equal(document.querySelector('#page-emails').hidden, false);
    assert.equal(document.querySelector('#schedule-form [name="enabled"]').checked, false);
  } finally { await window.happyDOM.close(); }
});
test('user UI requests reports only; UTM tabs change locally and filter the data sheet', async () => {
  const {window, document, calls} = await open('user');
  try {
    assert.equal(document.querySelector('#notice').textContent, '');
    assert.equal(document.querySelector('[data-page="settings"]').hidden, true);
    assert.equal(document.querySelector('#generate-panel').hidden, true);
    assert.ok(!calls.some(path => path.startsWith('/api/admin/')));
    const tabs = [...document.querySelectorAll('#utm-tabs button')];
    assert.equal(tabs.length, 3);
    assert.match(document.querySelector('#people-table').textContent, /Other source/);
    const before = calls.length; tabs[1].click();
    assert.ok(!document.querySelector('#people-table').textContent.includes('Other source'));
    assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
    assert.equal(calls.length, before);
    document.querySelector('#matched-only').checked = true;
    document.querySelector('#matched-only').dispatchEvent(new window.Event('change'));
    assert.ok(!document.querySelector('#people-table').textContent.includes('Unqualified lead'));
    assert.ok(document.querySelector('#stats').textContent.includes('50.00%'));
  } finally { await window.happyDOM.close(); }
});
