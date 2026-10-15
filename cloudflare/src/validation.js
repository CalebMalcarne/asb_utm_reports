import {DEFAULTS, SECRET_FIELDS} from './storage.js';
import {UTMS, nextRun, period, validateOptions} from './core.js';
import {companyName, cleanKey} from './providers.js';
import {recipients} from './mail.js';
import {Temporal} from '@js-temporal/polyfill';

const strings = (v, max = 100) => {
  if (!Array.isArray(v) || v.length > max || v.some(s => typeof s !== 'string' || s.length > 300)) throw new Error('Invalid list of report options.');
  return [...new Set(v)];
};
export function reportOptions(config, input = {}) {
  const options = {
    utm_fields: Object.fromEntries(UTMS.map(u => [u, String(config.utm_fields?.[u] || '')])),
    tag_field: config.tag_field,
    group_by: strings(input.group_by ?? config.group_by, 5),
    tags: strings(input.tags ?? config.tags), tag_rule: input.tag_rule ?? config.tag_rule,
    stat_tags: {qualified: strings(input.stat_tags?.qualified ?? config.stat_tags.qualified), contacted: strings(input.stat_tags?.contacted ?? config.stat_tags.contacted)}
  };
  if (!['any', 'all'].includes(options.tag_rule)) throw new Error('Invalid tag matching rule.');
  return options;
}
export function mergeSettings(old, input) {
  const result = {...old};
  for (const key of Object.keys(DEFAULTS)) {
    if (SECRET_FIELDS.includes(key) || !Object.hasOwn(input, key)) continue;
    result[key] = input[key];
  }
  for (const key of SECRET_FIELDS) if (typeof input[key] === 'string' && input[key].trim()) result[key] = cleanKey(input[key]);
  if (input.clear_secrets) for (const key of strings(input.clear_secrets, 4)) {
    if (!SECRET_FIELDS.includes(key)) throw new Error('Unknown secret field.');
    result[key] = '';
  }
  if (result.company) result.company = companyName(result.company);
  Temporal.Now.zonedDateTimeISO(String(result.timezone));
  if (!['global', 'eu'].includes(result.sendgrid_region)) throw new Error('Invalid SendGrid region.');
  if (result.sender && recipients(result.sender).length !== 1) throw new Error('Use one verified sender address.');
  if (typeof result.tag_field !== 'string' || result.tag_field.length > 100) throw new Error('Invalid tag field.');
  Object.assign(result, reportOptions(result));
  return result;
}
export function reportSpec(input, config, fields, now = new Date()) {
  const options = reportOptions(config, input.options);
  validateOptions(options, fields);
  if (!config.company || !config.pipedrive_key) throw new Error('Configure Pipedrive first.');
  if (input.aircall && (!config.aircall_id || !config.aircall_key || !options.tags.length)) throw new Error('Aircall requires saved credentials and at least one matching tag.');
  const spec = {name: String(input.name || 'UTM report').slice(0, 150), preset: input.preset || 'Last week',
    timezone: String(input.timezone || config.timezone), days: Number(input.days || 7),
    start_date: input.start_date, end_date: input.end_date, aircall: Boolean(input.aircall), options};
  return {...spec, bounds: period(spec, now)};
}
export function scheduleSpec(input, config, fields, now = new Date()) {
  const report = reportSpec(input, config, fields, now);
  if (report.preset === 'Custom') throw new Error('Use a relative reporting period for recurring emails.');
  if (!config.sendgrid_key || !config.sender) throw new Error('Configure SendGrid and a verified From address first.');
  const spec = {...report, recipients: recipients(input.recipients), subject: String(input.subject || 'ASB UTM report').slice(0, 180),
    groups: input.groups === null || input.groups === undefined ? null : strings(input.groups, 200),
    matched_only: Boolean(input.matched_only), frequency: input.frequency, time: input.time,
    weekday: Number(input.weekday), day: Number(input.day), hours: Number(input.hours), anchor: input.anchor || now.toISOString()};
  if (spec.groups && !spec.groups.length) throw new Error('Select groups or choose all UTM codes.');
  delete spec.bounds; // Recalculate each occurrence, anchored to its scheduled slot.
  delete spec.start_date; delete spec.end_date;
  return {spec, next_run: nextRun(spec, now)};
}
