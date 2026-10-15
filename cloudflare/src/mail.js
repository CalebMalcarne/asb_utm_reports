import {exportParts} from './core.js';
import {redact} from './providers.js';
import {stmt, nowISO} from './storage.js';

export function recipients(value) {
  const list = [...new Set((Array.isArray(value) ? value : String(value || '').split(/[,;\n]/)).map(s => s.trim()).filter(Boolean))];
  if (!list.length || list.length > 50 || list.some(s => !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(s))) throw new Error('Enter 1–50 valid email addresses, separated by commas.');
  return list;
}
export function endpoint(config) {
  if (!['global', 'eu'].includes(config.sendgrid_region)) throw new Error('Choose a valid SendGrid region.');
  return config.sendgrid_region === 'eu' ? 'https://api.eu.sendgrid.com/v3/mail/send' : 'https://api.sendgrid.com/v3/mail/send';
}
export function rejection(status, body, config) {
  let details = '';
  try { details = JSON.parse(body).errors?.map(e => String(e.message || '')).slice(0, 3).join(' ') || ''; } catch { /* never echo arbitrary HTML */ }
  let advice;
  if (/maximum credits exceeded/i.test(details)) advice = 'Sending quota exceeded. Check your SendGrid Email API plan, billing status, and credit reset. API key permissions do not fix this.';
  else if (/verified Sender Identity/i.test(details)) advice = 'Set the From address to an exact verified SendGrid sender, or authenticate its domain in SendGrid Settings > Sender Authentication.';
  else if (status === 401) advice = 'Authentication rejected. Check the full saved SendGrid secret, account status, and US/global versus EU region.';
  else if (status === 403) advice = 'Access denied. Check Mail Send permission, sender verification, account restrictions, and IP access rules.';
  else advice = 'SendGrid rejected the request.';
  return redact(`SendGrid HTTP ${status}. ${advice}${details ? ' SendGrid says: ' + details : ''}`, config);
}
function base64(text) {
  const bytes = new TextEncoder().encode(text);
  let out = '';
  for (let i = 0; i < bytes.length; i += 8192) out += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(out);
}
export function payload(config, to, subject, report, groups = null, matchedOnly = false, sandbox = false) {
  const from = recipients(config.sender);
  if (from.length !== 1) throw new Error('Enter one verified From email address.');
  if (!config.sendgrid_key) throw new Error('Save a SendGrid API key.');
  const message = {
    from: {email: from[0]}, personalizations: recipients(to).map(email => ({to: [{email}]})),
    subject: String(subject || 'ASB UTM report').slice(0, 180),
    content: [{type: 'text/plain', value: report ? `${report.name}\nCohort: ${report.meta.start} to ${report.meta.end} (end exclusive).\nTags reflect Pipedrive at fetch time. CSV files contain the selected UTM groups and their statistics.` : 'ASB UTM Reports email test.'}],
    tracking_settings: {click_tracking: {enable: false, enable_text: false}, open_tracking: {enable: false}}
  };
  if (report) message.attachments = Object.entries(exportParts(report, groups, matchedOnly)).map(([filename, text]) => ({
    filename, type: filename.endsWith('.csv') ? 'text/csv' : 'application/json', disposition: 'attachment', content: base64(text)
  }));
  if (sandbox) message.mail_settings = {sandbox_mode: {enable: true}};
  if (new TextEncoder().encode(JSON.stringify(message)).length > 25 * 1024 * 1024) throw new Error('Email exceeds 25 MB. Select fewer groups or a shorter period.');
  return message;
}
export async function send(config, message, fetcher = fetch) {
  let response;
  try { response = await fetcher(endpoint(config), {method: 'POST', redirect: 'error', headers: {Authorization: `Bearer ${config.sendgrid_key}`, 'Content-Type': 'application/json'}, body: JSON.stringify(message), signal: AbortSignal.timeout(60000)}); }
  catch { return {status: 'uncertain', message: 'SendGrid connection ended without a reliable result. Check SendGrid Activity before sending again; no automatic retry.'}; }
  const sandbox = message.mail_settings?.sandbox_mode?.enable;
  if (response.status === (sandbox ? 200 : 202)) return {status: sandbox ? 'validated' : 'accepted', message: sandbox ? 'Request validated in sandbox mode; no email sent. This does not prove sender verification or delivery.' : 'Accepted by SendGrid; delivery is not yet confirmed.'};
  return {status: response.status >= 500 ? 'uncertain' : 'rejected', message: rejection(response.status, await response.text(), config)};
}
// Persist the attempt BEFORE POST. A resumed/replayed workflow never sends it twice.
export async function sendOnce(env, jobId, config, message, fetcher = fetch) {
  const claim = await stmt(env, 'INSERT OR IGNORE INTO mail_attempts(job_id,status,updated_at) VALUES (?, ?, ?)', jobId, 'sending', nowISO()).run();
  if (!claim.meta.changes) {
    const previous = await stmt(env, 'SELECT * FROM mail_attempts WHERE job_id=?', jobId).first();
    return previous.status === 'sending' ? {status: 'uncertain', message: 'An earlier send attempt has no confirmed result. Check SendGrid Activity before sending again.'} : {status: previous.status, message: previous.message};
  }
  const result = await send(config, message, fetcher);
  await stmt(env, 'UPDATE mail_attempts SET status=?,message=?,updated_at=? WHERE job_id=?', result.status, result.message, nowISO(), jobId).run();
  return result;
}
