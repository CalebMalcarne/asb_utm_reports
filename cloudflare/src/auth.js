import {createRemoteJWKSet, jwtVerify} from 'jose';

export class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const keySets = new Map();
export async function authenticate(request, env, verificationKey) {
  const admin = (env.ADMIN_EMAIL || '').trim().toLowerCase();
  const user = (env.USER_EMAIL || '').trim().toLowerCase();
  const team = env.ACCESS_TEAM_DOMAIN || '';
  if (!admin || !user || admin === user || !env.ACCESS_AUD || !/^[a-z0-9-]+\.cloudflareaccess\.com$/i.test(team)) {
    throw new HttpError(503, 'Login is not configured. Complete the Cloudflare deployment settings.');
  }
  const token = request.headers.get('Cf-Access-Jwt-Assertion');
  if (!token) throw new HttpError(401, 'Sign in through Cloudflare Access.');
  const issuer = `https://${team}`;
  if (!verificationKey && !keySets.has(issuer)) keySets.set(issuer, createRemoteJWKSet(new URL(`${issuer}/cdn-cgi/access/certs`)));
  let payload;
  try {
    ({payload} = await jwtVerify(token, verificationKey || keySets.get(issuer), {
      issuer, audience: env.ACCESS_AUD, algorithms: ['RS256'], requiredClaims: ['exp', 'iat', 'sub', 'email'], clockTolerance: 5
    }));
  } catch { throw new HttpError(401, 'Your login is invalid or expired. Sign in again.'); }
  const email = String(payload.email).trim().toLowerCase();
  if (email !== admin && email !== user) throw new HttpError(403, 'This account does not have access.');
  return {email, role: email === admin ? 'admin' : 'user'};
}
export function requireAdmin(identity) {
  if (identity.role !== 'admin') throw new HttpError(403, 'Only the administrator can perform this action.');
}
export function checkMutation(request, env) {
  if (['GET', 'HEAD'].includes(request.method)) return;
  if (!env.APP_ORIGIN || request.headers.get('Origin') !== env.APP_ORIGIN || new URL(request.url).origin !== env.APP_ORIGIN) {
    throw new HttpError(403, 'Request origin does not match this application.');
  }
  if (!request.headers.get('Content-Type')?.startsWith('application/json') || request.headers.get('X-ASB-Request') !== '1') {
    throw new HttpError(403, 'Use the application to submit changes.');
  }
}
export function secureResponse(response) {
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'no-store');
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('Referrer-Policy', 'no-referrer');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
  return new Response(response.body, {status: response.status, headers});
}
