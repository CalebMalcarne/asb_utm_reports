import {Temporal} from '@js-temporal/polyfill';
import {parseDate, phone, plain} from './core.js';

export function companyName(value) {
  let s = String(value || '').trim().toLowerCase();
  if (s.startsWith('https://')) {
    const url = new URL(s);
    if (url.port || url.username || url.password) throw new Error('Use your Pipedrive company subdomain.');
    s = url.hostname;
  }
  s = s.replace(/\.pipedrive\.com\/?$/, '');
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(s)) throw new Error('Enter a Pipedrive company subdomain, such as your-company.');
  return s;
}
export function cleanKey(value) {
  let s = String(value || '').trim();
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) s = s.slice(1, -1).trim();
  s = s.replace(/^Bearer\s+/i, '');
  if (s && /[^\x21-\x7e]|["']/.test(s)) throw new Error('Paste only the API secret, without whitespace or quotes.');
  return s;
}
export function redact(message, config = {}) {
  let s = String(message);
  for (const key of ['pipedrive_key', 'aircall_id', 'aircall_key', 'sendgrid_key']) {
    if (config[key]) s = s.replaceAll(config[key], '[redacted]');
  }
  return s.replace(/SG\.[\w-]+\.[\w-]+/g, '[redacted]').replace(/Bearer\s+\S+/gi, 'Bearer [redacted]').slice(0, 1500);
}
export class ProviderError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
export async function getJSON(url, headers, label, fetcher = fetch) {
  let response;
  try { response = await fetcher(url, {headers, redirect: 'error', signal: AbortSignal.timeout(60000)}); }
  catch { throw new ProviderError(`${label}: network request failed.`, 503); }
  if (!response.ok) {
    const extra = response.status === 401 ? ' Check the saved credentials and account access.' : response.status === 429 ? ' Rate limited; the background job will retry this read.' : '';
    throw new ProviderError(`${label} HTTP ${response.status}.${extra}`, response.status);
  }
  return response.json();
}
export async function pipedrive(config, path, params = {}, version = 'v2') {
  if (!config.pipedrive_key) throw new Error('Save a Pipedrive personal API token in Settings.');
  const url = new URL(`https://${companyName(config.company)}.pipedrive.com/api/${version}/${path}`);
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  const result = await getJSON(url, {'x-api-token': config.pipedrive_key}, `Pipedrive ${path}`);
  if (result.success === false) throw new Error(`Pipedrive ${path} returned an unsuccessful response.`);
  return result;
}
export async function fetchFields(config) {
  await pipedrive(config, 'users/me', {}, 'v1');
  const result = [], seen = new Set();
  let cursor;
  for (let page = 0; page < 20; page++) {
    const data = await pipedrive(config, 'personFields', {limit: 500, cursor});
    if (!Array.isArray(data.data)) throw new Error('Unexpected Pipedrive personFields response.');
    result.push(...data.data);
    cursor = data.additional_data?.next_cursor;
    if (!cursor) return result;
    if (seen.has(cursor)) throw new Error('Pipedrive repeated a fields cursor.');
    seen.add(cursor);
    await new Promise(resolve => setTimeout(resolve, 350));
  }
  throw new Error('Pipedrive fields exceed the supported limit.');
}
export async function personPage(config, options, bounds, cursor) {
  const codes = [...new Set([...Object.values(options.utm_fields), options.tag_field].filter(c => /^[0-9a-f]{40}$/.test(c)))];
  if (codes.length > 15) throw new Error('Choose at most 15 custom Pipedrive fields.');
  const data = await pipedrive(config, 'persons', {limit: 500, cursor, sort_by: 'add_time', sort_direction: 'desc',
    include_labels: 'true', include_option_labels: 'true', custom_fields: codes.join(',')});
  if (!Array.isArray(data.data)) throw new Error('Unexpected Pipedrive persons response.');
  let crossed = false;
  const people = data.data.filter(p => {
    const dt = parseDate(p.add_time);
    if (!Number.isFinite(dt)) throw new Error('A Pipedrive person has an unreadable creation timestamp.');
    if (dt < Date.parse(bounds.start)) crossed = true;
    return dt >= Date.parse(bounds.start) && dt < Date.parse(bounds.end);
  });
  return {people, cursor: crossed ? null : data.additional_data?.next_cursor || null};
}
const pause = () => new Promise(resolve => setTimeout(resolve, 1100));
async function aircall(config, path, params = {}) {
  if (!config.aircall_id || !config.aircall_key) throw new Error('Save the Aircall API ID and token.');
  const url = new URL(`https://api.aircall.io/v1/${path}`);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  await pause();
  return getJSON(url, {Authorization: `Basic ${btoa(config.aircall_id + ':' + config.aircall_key)}`}, `Aircall ${path}`);
}
export function activityBounds(report, end = new Date()) {
  const start = Date.parse(report.meta.start);
  const earliest = Number(Temporal.Instant.from(end.toISOString()).toZonedDateTimeISO('UTC').subtract({months: 6}).epochMilliseconds);
  if (start < earliest) throw new Error('Aircall search supports the most recent six months. Choose a newer cohort for call enrichment.');
  return {start: new Date(start).toISOString(), end: end.toISOString()};
}
export function activityTargets(report) {
  const owners = new Map();
  for (const r of report.rows) for (const p of r.phones) {
    if (!owners.has(p)) owners.set(p, new Set());
    owners.get(p).add(r.person_id);
  }
  const selected = report.rows.filter(r => r.matches_tag);
  if (selected.length > 500) throw new Error('Aircall enrichment supports up to 500 matching people per report. Narrow the period or selected tags.');
  return selected.map(r => ({id: r.person_id, name: r.name, group_key: r.group_key,
    phones: r.phones.filter(p => owners.get(p).size === 1), shared: r.phones.some(p => owners.get(p).size > 1)}));
}
export async function personActivity(config, target, bounds) {
  const calls = new Map();
  const lo = Math.floor(Date.parse(bounds.start) / 1000), hi = Math.ceil(Date.parse(bounds.end) / 1000) - 1;
  for (const number of target.phones) {
    for (let page = 1; page <= 5; page++) {
      const data = await aircall(config, 'calls/search', {phone_number: number, from: lo, to: hi, page, per_page: 50, order: 'asc'});
      if (Number(data.meta?.total || 0) > 200) throw new Error('Over 200 calls for a person. Choose a shorter activity window.');
      if (!Array.isArray(data.calls)) throw new Error('Unexpected Aircall response.');
      for (const c of data.calls) {
        if (phone(c.raw_digits) === number && c.started_at * 1000 >= Date.parse(bounds.start) && c.started_at * 1000 < Date.parse(bounds.end)) calls.set(String(c.id), c);
      }
      if (calls.size > 200) throw new Error('Over 200 calls for a person. Choose a shorter activity window.');
      if (!data.meta?.next_page_link) break;
      if (page === 5) throw new Error('Aircall call limit reached. Narrow the activity window.');
    }
  }
  const result = [];
  for (const c of calls.values()) {
    let transcript = '', status = 'unavailable';
    try {
      const data = await aircall(config, `calls/${Number(c.id)}/transcription`, {mode: 'async'});
      const obj = data.transcription || data;
      transcript = (obj.content?.utterances || []).map(u => `[${u.start_time ?? u.timestamp ?? ''}] ${u.participant_type || 'unknown'}: ${u.text || ''}`).join('\n');
      status = transcript ? 'available' : 'empty or pending';
    } catch (e) {
      if (![403, 404].includes(e.status)) throw e;
      status = e.status === 403 ? 'forbidden' : 'unavailable';
    }
    if (transcript.length > 100000) throw new Error('A transcript exceeds the 100,000 character report limit.');
    result.push({person_id: target.id, name: target.name, call_id: String(c.id), phone: phone(c.raw_digits),
      started_at: new Date(c.started_at * 1000).toISOString(), direction: c.direction || '', duration: c.duration ?? 0,
      answered: Boolean(c.answered_at), agent: c.user?.name || '', tags: (c.tags || []).map(t => t.name || t.label || plain(t)),
      comments: (c.comments || []).map(x => x.content || '').join('\n'), transcript_status: status, transcript});
  }
  return {person_id: target.id, calls: result, status: target.shared ? 'partial: shared phone skipped' : !target.phones.length ? 'no valid phone' : 'complete'};
}
