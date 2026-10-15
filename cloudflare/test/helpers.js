import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {DEFAULTS, encrypt, stmt, nowISO} from '../src/storage.js';

export function environment() {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8'));
  function prepare(sql) {
    let params = [];
    return {
      bind(...args) { params = args; return this; },
      async first() { return db.prepare(sql).get(...params) || null; },
      async all() { return {results: db.prepare(sql).all(...params)}; },
      async run() { const result = db.prepare(sql).run(...params); return {meta: {changes: Number(result.changes)}}; }
    };
  }
  const objects = new Map(), workflowCalls = [];
  return {
    DB: {prepare, async batch(statements) { db.exec('BEGIN'); try { const result = []; for (const statement of statements) result.push(await statement.run()); db.exec('COMMIT'); return result; } catch (e) { db.exec('ROLLBACK'); throw e; } }},
    REPORTS: {async put(key, value) { objects.set(key, value); }, async get(key) { if (!objects.has(key)) return null; return {text: async () => objects.get(key), json: async () => JSON.parse(objects.get(key))}; }},
    REPORT_JOB: {async create(args) { if (workflowCalls.some(x => x.id === args.id)) throw new Error('Exists'); workflowCalls.push(args); return {id: args.id}; }, async get(id) { return {async status() { if (!workflowCalls.some(x => x.id === id)) throw new Error('Not found'); return {status: 'running'}; }}; }},
    SETTINGS_ENCRYPTION_KEY: btoa('0123456789abcdef0123456789abcdef'),
    ACCESS_TEAM_DOMAIN: 'asb-test.cloudflareaccess.com', ACCESS_AUD: 'test-audience', ADMIN_EMAIL: 'admin@asb.test', USER_EMAIL: 'user@asb.test', APP_ORIGIN: 'https://reports.asb.test',
    ASSETS: {fetch: async () => new Response('<html>private app</html>')}, objects, workflowCalls, rawDB: db
  };
}
export const schema = [{field_code: 'source', field_name: 'utm_source'}, {field_code: 'campaign', field_name: 'utm_campaign'},
  {field_code: 'label', field_name: 'Label', options: [{id: 1, label: 'Qualified'}, {id: 2, label: 'Contacted'}, {id: 3, label: 'Unqualified'}]}];
export const config = {...structuredClone(DEFAULTS), company: 'asb', pipedrive_key: 'fake-pd-secret', sendgrid_key: 'SG.fake.secret', sender: 'reports@asb.test',
  utm_fields: {utm_source: 'source', utm_campaign: 'campaign'}, tags: ['id:1'], stat_tags: {qualified: ['id:1'], contacted: ['id:2']}};
export const options = Object.fromEntries(['utm_fields', 'tag_field', 'group_by', 'tags', 'tag_rule', 'stat_tags'].map(k => [k, config[k]]));
export const bounds = {start: '2026-10-01T04:00:00.000Z', end: '2026-10-08T04:00:00.000Z', timezone: 'America/New_York'};
export const people = [
  {id: 1, name: 'Mark', add_time: '2026-10-02T12:00:00Z', source: 'AFLT2421', campaign: 'Karmaoholic - 48824', label_ids: [1, 2], phones: [{value: '(212) 555-0100'}]},
  {id: 2, name: 'Unqualified lead', add_time: '2026-10-02T13:00:00Z', source: 'AFLT2421', campaign: 'Karmaoholic - 48824', label_ids: [3]},
  {id: 3, name: 'Other source', add_time: '2026-10-03T12:00:00Z', source: 'OWN', campaign: 'Social', label_ids: [1]},
  {id: 4, name: 'No UTM', add_time: '2026-10-03T12:00:00Z', label_ids: [1]},
  {id: 5, name: 'Outside', add_time: bounds.end, source: 'OWN', label_ids: [1]}
];
export async function seed(env) {
  await stmt(env, 'INSERT INTO settings VALUES (1,?,?)', await encrypt(config, env), nowISO()).run();
  await stmt(env, 'INSERT INTO catalog VALUES (1,?,?)', JSON.stringify(schema), nowISO()).run();
}
export function steps() {
  const cache = new Map();
  return {cache, async do(name, conf, fn) { if (cache.has(name)) return structuredClone(cache.get(name)); const result = await fn(); cache.set(name, result); return structuredClone(result); }, async sleep() {}};
}
