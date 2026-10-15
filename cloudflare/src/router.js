import {authenticate, requireAdmin, checkMutation, secureResponse, HttpError} from './auth.js';
import {settings, publicSettings, saveSettings, fields, stmt, audit, nowISO, readReport} from './storage.js';
import {catalogFor, fieldCode, fieldName, UTMS, summaries, stats, exportParts, exportZip} from './core.js';
import {fetchFields, redact} from './providers.js';
import {mergeSettings, reportSpec, scheduleSpec} from './validation.js';
import {payload, send, recipients} from './mail.js';
import {enqueue, dispatch} from './jobs.js';

const json = (value, status = 200) => Response.json(value, {status});
async function body(request) {
  if (Number(request.headers.get('Content-Length') || 0) > 64000) throw new HttpError(413, 'Request is too large.');
  const reader = request.body?.getReader();
  if (!reader) return {};
  let size = 0; const chunks = [];
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 64000) { await reader.cancel(); throw new HttpError(413, 'Request is too large.'); }
    chunks.push(value);
  }
  const all = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.length; }
  try {
    const value = JSON.parse(new TextDecoder().decode(all));
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error();
    return value;
  } catch { throw new HttpError(400, 'Invalid JSON request.'); }
}
function manualID(input) {
  if (!/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(input.request_id || '')) throw new HttpError(400, 'Missing request ID. Reload and try again.');
  return `m-${input.request_id}`;
}
export async function route(request, env, ctx, identity) {
  const url = new URL(request.url), path = url.pathname;
  if (path === '/api/me' && request.method === 'GET') return json(identity);
  if (path === '/api/reports' && request.method === 'GET') {
    const offset = Math.max(0, Math.min(1000000, Number(url.searchParams.get('offset')) || 0));
    return json(await stmt(env, 'SELECT id,name,created_at,start_at,end_at,people FROM reports ORDER BY created_at DESC LIMIT 100 OFFSET ?', offset).all());
  }
  const reportMatch = path.match(/^\/api\/reports\/([\w-]+)(\/export)?$/);
  if (reportMatch) {
    if (!reportMatch[2] && request.method === 'GET') {
      const report = await readReport(env, reportMatch[1]);
      return json({...report, summary: summaries(report), total: stats(report.rows, report.options)});
    }
    if (reportMatch[2] && request.method === 'POST') {
      const input = await body(request), report = await readReport(env, reportMatch[1]);
      const parts = exportParts(report, input.groups ?? null, Boolean(input.matched_only));
      let data, filename, type;
      if (['people', 'summary', 'activity'].includes(input.format)) { filename = `${input.format}.csv`; data = parts[filename]; type = 'text/csv; charset=utf-8'; }
      else if (input.format === 'zip') { data = exportZip(parts); filename = 'utm-report.zip'; type = 'application/zip'; }
      else throw new HttpError(400, 'Select a CSV or ZIP export.');
      return new Response(data, {headers: {'Content-Type': type, 'Content-Disposition': `attachment; filename="${filename}"`}});
    }
    throw new HttpError(405, 'Method not allowed.');
  }
  if (path.startsWith('/api/admin/')) {
    // Every admin endpoint, including reads of settings and jobs, passes this guard first.
    requireAdmin(identity);
    if (path === '/api/admin/settings' && request.method === 'GET') {
      const config = await settings(env), schema = await fields(env);
      return json({settings: publicSettings(config), fields: schema, catalog: catalogFor(schema, config.tag_field)});
    }
    if (path === '/api/admin/settings' && request.method === 'PUT') {
      const old = await settings(env), config = mergeSettings(old, await body(request));
      await saveSettings(env, config, config.company !== old.company || config.pipedrive_key !== old.pipedrive_key);
      await audit(env, identity.email, 'settings.update');
      return json({settings: publicSettings(config), message: 'Settings saved. Existing schedules were paused; review and re-enable them.'});
    }
    if (path === '/api/admin/fields' && request.method === 'POST') {
      const config = await settings(env), schema = await fetchFields(config);
      for (const u of UTMS) {
        const matches = schema.filter(f => fieldName(f).trim().toLowerCase() === u);
        if (!config.utm_fields[u] && matches.length === 1) config.utm_fields[u] = fieldCode(matches[0]);
      }
      const catalog = catalogFor(schema, config.tag_field);
      for (const name of ['qualified', 'contacted']) if (!config.stat_tags[name].length) config.stat_tags[name] = Object.keys(catalog).filter(k => catalog[k].trim().toLowerCase() === name);
      if (!config.tags.length) config.tags = [...config.stat_tags.qualified];
      await saveSettings(env, config);
      await stmt(env, 'INSERT INTO catalog VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET fields=excluded.fields,updated_at=excluded.updated_at', JSON.stringify(schema), nowISO()).run();
      await audit(env, identity.email, 'pipedrive.refresh-fields');
      return json({settings: publicSettings(config), fields: schema, catalog, message: 'Pipedrive connected. Fields loaded; review mappings and paused schedules.'});
    }
    if (path === '/api/admin/sendgrid-check' && request.method === 'POST') {
      const config = await settings(env);
      return json(await send(config, payload(config, config.sender, 'Connection validation', null, null, false, true)));
    }
    if (path === '/api/admin/jobs' && request.method === 'GET') return json(await stmt(env, 'SELECT id,kind,status,message,report_id,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT 50').all());
    if (path === '/api/admin/reports' && request.method === 'POST') {
      const input = await body(request), config = await settings(env), schema = await fields(env);
      const spec = reportSpec(input, config, schema);
      const id = await enqueue(env, 'report', spec, identity.email, manualID(input));
      await audit(env, identity.email, 'report.create', id);
      ctx.waitUntil(dispatch(env));
      return json({id, message: 'Report queued. You can close this page while it runs.'}, 202);
    }
    if (['/api/admin/test-email', '/api/admin/email'].includes(path) && request.method === 'POST') {
      const input = await body(request), config = await settings(env);
      const to = recipients(input.recipients);
      const test = path.endsWith('/test-email');
      if (test && to.length !== 1) throw new HttpError(400, 'Enter one test email address.');
      const report = test ? null : await readReport(env, String(input.report_id));
      payload(config, to, input.subject, report, input.groups ?? null, Boolean(input.matched_only));
      const id = await enqueue(env, test ? 'test' : 'email', {recipients: to, subject: input.subject || (test ? 'ASB email test' : report.name),
        report_id: test ? null : input.report_id, groups: input.groups ?? null, matched_only: Boolean(input.matched_only)}, identity.email, manualID(input));
      await audit(env, identity.email, test ? 'email.test' : 'email.report', id);
      ctx.waitUntil(dispatch(env));
      return json({id, message: 'Email queued. Check the job status for the SendGrid response.'}, 202);
    }
    if (path === '/api/admin/schedules' && request.method === 'GET') {
      const data = await stmt(env, 'SELECT * FROM schedules ORDER BY updated_at DESC').all();
      return json({results: data.results.map(r => ({...r, spec: JSON.parse(r.spec)}))});
    }
    if (path === '/api/admin/schedules' && request.method === 'POST') {
      const input = await body(request), config = await settings(env), schema = await fields(env);
      const {spec, next_run} = scheduleSpec(input, config, schema);
      const id = crypto.randomUUID();
      await stmt(env, 'INSERT INTO schedules VALUES (?,?,?,?,?,?,?)', id, spec.name, JSON.stringify(spec), input.enabled ? 1 : 0, next_run, crypto.randomUUID(), nowISO()).run();
      await audit(env, identity.email, 'schedule.create', id);
      return json({id, next_run, message: input.enabled ? 'Schedule created and enabled.' : 'Schedule saved paused.'}, 201);
    }
    const scheduleMatch = path.match(/^\/api\/admin\/schedules\/([\w-]+)$/);
    if (scheduleMatch && request.method === 'PUT') {
      const old = await stmt(env, 'SELECT * FROM schedules WHERE id=?', scheduleMatch[1]).first();
      if (!old) throw new HttpError(404, 'Schedule not found.');
      const input = await body(request);
      if (Object.keys(input).length === 1 && input.enabled === false) {
        await stmt(env, 'UPDATE schedules SET enabled=0,revision=?,updated_at=? WHERE id=?', crypto.randomUUID(), nowISO(), old.id).run();
      } else {
        const {spec, next_run} = scheduleSpec({...JSON.parse(old.spec), ...input}, await settings(env), await fields(env));
        await stmt(env, 'UPDATE schedules SET name=?,spec=?,enabled=?,next_run=?,revision=?,updated_at=? WHERE id=?', spec.name, JSON.stringify(spec), input.enabled ? 1 : 0, next_run, crypto.randomUUID(), nowISO(), old.id).run();
      }
      await audit(env, identity.email, 'schedule.update', old.id);
      return json({message: 'Schedule updated.'});
    }
    if (scheduleMatch && request.method === 'DELETE') {
      await stmt(env, 'DELETE FROM schedules WHERE id=?', scheduleMatch[1]).run();
      await audit(env, identity.email, 'schedule.delete', scheduleMatch[1]);
      return json({message: 'Schedule deleted. Any unsent job from it will be cancelled.'});
    }
    throw new HttpError(404, 'Admin endpoint not found.');
  }
  if (path.startsWith('/api/')) throw new HttpError(404, 'Endpoint not found.');
  if (!['GET', 'HEAD'].includes(request.method)) throw new HttpError(405, 'Method not allowed.');
  return env.ASSETS.fetch(request);
}
export async function handle(request, env, ctx, verifier = authenticate) {
  let response;
  try {
    const identity = await verifier(request, env);
    checkMutation(request, env);
    response = await route(request, env, ctx, identity);
  } catch (error) {
    let message = error.message || 'Request failed.';
    // Never return storage/crypto internals; known validation/provider errors are safe text.
    if (/D1_|SQLITE|decrypt|OperationError|encrypt/i.test(message)) message = 'Stored settings or data could not be read. Check the deployment configuration.';
    let config = {};
    if (!error.status || error.status >= 500) { try { config = await settings(env); } catch { /* fail safely */ } }
    response = json({error: redact(message, config)}, error.status || 400);
  }
  return secureResponse(response);
}
