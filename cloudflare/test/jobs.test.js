import test from 'node:test';
import assert from 'node:assert/strict';
import {encrypt, decrypt, publicSettings, settings, saveSettings, stmt, readReport, nowISO} from '../src/storage.js';
import {runJob, enqueue, tick} from '../src/jobs.js';
import {sendOnce, send, payload, rejection} from '../src/mail.js';
import {handle} from '../src/router.js';
import {environment, seed, config, schema, people, bounds, options, steps} from './helpers.js';

test('settings are authenticated ciphertext; keys are never returned by settings API', async () => {
  const env = environment(); await seed(env);
  const cipher = (await stmt(env, 'SELECT ciphertext FROM settings').first()).ciphertext;
  assert.ok(!cipher.includes(config.sendgrid_key));
  assert.equal((await decrypt(cipher, env)).sendgrid_key, config.sendgrid_key);
  assert.ok(!Object.hasOwn(publicSettings(config), 'sendgrid_key'));
  assert.equal(publicSettings(config).secrets_saved.sendgrid_key, true);
  await assert.rejects(decrypt(cipher, {...env, SETTINGS_ENCRYPTION_KEY: btoa('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')}));
  const changed = cipher.slice(0, -5) + 'AAAAA'; await assert.rejects(decrypt(changed, env));
  assert.notEqual(await encrypt(config, env), await encrypt(config, env));
});
test('SendGrid explains quota and sender errors, redacts secrets, and performs one POST only', async () => {
  assert.match(rejection(401, JSON.stringify({errors: [{message: 'Maximum credits exceeded'}]}), config), /quota exceeded/);
  assert.match(rejection(403, JSON.stringify({errors: [{message: 'The from address does not match a verified Sender Identity.'}]}), config), /exact verified/);
  assert.ok(!rejection(401, JSON.stringify({errors: [{message: config.sendgrid_key}]}), config).includes(config.sendgrid_key));
  let calls = 0;
  const result = await send(config, payload(config, 'test@asb.test', 'Test', null), async () => { calls++; throw new Error('timeout'); });
  assert.equal(result.status, 'uncertain'); assert.equal(calls, 1);
});
test('persisted mail attempt prevents repeat send on workflow replay, including ambiguous outcomes', async () => {
  const env = environment(); let calls = 0;
  const message = payload(config, 'test@asb.test', 'Test', null);
  const fetcher = async () => { calls++; return new Response(null, {status: 202}); };
  assert.equal((await sendOnce(env, 'mail1', config, message, fetcher)).status, 'accepted');
  assert.equal((await sendOnce(env, 'mail1', config, message, fetcher)).status, 'accepted');
  assert.equal(calls, 1);
  await stmt(env, 'INSERT INTO mail_attempts(job_id,status,updated_at) VALUES (?,?,?)', 'mail2', 'sending', nowISO()).run();
  assert.equal((await sendOnce(env, 'mail2', config, message, fetcher)).status, 'uncertain');
  assert.equal(calls, 1);
});
test('sandbox check uses validation mode; report emails isolate recipient identities', async () => {
  const message = payload(config, ['one@asb.test', 'two@asb.test'], 'Test', null, null, false, true);
  assert.equal(message.mail_settings.sandbox_mode.enable, true);
  assert.equal(message.personalizations.length, 2);
  assert.equal(message.personalizations[0].to.length, 1);
  const result = await send(config, message, async () => new Response(null, {status: 200}));
  assert.equal(result.status, 'validated');
});
test('cron claims a slot once and skips missed backlog; configuration edits pause schedules', async () => {
  const env = environment(); await seed(env);
  const at = new Date('2026-10-15T14:00:00Z');
  const spec = {name: 'Weekly', preset: 'Last week', timezone: 'America/New_York', frequency: 'weekly', weekday: 1, time: '09:00', options, recipients: ['test@asb.test'], groups: null};
  await stmt(env, 'INSERT INTO schedules VALUES (?,?,?,?,?,?,?)', 'weekly', 'Weekly', JSON.stringify(spec), 1, '2026-10-05T13:00:00.000Z', 'revision1', at.toISOString()).run();
  await tick(env, at); await tick(env, at);
  assert.equal((await stmt(env, 'SELECT COUNT(*) AS n FROM jobs').first()).n, 1);
  assert.equal(env.workflowCalls.length, 1);
  const schedule = await stmt(env, 'SELECT * FROM schedules').first();
  assert.equal(schedule.next_run, '2026-10-19T13:00:00.000Z');
  await saveSettings(env, await settings(env));
  assert.equal((await stmt(env, 'SELECT enabled FROM schedules').first()).enabled, 0);
});
test('full workflow fetches every page, persists a private report, and serves scoped user exports', async () => {
  const env = environment(); await seed(env);
  const original = globalThis.fetch; const requests = [];
  globalThis.fetch = async url => {
    const u = new URL(url); requests.push(u.toString());
    if (u.pathname.endsWith('/users/me')) return Response.json({data: {id: 1}});
    if (u.pathname.endsWith('/personFields')) return Response.json({data: schema});
    if (u.pathname.endsWith('/persons')) return Response.json(u.searchParams.get('cursor') ? {data: people.slice(2)} : {data: people.slice(0, 2), additional_data: {next_cursor: 'page2'}});
    throw new Error('Unexpected URL');
  };
  try {
    const id = await enqueue(env, 'report', {name: 'Full test', bounds, options}, config.sender);
    const step = steps();
    assert.equal((await runJob(env, id, step)).status, 'complete');
    const report = await readReport(env, id); assert.equal(report.rows.length, 3);
    assert.equal((await stmt(env, 'SELECT status FROM jobs WHERE id=?', id).first()).status, 'complete');
    assert.equal(requests.filter(s => s.includes('/persons?')).length, 2);
    assert.ok(!JSON.stringify([...step.cache.values()]).includes(config.pipedrive_key));
    assert.ok(!JSON.stringify(report).includes(config.sendgrid_key));
    const user = async () => ({email: env.USER_EMAIL, role: 'user'});
    const get = await handle(new Request(`${env.APP_ORIGIN}/api/reports/${id}`), env, {}, user);
    assert.equal(get.status, 200); assert.equal((await get.json()).total.qualified_pct, 66.67);
    const exported = await handle(new Request(`${env.APP_ORIGIN}/api/reports/${id}/export`, {method: 'POST', headers: {'Origin': env.APP_ORIGIN, 'Content-Type': 'application/json', 'X-ASB-Request': '1'}, body: JSON.stringify({format: 'people', groups: [report.rows[0].group_key]})}), env, {}, user);
    assert.equal(exported.status, 200); assert.ok(!(await exported.text()).includes('Other source'));
    await runJob(env, id, step); // replay uses completed durable steps
    assert.equal(requests.filter(s => s.includes('/persons?')).length, 2);
  } finally { globalThis.fetch = original; }
});
test('schedule paused after report generation is checked again before sending', async () => {
  const env = environment(); await seed(env); let sends = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async url => {
    const u = new URL(url);
    if (u.hostname.includes('sendgrid')) { sends++; return new Response(null, {status: 202}); }
    if (u.pathname.endsWith('/users/me')) return Response.json({data: {id: 1}});
    if (u.pathname.endsWith('/personFields')) return Response.json({data: schema});
    return Response.json({data: people});
  };
  try {
    const spec = {name: 'Scheduled', bounds, options, recipients: ['test@asb.test'], schedule_id: 's', revision: 'r'};
    await stmt(env, 'INSERT INTO schedules VALUES (?,?,?,?,?,?,?)', 's', 'Schedule', '{}', 1, nowISO(), 'r', nowISO()).run();
    const id = await enqueue(env, 'scheduled', spec, 'scheduler');
    const step = steps(), originalDo = step.do.bind(step);
    step.do = async (name, c, fn) => {
      const result = await originalDo(name, c, fn);
      if (name === 'publish report') await stmt(env, 'UPDATE schedules SET enabled=0').run();
      return result;
    };
    assert.equal((await runJob(env, id, step)).status, 'cancelled');
    assert.equal(sends, 0); assert.equal((await readReport(env, id)).rows.length, 3);
  } finally { globalThis.fetch = original; }
});
test('repeated pagination cursor fails without publishing a misleading partial report', async () => {
  const env = environment(); await seed(env);
  const original = globalThis.fetch;
  globalThis.fetch = async url => {
    const path = new URL(url).pathname;
    if (path.endsWith('/users/me')) return Response.json({data: {id: 1}});
    if (path.endsWith('/personFields')) return Response.json({data: schema});
    return Response.json({data: [people[0]], additional_data: {next_cursor: 'same'}});
  };
  try {
    const id = await enqueue(env, 'report', {bounds, options}, 'admin');
    const result = await runJob(env, id, steps());
    assert.equal(result.status, 'failed'); assert.match(result.message, /repeated/);
    assert.equal((await stmt(env, 'SELECT COUNT(*) AS n FROM reports').first()).n, 0);
  } finally { globalThis.fetch = original; }
});
