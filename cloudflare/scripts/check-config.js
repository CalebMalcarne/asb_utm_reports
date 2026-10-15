import {readFileSync} from 'node:fs';
const config = JSON.parse(readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8'));
const v = config.vars, errors = [];
if (!/^https:\/\/[^/]+$/.test(v.APP_ORIGIN) || v.APP_ORIGIN.includes('example.com')) errors.push('Set APP_ORIGIN to your real HTTPS hostname.');
if (!/^[a-z0-9-]+\.cloudflareaccess\.com$/i.test(v.ACCESS_TEAM_DOMAIN) || v.ACCESS_TEAM_DOMAIN.includes('YOUR-TEAM')) errors.push('Set ACCESS_TEAM_DOMAIN to your Cloudflare Zero Trust team domain.');
if (!v.ACCESS_AUD || v.ACCESS_AUD.includes('REPLACE')) errors.push('Set ACCESS_AUD from the Access application.');
for (const key of ['ADMIN_EMAIL', 'USER_EMAIL']) if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v[key]) || v[key].endsWith('@example.com')) errors.push(`Set ${key} to a real login email.`);
if (v.ADMIN_EMAIL.toLowerCase() === v.USER_EMAIL.toLowerCase()) errors.push('Admin and user must have different email addresses.');
if (config.d1_databases[0].database_id === '00000000-0000-0000-0000-000000000000') errors.push('Set the D1 database_id returned by wrangler d1 create.');
if (config.routes[0].pattern !== new URL(v.APP_ORIGIN).hostname) errors.push('The custom domain route must match APP_ORIGIN.');
if (config.workers_dev !== false || config.preview_urls !== false || config.assets.run_worker_first !== true) errors.push('Keep workers_dev/preview_urls disabled and run_worker_first enabled.');
if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
console.log('Deployment configuration is ready. Ensure the encryption secret and Access policy are configured before deployment.');
