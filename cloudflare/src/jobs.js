import {settings, fields, encrypt, decrypt, stmt, nowISO, setJob, readReport, storeReport} from './storage.js';
import {buildReport, validateOptions, period, nextRun} from './core.js';
import {fetchFields, personPage, activityTargets, activityBounds, personActivity, redact} from './providers.js';
import {payload, sendOnce} from './mail.js';

export const readStep = {retries: {limit: 4, delay: '30 seconds', backoff: 'exponential'}, timeout: '5 minutes'};
export const noRetry = {retries: {limit: 0, delay: '1 second'}, timeout: '5 minutes'};
async function jobConfig(env, id) {
  const object = await env.REPORTS.get(`jobs/${id}/config.enc`);
  if (!object) throw new Error('Job configuration expired. Start a new job.');
  return decrypt(await object.text(), env);
}
async function tempJSON(env, key) {
  const object = await env.REPORTS.get(key);
  if (!object) throw new Error('Job data expired. Start a new job.');
  return object.json();
}
export async function enqueue(env, kind, params, actor, id = crypto.randomUUID(), scheduleId = null, slot = null) {
  const now = nowISO();
  await stmt(env, 'INSERT OR IGNORE INTO jobs(id,kind,params,created_at,updated_at,created_by,schedule_id,slot) VALUES (?,?,?,?,?,?,?,?)',
    id, kind, JSON.stringify(params), now, now, actor, scheduleId, slot).run();
  return id;
}
export async function dispatch(env) {
  const active = await stmt(env, "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('dispatched','running','sending')").first();
  const count = Math.max(0, 2 - active.n);
  const {results} = await stmt(env, "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT ?", count).all();
  for (const {id} of results) {
    try {
      await env.REPORT_JOB.create({id, params: {jobId: id}});
      await stmt(env, "UPDATE jobs SET status='dispatched',updated_at=? WHERE id=? AND status='queued'", nowISO(), id).run();
    } catch {
      // A create timeout may have created the workflow. Its deterministic ID prevents duplication.
      try {
        const instance = await env.REPORT_JOB.get(id);
        const state = await instance.status();
        if (state.status) await stmt(env, "UPDATE jobs SET status='dispatched',updated_at=? WHERE id=? AND status='queued'", nowISO(), id).run();
      } catch { /* remain queued; cron safely attempts dispatch again */ }
    }
  }
}
export async function tick(env, now = new Date()) {
  const at = now.toISOString();
  const {results: schedules} = await stmt(env, 'SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 50', at).all();
  for (const schedule of schedules) {
    const spec = JSON.parse(schedule.spec);
    const id = `s-${schedule.id}-${Date.parse(schedule.next_run)}`;
    const params = {...spec, schedule_id: schedule.id, revision: schedule.revision, scheduled_at: schedule.next_run};
    // One transaction: create the unique slot and advance only the revision/slot we read.
    await env.DB.batch([
      stmt(env, `INSERT OR IGNORE INTO jobs(id,kind,params,created_at,updated_at,created_by,schedule_id,slot)
        SELECT ?, 'scheduled', ?, ?, ?, 'scheduler', id, next_run FROM schedules
        WHERE id=? AND enabled=1 AND revision=? AND next_run=?`, id, JSON.stringify(params), at, at, schedule.id, schedule.revision, schedule.next_run),
      stmt(env, 'UPDATE schedules SET next_run=? WHERE id=? AND enabled=1 AND revision=? AND next_run=?', nextRun(spec, now), schedule.id, schedule.revision, schedule.next_run)
    ]);
  }
  // Recover terminal workflow errors without retrying a possibly accepted email.
  const {results: stale} = await stmt(env, "SELECT id FROM jobs WHERE status IN ('dispatched','running','sending') AND updated_at<? LIMIT 20", new Date(now.getTime() - 30 * 60000).toISOString()).all();
  for (const job of stale) {
    try {
      const instance = await env.REPORT_JOB.get(job.id), state = await instance.status();
      if (['errored', 'terminated', 'complete'].includes(state.status)) {
        const mail = await stmt(env, 'SELECT status,message FROM mail_attempts WHERE job_id=?', job.id).first();
        await setJob(env, job.id, mail ? mail.status === 'sending' ? 'uncertain' : mail.status : 'failed', mail?.message || 'Background workflow ended without a confirmed result. Check the report list and Cloudflare Workflows.');
      }
    } catch { /* an unavailable control plane is not proof of a failed job */ }
  }
  await dispatch(env);
}
async function scheduleStillEnabled(env, params) {
  if (!params.schedule_id) return true;
  return Boolean(await stmt(env, 'SELECT id FROM schedules WHERE id=? AND enabled=1 AND revision=?', params.schedule_id, params.revision).first());
}
export async function runJob(env, id, step) {
  let safeConfig;
  try {
    const job = await step.do('load job', readStep, async () => {
      const row = await stmt(env, 'SELECT * FROM jobs WHERE id=?', id).first();
      if (!row) throw new Error('Job not found.');
      return {...row, params: JSON.parse(row.params)};
    });
    const params = job.params;
    const context = await step.do('prepare job', readStep, async () => {
      if (!await scheduleStillEnabled(env, params)) return {cancelled: true};
      const config = await settings(env);
      const options = params.options;
      await env.REPORTS.put(`jobs/${id}/config.enc`, await encrypt(config, env));
      await setJob(env, id, 'running', 'Preparing report…');
      if (['test', 'email'].includes(job.kind)) return {kind: job.kind};
      const freshFields = await fetchFields(config);
      validateOptions(options, freshFields);
      await env.REPORTS.put(`jobs/${id}/fields.json`, JSON.stringify(freshFields));
      const bounds = params.bounds || period(params, new Date(params.scheduled_at || job.created_at));
      return {kind: job.kind, bounds};
    });
    if (context.cancelled) {
      await step.do('cancel job', readStep, () => setJob(env, id, 'cancelled', 'Schedule was paused or changed.'));
      return {status: 'cancelled'};
    }
    if (!['test', 'email'].includes(job.kind)) {
      let cursor = null, total = 0, pages = 0;
      const seen = new Set();
      do {
        if (pages >= 1000) throw new Error('Pipedrive scan exceeded 500,000 people. Use a more recent period. No partial report was published.');
        const page = pages++, inputCursor = cursor;
        const result = await step.do(`Pipedrive page ${page}`, readStep, async () => {
          const config = await jobConfig(env, id);
          const data = await personPage(config, params.options, context.bounds, inputCursor);
          await env.REPORTS.put(`jobs/${id}/page-${page}.json`, JSON.stringify(data.people));
          await setJob(env, id, 'running', `Reading Pipedrive page ${page + 1}…`);
          return {cursor: data.cursor, count: data.people.length};
        });
        total += result.count;
        if (total > 20000) throw new Error('The period contains over 20,000 people. Choose a shorter period. No partial report was published.');
        cursor = result.cursor;
        if (cursor && seen.has(cursor)) throw new Error('Pipedrive repeated a pagination cursor. No partial report was published.');
        if (cursor) { seen.add(cursor); await step.sleep(`Pipedrive pace ${page}`, '1 second'); }
      } while (cursor);
      const activity = await step.do('assemble cohort', readStep, async () => {
        const people = [];
        for (let p = 0; p < pages; p++) people.push(...await tempJSON(env, `jobs/${id}/page-${p}.json`));
        const report = buildReport(people, await tempJSON(env, `jobs/${id}/fields.json`), params.options, context.bounds, params.name || 'UTM report');
        let targets = [], bounds = null;
        if (params.aircall) {
          bounds = activityBounds(report, new Date(job.created_at));
          targets = activityTargets(report);
          report.meta.activity = bounds;
        }
        await env.REPORTS.put(`jobs/${id}/draft.json`, JSON.stringify(report));
        await env.REPORTS.put(`jobs/${id}/targets.json`, JSON.stringify(targets));
        return {count: targets.length, bounds};
      });
      for (let index = 0; index < activity.count; index++) {
        await step.do(`Aircall person ${index}`, {...readStep, timeout: '15 minutes', retries: {limit: 2, delay: '1 minute', backoff: 'exponential'}}, async () => {
          const target = (await tempJSON(env, `jobs/${id}/targets.json`))[index];
          const config = await jobConfig(env, id);
          const result = await personActivity(config, target, activity.bounds);
          await env.REPORTS.put(`jobs/${id}/activity-${index}.json`, JSON.stringify(result));
          await setJob(env, id, 'running', `Aircall ${index + 1} of ${activity.count} people…`);
          return null;
        });
      }
      await step.do('publish report', readStep, async () => {
        const report = await tempJSON(env, `jobs/${id}/draft.json`);
        const byId = new Map(report.rows.map(r => [r.person_id, r]));
        for (let i = 0; i < activity.count; i++) {
          const data = await tempJSON(env, `jobs/${id}/activity-${i}.json`);
          const row = byId.get(data.person_id);
          row.aircall_status = data.status;
          row.call_count = data.calls.length;
          row.answered_calls = data.calls.filter(c => c.answered).length;
          row.transcripts_available = data.calls.filter(c => c.transcript_status === 'available').length;
          report.calls.push(...data.calls);
          if (data.status !== 'complete') report.issues.push({person_id: row.person_id, message: data.status});
        }
        await storeReport(env, id, report, job.created_by);
        await setJob(env, id, 'running', 'Report saved.', id);
        return null;
      });
    }
    if (['scheduled', 'test', 'email'].includes(job.kind)) {
      const outcome = await step.do('send email once', noRetry, async () => {
        if (!await scheduleStillEnabled(env, params)) return {status: 'cancelled', message: 'Schedule was paused or changed before sending. The saved report is still available.'};
        const config = await jobConfig(env, id);
        const report = job.kind === 'test' ? null : await readReport(env, job.kind === 'email' ? params.report_id : id);
        const message = payload(config, params.recipients, params.subject, report, params.groups ?? null, Boolean(params.matched_only));
        await setJob(env, id, 'sending', 'Submitting to SendGrid once…');
        return sendOnce(env, id, config, message);
      });
      await step.do('record mail result', readStep, () => setJob(env, id, outcome.status, outcome.message));
      return outcome;
    }
    await step.do('complete job', readStep, () => setJob(env, id, 'complete', 'Report is ready.', id));
    return {status: 'complete', report_id: id};
  } catch (error) {
    try { safeConfig = await jobConfig(env, id); } catch { safeConfig = {}; }
    // Do not throw provider payloads into Cloudflare logs/workflow state.
    const message = redact(error.message || 'Report job failed.', safeConfig);
    await step.do('record failure', readStep, async () => {
      const mail = await stmt(env, 'SELECT status,message FROM mail_attempts WHERE job_id=?', id).first();
      const status = mail ? mail.status === 'sending' ? 'uncertain' : mail.status : 'failed';
      await setJob(env, id, status, mail?.message || message);
    });
    return {status: 'failed', message};
  }
}
