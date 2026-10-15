import {Temporal} from '@js-temporal/polyfill';
import {parsePhoneNumberFromString} from 'libphonenumber-js';
import {zipSync, strToU8} from 'fflate';

export const UTMS = ['utm_source', 'utm_campaign', 'utm_medium', 'utm_content', 'utm_term'];
export const PRESETS = ['Today', 'Yesterday', 'This week', 'Last week', 'Last 7 days', 'This month', 'Last month', 'Last 30 days', 'Previous N complete days', 'Custom'];
export const fieldCode = f => String(f.field_code || f.key || '');
export const fieldName = f => String(f.field_name || f.name || fieldCode(f));
export const fieldValue = (p, code) => p.custom_fields?.[code] ?? p[code];
export function plain(v) {
  if (Array.isArray(v)) return v.map(plain).join('; ');
  if (v && typeof v === 'object') return plain(v.label ?? v.value ?? '');
  const text = String(v ?? '').trim();
  return ['-', 'n/a', 'none', 'null'].includes(text.toLowerCase()) ? '' : text;
}
export function parseDate(value) {
  if (typeof value === 'number') return value * 1000;
  let s = String(value || '').replace(' ', 'T');
  if (!/(Z|[+-]\d\d:\d\d)$/.test(s)) s += 'Z';
  return Date.parse(s);
}
export function phone(v) {
  const p = parsePhoneNumberFromString(String(v ?? ''), 'US');
  return p?.isPossible() ? p.number : '';
}
export function period(spec, now = new Date()) {
  const zone = spec.timezone || 'America/New_York';
  const today = Temporal.Instant.from(now.toISOString()).toZonedDateTimeISO(zone).toPlainDate();
  const monday = today.subtract({days: today.dayOfWeek - 1});
  const first = today.with({day: 1});
  let a, b = today;
  switch (spec.preset) {
    case 'Today': a = today; break;
    case 'Yesterday': a = b = today.subtract({days: 1}); break;
    case 'This week': a = monday; break;
    case 'Last week': a = monday.subtract({days: 7}); b = monday.subtract({days: 1}); break;
    case 'Last 7 days': a = today.subtract({days: 6}); break;
    case 'This month': a = first; break;
    case 'Last month': a = first.subtract({months: 1}); b = first.subtract({days: 1}); break;
    case 'Last 30 days': a = today.subtract({days: 29}); break;
    case 'Previous N complete days': {
      const days = Number(spec.days);
      if (!Number.isInteger(days) || days < 1 || days > 366) throw new Error('Complete days must be between 1 and 366.');
      a = today.subtract({days}); b = today.subtract({days: 1}); break;
    }
    case 'Custom': a = Temporal.PlainDate.from(spec.start_date); b = Temporal.PlainDate.from(spec.end_date); break;
    default: throw new Error('Select a reporting period.');
  }
  if (Temporal.PlainDate.compare(a, b) > 0) throw new Error('Start date must be on or before end date.');
  const start = Number(a.toZonedDateTime(zone).epochMilliseconds);
  const end = Math.min(Number(b.add({days: 1}).toZonedDateTime(zone).epochMilliseconds), now.getTime());
  if (start >= end) throw new Error('Choose a reporting period that has already started.');
  return {start: new Date(start).toISOString(), end: new Date(end).toISOString(), timezone: zone};
}
export function tagEntries(person, code, fields) {
  let field = fields.find(f => fieldCode(f) === code) || {};
  if (code === 'label_ids' && !field.options?.length) field = fields.find(f => fieldCode(f) === 'label') || field;
  const options = Object.fromEntries((field.options || []).map(o => [String(o.id), String(o.label)]));
  let values = code === 'label_ids' ? person.labels || person.label_ids || [] : fieldValue(person, code);
  if (values == null) return {};
  if (!Array.isArray(values)) values = typeof values === 'string' ? values.split(/[,;\n]/) : [values];
  const result = {};
  for (const v of values) {
    let token, label;
    if (v && typeof v === 'object') {
      const id = String(v.id ?? v.value ?? ''); token = `id:${id}`; label = plain(v.label ?? v.name ?? options[id] ?? id);
    } else if (Object.hasOwn(options, String(v).trim()) || code === 'label_ids' || ['enum', 'set'].includes(field.field_type)) {
      const id = String(v).trim(); token = `id:${id}`; label = options[id] || id;
    } else { label = plain(v); token = `text:${label.toLowerCase()}`; }
    if (label) result[token] = label;
  }
  return result;
}
export function catalogFor(fields, code, people = []) {
  let f = fields.find(f => fieldCode(f) === code);
  if (code === 'label_ids' && !f?.options?.length) f = fields.find(f => fieldCode(f) === 'label');
  const result = Object.fromEntries((f?.options || []).map(o => [`id:${o.id}`, String(o.label)]));
  for (const p of people) Object.assign(result, tagEntries(p, code, fields));
  return result;
}
export function validateOptions(config, fields) {
  if (!UTMS.some(u => config.utm_fields?.[u])) throw new Error('Map at least one UTM field in Settings.');
  const codes = new Set(fields.map(fieldCode));
  for (const code of [...Object.values(config.utm_fields), config.tag_field]) {
    if (code && code !== 'label_ids' && !codes.has(code)) throw new Error('A mapped field no longer exists. Refresh Pipedrive fields in Settings.');
  }
  if (!config.group_by?.length || config.group_by.some(u => !UTMS.includes(u))) throw new Error('Select at least one UTM field to group by.');
  const catalog = catalogFor(fields, config.tag_field);
  for (const token of [...config.tags, ...config.stat_tags.qualified, ...config.stat_tags.contacted]) {
    if (!token.startsWith('text:') && !Object.hasOwn(catalog, token)) throw new Error('A selected tag no longer exists. Refresh and select available tags.');
  }
}
export function buildReport(people, fields, options, bounds, name, now = new Date()) {
  const catalog = catalogFor(fields, options.tag_field, people);
  const rows = [], seen = new Set();
  for (const p of people) {
    const id = String(p.id);
    if (seen.has(id)) continue;
    seen.add(id);
    const dt = parseDate(p.add_time);
    if (!Number.isFinite(dt)) throw new Error('A Pipedrive person has an unreadable creation timestamp.');
    if (dt < Date.parse(bounds.start) || dt >= Date.parse(bounds.end)) continue;
    const utms = Object.fromEntries(UTMS.map(u => [u, options.utm_fields[u] ? plain(fieldValue(p, options.utm_fields[u])) : '']));
    if (!Object.values(utms).some(Boolean)) continue;
    const tags = tagEntries(p, options.tag_field, fields), tokens = Object.keys(tags);
    const matches = options.tags.length > 0 && (options.tag_rule === 'all' ? options.tags.every(t => tokens.includes(t)) : options.tags.some(t => tokens.includes(t)));
    const list = value => Array.isArray(value) ? value : value ? [value] : [];
    rows.push({person_id: id, name: p.name || '', created_at: new Date(dt).toISOString(),
      phones: [...new Set(list(p.phones || p.phone).map(x => phone(x?.value ?? x)).filter(Boolean))],
      emails: list(p.emails || p.email).map(x => String(x?.value ?? x)), ...utms,
      tags: Object.values(tags), tag_tokens: tokens, matches_tag: matches,
      matched_tags: options.tags.filter(t => tokens.includes(t)).map(t => tags[t]),
      group_key: JSON.stringify(options.group_by.map(u => utms[u])),
      utm_group: options.group_by.map(u => `${u}=${utms[u] || '(missing)'}`).join(' / '),
      call_count: null, answered_calls: null, transcripts_available: null, aircall_status: 'not requested'});
  }
  return {name, meta: {...bounds, fetched_at: now.toISOString(), cohort: 'Pipedrive person creation time; tag values at fetch time'}, options, catalog, rows, calls: [], issues: []};
}
export function stats(rows, options, groupKey = null) {
  const out = {group_key: groupKey, utm_group: groupKey == null ? 'All UTM codes' : rows[0]?.utm_group || '', people: rows.length,
    matching_people: rows.filter(r => r.matches_tag).length,
    aircall_people_fetched: rows.filter(r => r.aircall_status === 'complete').length,
    calls: rows.reduce((n, r) => n + (r.call_count || 0), 0)};
  out.match_pct = rows.length ? +(100 * out.matching_people / rows.length).toFixed(2) : 0;
  for (const name of ['qualified', 'contacted']) {
    const tokens = options.stat_tags[name];
    const count = tokens.length ? rows.filter(r => tokens.some(t => r.tag_tokens.includes(t))).length : null;
    out[`${name}_people`] = count;
    out[`${name}_pct`] = count == null ? null : rows.length ? +(100 * count / rows.length).toFixed(2) : 0;
  }
  return out;
}
export function summaries(report) {
  const groups = new Map();
  for (const r of report.rows) {
    if (!groups.has(r.group_key)) groups.set(r.group_key, []);
    groups.get(r.group_key).push(r);
  }
  return [...groups].map(([key, rows]) => stats(rows, report.options, key)).sort((a, b) => a.utm_group.localeCompare(b.utm_group));
}
export function csv(rows, emptyColumns = ['person_id', 'name']) {
  const columns = rows.length ? [...new Set(rows.flatMap(Object.keys))] : emptyColumns;
  function cell(v) {
    let s = v == null ? '' : Array.isArray(v) ? v.join('; ') : typeof v === 'object' ? JSON.stringify(v) : String(v);
    if (/^[\s]*[=+@-]/.test(s) || /^[\t\r\n]/.test(s)) s = "'" + s;
    return `"${s.replaceAll('"', '""')}"`;
  }
  return '\uFEFF' + [columns.map(cell).join(','), ...rows.map(r => columns.map(k => cell(r[k])).join(','))].join('\r\n') + '\r\n';
}
export function exportParts(report, groups = null, matchedOnly = false) {
  if (groups !== null && (!Array.isArray(groups) || !groups.length)) throw new Error('Select at least one UTM group.');
  if (groups?.some(g => typeof g !== 'string' || g.length > 3000)) throw new Error('Invalid UTM group selection.');
  const cohort = report.rows.filter(r => groups === null || groups.includes(r.group_key));
  const rows = cohort.filter(r => !matchedOnly || r.matches_tag), ids = new Set(rows.map(r => r.person_id));
  const scoped = {...report, rows: cohort};
  const summary = summaries(scoped), byKey = new Map(summary.map(s => [s.group_key, s]));
  const cleanStats = s => { const {group_key, ...rest} = s; return rest; };
  const people = rows.map(({group_key, tag_tokens, ...r}) => {
    const s = byKey.get(group_key);
    return {...r, group_people: s.people, group_qualified_people: s.qualified_people, group_qualified_pct: s.qualified_pct,
      group_contacted_people: s.contacted_people, group_contacted_pct: s.contacted_pct};
  });
  const totals = stats(cohort, report.options);
  if (groups !== null) totals.utm_group = groups.length === 1 ? summary[0]?.utm_group || 'Selected UTM code (no people in this period)' : 'Selected UTM codes';
  const summaryRows = [{row_type: 'total', ...cleanStats(totals)}, ...summary.map(s => ({row_type: 'utm_group', ...cleanStats(s)}))];
  return {
    'people.csv': csv(people), 'summary.csv': csv(summaryRows),
    'activity.csv': csv(report.calls.filter(c => ids.has(c.person_id)), ['person_id', 'call_id', 'transcript_status', 'transcript']),
    'report_settings.json': JSON.stringify({meta: report.meta, options: report.options, groups, matchedOnly,
      denominator: 'All people with UTMs in the selected group(s), regardless of the detail filter.',
      issues: report.issues.filter(i => !i.person_id || ids.has(i.person_id))}, null, 2)
  };
}
export function exportZip(parts) { return zipSync(Object.fromEntries(Object.entries(parts).map(([k, v]) => [k, strToU8(v)]))); }

