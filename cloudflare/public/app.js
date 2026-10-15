const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const UTMS = ['utm_source', 'utm_campaign', 'utm_medium', 'utm_content', 'utm_term'];
const PRESETS = ['Today', 'Yesterday', 'This week', 'Last week', 'Last 7 days', 'This month', 'Last month', 'Last 30 days', 'Previous N complete days', 'Custom'];
const state = {me: null, settings: null, fields: [], catalog: {}, reports: [], report: null, reportId: null, tab: null, page: 0, callsLimit: 50, scheduleId: null, editingSpec: null, jobSignature: ''};
function el(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
function notice(message, error = false) { const n = $('#notice'); n.textContent = message; n.className = error ? 'error' : ''; n.hidden = !message; }
function selected(select) { return [...select.selectedOptions].map(o => o.value); }
function options(select, entries, chosen = [], blank = false) {
  select.replaceChildren(); if (blank) select.append(new Option('(not mapped)', ''));
  for (const [value, label] of entries) select.append(new Option(label, value, false, chosen.includes(value)));
}
async function api(path, method = 'GET', value) {
  const response = await fetch(path, {method, credentials: 'same-origin', headers: method === 'GET' ? {} : {'Content-Type': 'application/json', 'X-ASB-Request': '1'}, body: value === undefined ? undefined : JSON.stringify(value), redirect: 'error'});
  if (!response.ok) { let message; try { message = (await response.json()).error; } catch { message = 'Your session may have expired. Reload to sign in again.'; } throw new Error(message || `Request failed (${response.status}).`); }
  return response;
}
async function data(path, method = 'GET', value) { return (await api(path, method, value)).json(); }
function action(element, event, fn) {
  element.addEventListener(event, async e => {
    if (event === 'submit') e.preventDefault();
    const button = event === 'submit' ? e.submitter : element.tagName === 'BUTTON' ? element : null;
    if (button) button.disabled = true;
    try { await fn(e); } catch (error) { notice(error.message, true); }
    finally { if (button) button.disabled = false; }
  });
}
const date = (v, timeZone) => new Date(v).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short', ...(timeZone ? {timeZone} : {})});
const pct = v => v == null ? 'Not configured' : `${v.toFixed(2)}%`;
function table(container, columns, rows) {
  container.replaceChildren();
  if (!rows.length) { container.append(el('p', 'No records in this view.', 'muted')); return; }
  const t = el('table'), head = el('thead'), tr = el('tr');
  for (const c of columns) { const th = el('th', c.label); th.scope = 'col'; tr.append(th); }
  head.append(tr); t.append(head);
  const tbody = el('tbody');
  for (const row of rows) {
    const r = el('tr');
    for (const c of columns) {
      const td = el('td'), value = c.render ? c.render(row) : row[c.key];
      if (value instanceof Node) td.append(value);
      else td.textContent = value == null ? '—' : Array.isArray(value) ? value.join('; ') : String(value);
      r.append(td);
    }
    tbody.append(r);
  }
  t.append(tbody); container.append(t);
}
function showPage(name) {
  if (name !== 'reports' && state.me?.role !== 'admin') return;
  for (const section of $$('main > section')) section.hidden = section.id !== `page-${name}`;
  for (const button of $$('.nav')) button.classList.toggle('active', button.dataset.page === name);
  if (name === 'emails') { updateEmailScope(); loadSchedules().catch(e => notice(e.message, true)); }
}
for (const button of $$('.nav')) button.addEventListener('click', () => showPage(button.dataset.page));
for (const select of $$('.period-select')) {
  options(select, PRESETS.filter(p => select.form.id !== 'schedule-form' || p !== 'Custom').map(p => [p, p]), ['Last week']);
  const update = () => {
    for (const node of select.form.querySelectorAll('[data-custom]')) node.hidden = select.value !== 'Custom';
    for (const node of select.form.querySelectorAll('[data-days]')) node.hidden = select.value !== 'Previous N complete days';
  };
  select.addEventListener('change', update); update();
}
function updateFrequency() { for (const label of $$('[data-frequency]')) label.hidden = !label.dataset.frequency.split(' ').includes($('#schedule-form').elements.frequency.value); }
$('#schedule-form').elements.frequency.addEventListener('change', updateFrequency);

async function loadReports(append = false) {
  const old = state.reportId;
  const response = await data(`/api/reports?offset=${append ? state.reports.length : 0}`);
  state.reports = append ? [...state.reports, ...response.results] : response.results;
  if (old && !state.reports.some(r => r.id === old)) state.reports.unshift({id: old, name: state.report?.name || 'Selected report', created_at: state.report?.meta.fetched_at || new Date().toISOString()});
  options($('#saved-report'), state.reports.map(r => [r.id, `${r.name} · ${date(r.created_at)}${r.people == null ? '' : ` · ${r.people} people`}`]), old ? [old] : []);
  $('#older-reports').hidden = response.results.length < 100;
  if (!state.reports.length) $('#saved-report').append(new Option('No reports yet', ''));
  if (!state.report && state.reports.length) await loadReport(state.reports[0].id);
}
async function loadReport(id) {
  if (!id) return;
  state.report = await data(`/api/reports/${id}`); state.reportId = id; state.tab = null; state.page = 0; state.callsLimit = 50;
  $('#saved-report').value = id; $('#empty-report').hidden = true; $('#report-content').hidden = false;
  const r = state.report;
  $('#report-meta').textContent = `Created ${date(r.meta.fetched_at)} · Cohort ${date(r.meta.start, r.meta.timezone)} → ${date(r.meta.end, r.meta.timezone)} (end exclusive) · ${r.meta.timezone} · Current tags at fetch time`;
  $('#matched-only').checked = false; $('#people-search').value = '';
  options($('#export-groups'), r.summary.map(s => [s.group_key, groupLabel(s.group_key)]));
  renderTabs(); renderGroup(); updateEmailScope();
}
function groupLabel(key) { return JSON.parse(key).map(v => v || '(missing)').join(' / '); }
function renderTabs() {
  const container = $('#utm-tabs'); container.replaceChildren();
  const entries = [[null, 'All UTM codes'], ...state.report.summary.map(s => [s.group_key, groupLabel(s.group_key)])];
  const labels = entries.map(e => e[1]);
  entries.forEach(([key, label], index) => {
    const unique = labels.filter(x => x === label).length > 1 ? `${label} (${index + 1})` : label;
    const b = el('button', unique); b.type = 'button'; b.role = 'tab'; b.id = `utm-tab-${index}`; b.title = key ? state.report.summary.find(s => s.group_key === key).utm_group : label;
    b.setAttribute('aria-selected', String(state.tab === key)); b.setAttribute('aria-controls', 'group-panel'); b.tabIndex = state.tab === key ? 0 : -1;
    b.addEventListener('click', () => {
      const top = window.scrollY;
      state.tab = key; state.page = 0; state.callsLimit = 50;
      for (const tab of container.children) { tab.setAttribute('aria-selected', String(tab === b)); tab.tabIndex = tab === b ? 0 : -1; }
      $('#group-panel').setAttribute('aria-labelledby', b.id);
      renderGroup(); updateEmailScope(); window.scrollTo({top, behavior: 'instant'});
    });
    b.addEventListener('keydown', e => {
      let next;
      if (e.key === 'ArrowRight') next = (index + 1) % entries.length;
      else if (e.key === 'ArrowLeft') next = (index - 1 + entries.length) % entries.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = entries.length - 1;
      if (next !== undefined) { e.preventDefault(); container.children[next].click(); container.children[next].focus({preventScroll: true}); }
    });
    container.append(b);
  });
  $('#group-panel').setAttribute('aria-labelledby', 'utm-tab-0');
}
function cohort() { return state.report.rows.filter(r => state.tab === null || r.group_key === state.tab); }
function renderGroup() {
  const r = state.report; if (!r) return;
  const summary = state.tab === null ? r.total : r.summary.find(s => s.group_key === state.tab);
  $('#group-title').textContent = state.tab === null ? 'All UTM codes' : groupLabel(state.tab);
  $('#export-scope').textContent = state.tab === null ? 'Exports include all UTM codes.' : 'Exports include this tab only.';
  const cards = [
    ['People', summary.people.toLocaleString(), 'People with a UTM code'],
    ['Qualified', pct(summary.qualified_pct), summary.qualified_people == null ? 'Select tags in Settings' : `${summary.qualified_people} of ${summary.people} people`],
    ['Contacted', pct(summary.contacted_pct), summary.contacted_people == null ? 'Select tags in Settings' : `${summary.contacted_people} of ${summary.people} people`],
    ['Matching selected tags', pct(summary.match_pct), `${summary.matching_people} people · ${summary.calls} calls fetched`]
  ];
  $('#stats').replaceChildren(...cards.map(([label, value, detail]) => { const card = el('div', null, 'stat'); card.append(el('div', label, 'label'), el('div', value, 'value'), el('div', detail, 'detail')); return card; }));
  $('#comparison').hidden = state.tab !== null;
  if (state.tab === null) table($('#comparison-table'), [{label: 'UTM code', render: s => groupLabel(s.group_key)}, {label: 'People', key: 'people'}, {label: 'Qualified', render: s => pct(s.qualified_pct)}, {label: 'Contacted', render: s => pct(s.contacted_pct)}, {label: 'Matching tags', render: s => pct(s.match_pct)}], r.summary);
  renderPeople(); renderActivity();
  const ids = new Set(cohort().map(p => p.person_id));
  const issues = r.issues.filter(i => !i.person_id || ids.has(i.person_id));
  $('#report-issues').textContent = issues.length ? `Activity notes: ${issues.map(i => `Person ${i.person_id}: ${i.message}`).join(' · ')}` : '';
}
function detailRows() { return cohort().filter(r => !$('#matched-only').checked || r.matches_tag); }
function renderPeople() {
  if (!state.report) return;
  const query = $('#people-search').value.toLowerCase().trim();
  const rows = detailRows().filter(r => !query || [r.name, ...r.phones, ...r.emails, ...UTMS.map(u => r[u])].join(' ').toLowerCase().includes(query));
  const pages = Math.max(1, Math.ceil(rows.length / 100)); state.page = Math.min(state.page, pages - 1);
  table($('#people-table'), [{label: 'Person', key: 'name'}, {label: 'Phone', key: 'phones'}, {label: 'Email', key: 'emails'},
    ...state.report.options.group_by.map(u => ({label: u, key: u})), {label: 'Tags', key: 'tags'}, {label: 'Matches', render: r => r.matches_tag ? 'Yes' : 'No'},
    {label: 'Calls', key: 'call_count'}, {label: 'Aircall status', key: 'aircall_status'}], rows.slice(state.page * 100, (state.page + 1) * 100));
  $('#people-page').textContent = `Page ${state.page + 1} of ${pages} · ${rows.length} people${query ? ' · Search affects the screen only' : ''}`;
  $('#previous-people').disabled = state.page === 0; $('#next-people').disabled = state.page >= pages - 1;
}
function renderActivity() {
  const ids = new Set(detailRows().map(r => r.person_id)), r = state.report;
  const calls = r.calls.filter(c => ids.has(c.person_id));
  $('#activity-meta').textContent = r.meta.activity ? `${date(r.meta.activity.start)} → ${date(r.meta.activity.end)} · ${calls.length} calls. Transcripts depend on Aircall availability and permissions.` : 'Aircall enrichment was not requested for this report.';
  table($('#activity-table'), [{label: 'Person', key: 'name'}, {label: 'Started', render: c => date(c.started_at)}, {label: 'Direction', key: 'direction'}, {label: 'Seconds', key: 'duration'}, {label: 'Answered', render: c => c.answered ? 'Yes' : 'No'}, {label: 'Agent', key: 'agent'}, {label: 'Transcript', render: c => {
    if (!c.transcript) return c.transcript_status;
    const d = el('details', null, 'activity-details'); d.append(el('summary', 'Read transcript'), el('pre', c.transcript)); return d;
  }}], calls.slice(0, state.callsLimit));
  $('#more-calls').hidden = calls.length <= state.callsLimit;
}
$('#people-search').addEventListener('input', () => { state.page = 0; renderPeople(); });
$('#matched-only').addEventListener('change', () => { state.page = 0; renderGroup(); updateEmailScope(); });
$('#previous-people').addEventListener('click', () => { state.page--; renderPeople(); });
$('#next-people').addEventListener('click', () => { state.page++; renderPeople(); });
$('#more-calls').addEventListener('click', () => { state.callsLimit += 50; renderActivity(); });
action($('#saved-report'), 'change', () => loadReport($('#saved-report').value));
action($('#refresh-reports'), 'click', () => loadReports());
action($('#older-reports'), 'click', () => loadReports(true));
function scope() { return state.tab === null ? null : [state.tab]; }
async function download(format, groups = scope()) {
  if (!state.reportId) throw new Error('Select a report first.');
  if (groups && !groups.length) throw new Error('Select at least one UTM group.');
  const response = await api(`/api/reports/${state.reportId}/export`, 'POST', {format, groups, matched_only: $('#matched-only').checked});
  const url = URL.createObjectURL(await response.blob());
  const a = el('a'); a.href = url;
  const label = groups === null ? 'all-utms' : groups.length === 1 ? groupLabel(groups[0]).replace(/[^a-z0-9_-]/gi, '_').slice(0, 70) : 'selected-utms';
  a.download = `${label}-${format}.${format === 'zip' ? 'zip' : 'csv'}`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
for (const button of $$('[data-export]')) action(button, 'click', () => download(button.dataset.export));
action($('#export-selected'), 'click', () => download('zip', selected($('#export-groups'))));

function fieldCode(f) { return String(f.field_code || f.key || ''); }
function fieldName(f) { return String(f.field_name || f.name || fieldCode(f)); }
function fieldCatalog(code) {
  let f = state.fields.find(f => fieldCode(f) === code);
  if (code === 'label_ids' && !f?.options?.length) f = state.fields.find(f => fieldCode(f) === 'label');
  return Object.fromEntries((f?.options || []).map(o => [`id:${o.id}`, String(o.label)]));
}
function fillTagChoices(keep = false) {
  const config = state.settings, current = keep ? {
    tags: selected($('#settings-tags')), qualified: selected($('#qualified-tags')), contacted: selected($('#contacted-tags'))
  } : {tags: config.tags, ...config.stat_tags};
  state.catalog = fieldCatalog($('#tag-field').value);
  const text = $('#text-tags').value.split('\n').map(t => t.trim()).filter(Boolean);
  for (const t of text) state.catalog[`text:${t.toLowerCase()}`] = t;
  for (const t of [...current.tags, ...current.qualified, ...current.contacted]) if (t.startsWith('text:')) state.catalog[t] = t.slice(5);
  const entries = Object.entries(state.catalog).sort((a, b) => a[1].localeCompare(b[1]));
  options($('#settings-tags'), entries, current.tags); options($('#qualified-tags'), entries, current.qualified); options($('#contacted-tags'), entries, current.contacted);
}
function populateSettings() {
  const s = state.settings, form = $('#settings-form');
  for (const k of ['company', 'timezone', 'sender', 'sendgrid_region']) form.elements[k].value = s[k] || '';
  for (const k of ['pipedrive_key', 'aircall_id', 'aircall_key', 'sendgrid_key']) { form.elements[k].value = ''; $(`[data-secret="${k}"]`).textContent = s.secrets_saved[k] ? 'Saved on server' : 'Not saved'; }
  const entries = state.fields.map(f => [fieldCode(f), fieldName(f)]);
  const mappings = $('#utm-mappings'); mappings.replaceChildren();
  for (const u of UTMS) { const label = el('label', u), select = el('select'); select.id = `map-${u}`; options(select, entries, [s.utm_fields[u] || ''], true); label.append(select); mappings.append(label); }
  options($('#tag-field'), [['label_ids', 'Person labels (native)'], ...entries.filter(([k]) => k !== 'label_ids')], [s.tag_field]);
  options($('#settings-group-by'), UTMS.map(u => [u, u]), s.group_by);
  $('#text-tags').value = [...new Set([...s.tags, ...s.stat_tags.qualified, ...s.stat_tags.contacted].filter(t => t.startsWith('text:')).map(t => t.slice(5)))].join('\n');
  fillTagChoices();
  options($('#report-group-by'), UTMS.map(u => [u, u]), s.group_by);
  options($('#report-tags'), Object.entries(state.catalog), s.tags);
  $('#report-form').elements.tag_rule.value = s.tag_rule;
  $('#report-form').elements.timezone.value = s.timezone; $('#schedule-form').elements.timezone.value = s.timezone;
}
async function loadSettings() {
  const result = await data('/api/admin/settings'); state.settings = result.settings; state.fields = result.fields; state.catalog = result.catalog; populateSettings();
}
function settingsInput() {
  const form = $('#settings-form');
  return {...Object.fromEntries(new FormData(form)), utm_fields: Object.fromEntries(UTMS.map(u => [u, $(`#map-${u}`).value])),
    group_by: selected($('#settings-group-by')), tags: selected($('#settings-tags')), tag_rule: state.settings.tag_rule,
    stat_tags: {qualified: selected($('#qualified-tags')), contacted: selected($('#contacted-tags'))}};
}
async function saveSettings() {
  const result = await data('/api/admin/settings', 'PUT', settingsInput());
  await loadSettings(); notice(result.message); return result;
}
$('#tag-field').addEventListener('change', () => fillTagChoices(false));
$('#add-text-tags').addEventListener('click', () => fillTagChoices(true));
action($('#settings-form'), 'submit', saveSettings);
action($('#connect-pipedrive'), 'click', async () => { await saveSettings(); const result = await data('/api/admin/fields', 'POST', {}); await loadSettings(); notice(result.message); });
action($('#check-sendgrid'), 'click', async () => { await saveSettings(); const result = await data('/api/admin/sendgrid-check', 'POST', {}); notice(result.message, !['validated', 'accepted'].includes(result.status)); });
function chosenReportOptions() { return {group_by: selected($('#report-group-by')), tags: selected($('#report-tags')), tag_rule: $('#report-form').elements.tag_rule.value, stat_tags: state.settings.stat_tags}; }
function formReport(form) {
  const raw = Object.fromEntries(new FormData(form));
  return {...raw, aircall: form.elements.aircall.checked, options: chosenReportOptions()};
}
action($('#report-form'), 'submit', async () => {
  const result = await data('/api/admin/reports', 'POST', {...formReport($('#report-form')), request_id: crypto.randomUUID()});
  notice(result.message); await loadJobs();
});
function updateEmailScope() { $('#email-scope').textContent = state.report ? `${state.report.name} · ${state.tab === null ? 'All UTM codes' : groupLabel(state.tab)} · ${$('#matched-only').checked ? 'matching people only' : 'all people'}. Includes statistics, people, and activity CSV attachments.` : 'Select a saved report first.'; }
action($('#test-form'), 'submit', async () => { const result = await data('/api/admin/test-email', 'POST', {...Object.fromEntries(new FormData($('#test-form'))), request_id: crypto.randomUUID()}); notice(result.message); await loadJobs(); });
action($('#email-form'), 'submit', async () => {
  if (!state.reportId) throw new Error('Select a saved report first.');
  const result = await data('/api/admin/email', 'POST', {...Object.fromEntries(new FormData($('#email-form'))), report_id: state.reportId, groups: scope(), matched_only: $('#matched-only').checked, request_id: crypto.randomUUID()}); notice(result.message); await loadJobs();
});
async function loadJobs() {
  const result = await data('/api/admin/jobs');
  table($('#jobs'), [{label: 'Created', render: j => date(j.created_at)}, {label: 'Type', key: 'kind'}, {label: 'Status', key: 'status'}, {label: 'Details', key: 'message'}, {label: 'Report', render: j => {
    if (!j.report_id) return '—';
    const b = el('button', 'Open', 'secondary'); action(b, 'click', async () => { await loadReport(j.report_id); showPage('reports'); }); return b;
  }}], result.results);
  const signature = result.results.map(j => `${j.id}:${j.status}`).join('|');
  if (state.jobSignature && state.jobSignature !== signature) await loadReports();
  state.jobSignature = signature;
}
function resetSchedule() {
  state.scheduleId = null; state.editingSpec = null; $('#schedule-form').reset();
  $('#schedule-form').elements.preset.value = 'Last week'; $('#schedule-form').elements.timezone.value = state.settings.timezone;
  $('#schedule-form').elements.preset.dispatchEvent(new Event('change')); updateFrequency();
  $('#schedule-title').textContent = 'Create a schedule'; $('#cancel-schedule').hidden = true;
}
function editSchedule(row) {
  state.scheduleId = row.id; state.editingSpec = row.spec;
  const form = $('#schedule-form');
  for (const [key, value] of Object.entries(row.spec)) {
    const input = form.elements[key]; if (!input) continue;
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else if (key === 'recipients') input.value = value.join(', ');
    else if (typeof value !== 'object') input.value = value;
  }
  form.elements.enabled.checked = Boolean(row.enabled); form.elements.scope.value = row.spec.groups === null ? 'all' : 'selected';
  form.elements.preset.dispatchEvent(new Event('change')); updateFrequency();
  $('#schedule-title').textContent = `Edit schedule: ${row.name}`; $('#cancel-schedule').hidden = false;
  notice('Editing keeps this schedule’s saved tag/group options. To use new report options, create a new schedule.');
}
$('#cancel-schedule').addEventListener('click', resetSchedule);
async function loadSchedules() {
  const result = await data('/api/admin/schedules');
  table($('#schedules'), [{label: 'Name', key: 'name'}, {label: 'Status', render: r => r.enabled ? 'Enabled' : 'Paused'}, {label: 'Frequency', render: r => `${r.spec.frequency} · ${r.spec.timezone}`}, {label: 'Next run', render: r => r.enabled ? date(r.next_run, r.spec.timezone) : 'Paused'}, {label: 'Recipients', render: r => r.spec.recipients.join(', ')}, {label: 'Actions', render: r => {
    const group = el('div'), edit = el('button', 'Edit', 'secondary'), toggle = el('button', r.enabled ? 'Pause' : 'Enable', 'secondary'), remove = el('button', 'Delete', 'secondary');
    edit.addEventListener('click', () => editSchedule(r));
    action(toggle, 'click', async () => { await data(`/api/admin/schedules/${r.id}`, 'PUT', {enabled: !r.enabled}); await loadSchedules(); });
    action(remove, 'click', async () => { if (!window.confirm(`Delete schedule “${r.name}”?`)) return; await data(`/api/admin/schedules/${r.id}`, 'DELETE', {}); await loadSchedules(); });
    group.append(edit, toggle, remove); return group;
  }}], result.results);
}
action($('#schedule-form'), 'submit', async () => {
  const form = $('#schedule-form'), input = formReport(form);
  input.enabled = form.elements.enabled.checked; input.matched_only = form.elements.matched_only.checked;
  input.groups = form.elements.scope.value === 'all' ? null : state.editingSpec?.groups || selected($('#export-groups'));
  if (state.editingSpec) { input.options = state.editingSpec.options; input.anchor = state.editingSpec.anchor; }
  const result = await data(state.scheduleId ? `/api/admin/schedules/${state.scheduleId}` : '/api/admin/schedules', state.scheduleId ? 'PUT' : 'POST', input);
  notice(result.message); resetSchedule(); await loadSchedules();
});

async function start() {
  state.me = await data('/api/me'); $('#identity').textContent = `${state.me.email} · ${state.me.role === 'admin' ? 'Admin' : 'User'}`;
  if (state.me.role === 'admin') {
    for (const node of $$('.admin')) if (node.tagName !== 'SECTION') node.hidden = false;
    await loadSettings(); await loadJobs();
    let polling = false;
    setInterval(async () => {
      if (document.hidden || polling) return; polling = true;
      try { await loadJobs(); } catch { /* explicit refresh surfaces session errors */ } finally { polling = false; }
    }, 10000);
  }
  await loadReports();
}
start().catch(error => notice(error.message, true));
