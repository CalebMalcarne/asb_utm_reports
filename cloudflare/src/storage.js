import {HttpError} from './auth.js';

export const DEFAULTS = {
  company: '', pipedrive_key: '', aircall_id: '', aircall_key: '', sendgrid_key: '',
  sender: '', sendgrid_region: 'global', timezone: 'America/New_York',
  utm_fields: {}, tag_field: 'label_ids', group_by: ['utm_source', 'utm_campaign'],
  tags: [], tag_rule: 'any', stat_tags: {qualified: [], contacted: []}
};
export const SECRET_FIELDS = ['pipedrive_key', 'aircall_id', 'aircall_key', 'sendgrid_key'];
export const nowISO = () => new Date().toISOString();
export const stmt = (env, sql, ...args) => env.DB.prepare(sql).bind(...args);
const to64 = bytes => btoa(String.fromCharCode(...bytes));
const from64 = text => Uint8Array.from(atob(text), x => x.charCodeAt(0));
async function encryptionKey(env) {
  let bytes;
  try { bytes = from64(env.SETTINGS_ENCRYPTION_KEY || ''); } catch { /* fail closed below */ }
  if (bytes?.length !== 32) throw new HttpError(503, 'Set the SETTINGS_ENCRYPTION_KEY Worker secret (32 random bytes, base64).');
  return crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
export async function encrypt(value, env) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: new TextEncoder().encode('asb-settings-v1')}, await encryptionKey(env), new TextEncoder().encode(JSON.stringify(value)));
  return `v1.${to64(iv)}.${to64(new Uint8Array(data))}`;
}
export async function decrypt(value, env) {
  const [version, iv, body] = value.split('.');
  if (version !== 'v1') throw new Error('Unknown settings format.');
  const clear = await crypto.subtle.decrypt({name: 'AES-GCM', iv: from64(iv), additionalData: new TextEncoder().encode('asb-settings-v1')}, await encryptionKey(env), from64(body));
  return JSON.parse(new TextDecoder().decode(clear));
}
export async function settings(env) {
  const row = await stmt(env, 'SELECT ciphertext FROM settings WHERE id=1').first();
  return {...structuredClone(DEFAULTS), ...(row ? await decrypt(row.ciphertext, env) : {})};
}
export function publicSettings(config) {
  return {...Object.fromEntries(Object.entries(config).filter(([k]) => !SECRET_FIELDS.includes(k))),
    secrets_saved: Object.fromEntries(SECRET_FIELDS.map(k => [k, Boolean(config[k])]))};
}
export async function saveSettings(env, config, clearCatalog = false) {
  const queries = [stmt(env, 'INSERT INTO settings VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=excluded.updated_at', await encrypt(config, env), nowISO())];
  // Existing schedules were approved with the old credentials/mapping. Require re-enabling them.
  queries.push(stmt(env, 'UPDATE schedules SET enabled=0, revision=?, updated_at=?', crypto.randomUUID(), nowISO()));
  if (clearCatalog) queries.push(stmt(env, 'DELETE FROM catalog'));
  await env.DB.batch(queries);
}
export async function fields(env) {
  const row = await stmt(env, 'SELECT fields FROM catalog WHERE id=1').first();
  return row ? JSON.parse(row.fields) : [];
}
export async function audit(env, actor, action, id = '') {
  await stmt(env, 'INSERT INTO audit(at,actor,action,object_id) VALUES (?,?,?,?)', nowISO(), actor, action, id).run();
}
export async function readReport(env, id) {
  const row = await stmt(env, 'SELECT * FROM reports WHERE id=?', id).first();
  if (!row) throw new HttpError(404, 'Report not found.');
  const obj = await env.REPORTS.get(row.object_key);
  if (!obj) throw new HttpError(404, 'Report data not found.');
  return obj.json();
}
export async function storeReport(env, id, report, actor) {
  const key = `reports/${id}.json`;
  const text = JSON.stringify(report);
  if (new TextEncoder().encode(text).length > 20 * 1024 * 1024) throw new Error('Report exceeds the 20 MB limit. Choose a shorter period.');
  await env.REPORTS.put(key, text, {httpMetadata: {contentType: 'application/json'}});
  await stmt(env, 'INSERT OR IGNORE INTO reports VALUES (?,?,?,?,?,?,?,?)', id, report.name, key,
    report.meta.fetched_at, report.meta.start, report.meta.end, report.rows.length, actor).run();
}
export async function setJob(env, id, status, message = '', reportId = null) {
  await stmt(env, 'UPDATE jobs SET status=?, message=?, report_id=COALESCE(?, report_id), updated_at=? WHERE id=?', status, message, reportId, nowISO(), id).run();
}