export function nextRun(spec, after = new Date()) {
  const instant = Temporal.Instant.from(after.toISOString());
  const zone = spec.timezone || 'America/New_York';
  const local = instant.toZonedDateTimeISO(zone);
  if (spec.frequency === 'interval') {
    const hours = Number(spec.hours);
    if (!Number.isInteger(hours) || hours < 1 || hours > 8760) throw new Error('Interval must be 1–8760 hours.');
    const anchor = Date.parse(spec.anchor || after.toISOString());
    if (!Number.isFinite(anchor)) throw new Error('Invalid schedule anchor.');
    return new Date(anchor + Math.max(1, Math.floor((after.getTime() - anchor) / (hours * 3600000)) + 1) * hours * 3600000).toISOString();
  }
  if (!['daily', 'weekly', 'monthly'].includes(spec.frequency)) throw new Error('Choose daily, weekly, monthly, or interval.');
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(spec.time || '')) throw new Error('Enter a schedule time as HH:MM.');
  const weekday = Number(spec.weekday), day = Number(spec.day);
  if (spec.frequency === 'weekly' && (!Number.isInteger(weekday) || weekday < 1 || weekday > 7)) throw new Error('Choose a weekday (Monday = 1).');
  if (spec.frequency === 'monthly' && (!Number.isInteger(day) || day < 1 || day > 31)) throw new Error('Choose a month day from 1 to 31.');
  for (let offset = 0; offset < 370; offset++) {
    const date = local.toPlainDate().add({days: offset});
    if (spec.frequency === 'weekly' && date.dayOfWeek !== weekday) continue;
    if (spec.frequency === 'monthly' && date.day !== Math.min(day, date.daysInMonth)) continue;
    // Temporal compatible disambiguation shifts spring gaps forward; uses the first fall occurrence.
    const candidate = date.toPlainDateTime(spec.time).toZonedDateTime(zone, {disambiguation: 'compatible'});
    if (Temporal.Instant.compare(candidate.toInstant(), instant) > 0) return new Date(Number(candidate.epochMilliseconds)).toISOString();
  }
  throw new Error('Could not find the next schedule time.');
}
