import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPair, SignJWT} from 'jose';
import {authenticate, checkMutation} from '../src/auth.js';
import {handle} from '../src/router.js';
import {environment} from './helpers.js';

const keys = await generateKeyPair('RS256');
const env = environment();
async function token(email, overrides = {}) {
  return new SignJWT({email, aud: env.ACCESS_AUD, iss: `https://${env.ACCESS_TEAM_DOMAIN}`, sub: email,
    iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 300, ...overrides})
    .setProtectedHeader({alg: 'RS256'}).sign(keys.privateKey);
}
function request(jwt) { return new Request(env.APP_ORIGIN, {headers: jwt ? {'Cf-Access-Jwt-Assertion': jwt} : {}}); }
test('requires a signed token, and derives the role only from verified email', async () => {
  await assert.rejects(authenticate(request(), env, keys.publicKey), {status: 401});
  const admin = await authenticate(request(await token(env.ADMIN_EMAIL)), env, keys.publicKey);
  assert.equal(admin.role, 'admin');
  const user = await authenticate(request(await token(env.USER_EMAIL, {role: 'admin'})), env, keys.publicKey);
  assert.equal(user.role, 'user');
  await assert.rejects(authenticate(request(await token('outsider@asb.test')), env, keys.publicKey), {status: 403});
});
test('rejects expiry, issuer/audience mismatch, forged signature, and identical role identities', async () => {
  for (const override of [{exp: 1}, {aud: 'other-app'}, {iss: 'https://evil.test'}]) await assert.rejects(authenticate(request(await token(env.ADMIN_EMAIL, override)), env, keys.publicKey), {status: 401});
  const other = await generateKeyPair('RS256');
  await assert.rejects(authenticate(request(await token(env.ADMIN_EMAIL)), env, other.publicKey), {status: 401});
  await assert.rejects(authenticate(request(await token(env.ADMIN_EMAIL)), {...env, USER_EMAIL: env.ADMIN_EMAIL}, keys.publicKey), {status: 503});
});
test('Cloudflare identity headers alone do not authenticate', async () => {
  const response = await handle(new Request(env.APP_ORIGIN, {headers: {'Cf-Access-Authenticated-User-Email': env.ADMIN_EMAIL}}), env, {});
  assert.equal(response.status, 401); assert.equal(response.headers.get('Cache-Control'), 'no-store');
});
test('regular user is blocked from every admin route, regardless of UI', async () => {
  const endpoints = [['settings', 'GET'], ['settings', 'PUT'], ['fields', 'POST'], ['sendgrid-check', 'POST'], ['jobs', 'GET'],
    ['reports', 'POST'], ['test-email', 'POST'], ['email', 'POST'], ['schedules', 'GET'], ['schedules', 'POST'], ['schedules/id', 'PUT'], ['schedules/id', 'DELETE']];
  for (const [path, method] of endpoints) {
    const response = await handle(new Request(`${env.APP_ORIGIN}/api/admin/${path}`, {method, headers: {'Origin': env.APP_ORIGIN, 'Content-Type': 'application/json', 'X-ASB-Request': '1'}, body: method === 'GET' ? undefined : '{}'}), env, {}, async () => ({email: env.USER_EMAIL, role: 'user'}));
    assert.equal(response.status, 403, `${method} ${path}`);
  }
});
test('CSRF protection requires exact origin, JSON, and custom request header', () => {
  const valid = {Origin: env.APP_ORIGIN, 'Content-Type': 'application/json', 'X-ASB-Request': '1'};
  assert.doesNotThrow(() => checkMutation(new Request(env.APP_ORIGIN, {method: 'POST', headers: valid}), env));
  for (const headers of [{...valid, Origin: 'https://evil.test'}, {...valid, Origin: 'null'}, {...valid, 'X-ASB-Request': ''}, {...valid, 'Content-Type': 'text/plain'}]) assert.throws(() => checkMutation(new Request(env.APP_ORIGIN, {method: 'POST', headers}), env), {status: 403});
});
