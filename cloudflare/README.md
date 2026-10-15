# ASB UTM Reports — Cloudflare edition

A self-contained Cloudflare implementation of the UTM reporting tool. **Copy the contents of this folder into the root of your new repository.** It does not import anything from the Python/Streamlit app in the parent folder and does not require a running PC, Python server, or Cloudflare Tunnel.

This is a **Cloudflare Worker with Static Assets**, not a Pages-only static site. The browser UI, API, persistent report storage, and scheduler are all hosted on Cloudflare.

## Two accounts

Sign-in uses **Cloudflare Access**, with an email one-time code or your existing identity provider. There are no shared passwords or public account registration.

| Capability | Admin | User |
| --- | --- | --- |
| View saved reports, group tabs, statistics, calls/transcripts | Yes | Yes |
| Export current tab, all codes, or selected groups | Yes | Yes |
| Create reports / fetch Pipedrive and Aircall | Yes | No |
| Change API keys, field mappings, statistic tags | Yes | No |
| Test email, email reports, create/edit/pause schedules | Yes | No |
| View provider errors and job history | Yes | No |

The Worker verifies the Access token's signature, issuer, audience, and expiry on **every request**, including assets. Only the two configured email identities are accepted. The role is assigned on the server from that verified identity. Hiding controls in the browser is not the permission boundary.

## What is included

- Saved, centrally accessible reports grouped by actual UTM values; **All UTM codes** combines the people counts, not the average of group percentages.
- Qualified/contacted counts and percentages on screen and in CSV exports. Exact Pipedrive tag IDs prevent “Unqualified” from matching “Qualified.” Multiple statistic tags count each person once.
- Today/yesterday, week/month, last 7/30 days, custom inclusive dates, and previous N complete days, using a configurable time zone.
- Pipedrive person field discovery, UTM mappings, native labels or custom tags, configurable grouping and matching rules.
- Optional Aircall call history and transcripts for matching people, with shared phone numbers flagged and skipped. Each report retains its original tag/statistic configuration.
- SendGrid test emails and report attachments, global/EU selection, sender-verification/quota diagnostics, and no automatic retry of an uncertain email submission.
- Daily, weekly, monthly, or every-N-hours email schedules; relative reporting periods and all/selected UTM scopes. Each occurrence fetches a fresh report.
- Encrypted API settings in D1, private report objects in R2, and Cloudflare Workflows for imports that outlive a browser request.

## 1. Put this folder in the new repository

Copy **all files inside `cloudflare/`**, including `.gitignore`, into your new repo's root. Do not copy `node_modules`, `dist`, `.wrangler`, or local environment files. The root should contain `package.json`, `package-lock.json`, `wrangler.jsonc`, `src/`, `public/`, `migrations/`, `scripts/`, and `test/`.

Use Node.js 22 or newer (Node 24 recommended). From that root:

```sh
npm ci
npm test
npm run build
```

`build` bundles a local dry run; it does not deploy anything. This project is intended for **Workers Paid** because paginated imports and Aircall enrichment can exceed free Workflow step limits. Enable R2 in your Cloudflare account as well. Check current Cloudflare usage/pricing for your account before running large recurring imports.

## 2. Create storage

Sign in to the Cloudflare account that manages your intended domain:

```sh
npx wrangler login
npx wrangler d1 create asb-utm-reports
npx wrangler r2 bucket create asb-utm-reports
```

Copy the returned D1 `database_id` into `wrangler.jsonc`. If you choose different resource names, update the bindings and `migrate` script consistently.

Keep the R2 bucket **private**: do not enable `r2.dev` public access or attach a public bucket domain. Reports are served only through the authenticated Worker.

## 3. Configure Cloudflare Access

In Cloudflare Zero Trust:

1. Configure a login method, such as the one-time PIN sent to a user's email.
2. Add an **Access → Applications → Self-hosted** application for your full report hostname, for example `reports.yourdomain.com`. Protect the entire hostname, including `/api/*`; do not limit protection to one path.
3. Add an **Allow** policy with an **Emails** include rule containing exactly your admin email and your user email. Do not use an Everyone or Bypass policy.
4. Copy the application's **Audience (AUD) tag** and your Zero Trust team domain, such as `your-team.cloudflareaccess.com`.

Edit `wrangler.jsonc`:

| Setting | Value |
| --- | --- |
| `routes[0].pattern` | Your report hostname, without `https://` |
| `APP_ORIGIN` | The same hostname with `https://`, no trailing slash |
| `ACCESS_TEAM_DOMAIN` | Your team domain, without `https://` |
| `ACCESS_AUD` | The Access application's AUD tag |
| `ADMIN_EMAIL` | The one admin login email |
| `USER_EMAIL` | The one report-only login email, different from admin |
| `d1_databases[0].database_id` | The database ID created above |

Keep `workers_dev: false`, `preview_urls: false`, and `assets.run_worker_first: true`. The token check also denies unauthenticated requests if an alternate route is accidentally exposed. Use a custom domain in a Cloudflare-managed zone; Wrangler creates the Worker custom-domain route.

## 4. Deploy and set the encryption secret

```sh
npm run check
npm run migrate
npm run deploy
```

`check` refuses the example deployment values. `migrate` applies the versioned D1 schema to the remote database. `deploy` creates/updates the Worker, assets, Workflow binding, and five-minute cron trigger. A new cron trigger can take time to propagate in Cloudflare.

Generate one encryption key on your own computer:

```sh
node -e "console.log(require('node:crypto').randomBytes(32).toString('base64'))"
npx wrangler secret put SETTINGS_ENCRYPTION_KEY
```

Paste the generated value at Wrangler's secret prompt and keep a recovery copy in your password manager. **Do not put this value into Git or the browser settings form.** The app cannot save credentials until this secret is configured. Losing or replacing it prevents decryption of existing settings and in-flight job snapshots; do not rotate it casually.

Open your hostname, sign in as the admin, then:

1. Enter the Pipedrive company/token in **Settings** and choose **Save & connect Pipedrive / refresh fields**.
2. Review the discovered UTM fields, tag field, grouping, Qualified tags, Contacted tags, and matching tags. For a plain-text custom tag field, add the exact tag text using **Additional text tags**.
3. Enter the Aircall API ID/token if needed.
4. Enter the SendGrid key and **verified From email**. Use US/global unless the account is specifically an EU regional subuser. The validation button uses SendGrid sandbox mode and sends no email; the separate test-email button sends a real message.
5. Save settings, create a report, and check it under **Saved report**. Imports run in the background; the admin's job list shows progress and results.
6. Sign in as the other email to verify that it sees only Reports and can export.
7. Create a schedule and explicitly check **Enable recurring emails** when ready.

No real API credentials, account identities, or live deployment are included in this repository. The old app's local encrypted settings and schedules are not automatically copied; configure the Cloudflare app once and recreate the schedules you want enabled. Disable the old scheduler when switching production email delivery to avoid two independent systems sending the same reports.

## Git-based deployments

After the first deployment, you can connect the **new repository** in Cloudflare **Workers & Pages → your Worker → Settings → Builds**. Use the repository root as the project root, `npm test` as the build command, and `npm run deploy` as the deploy command. Cloudflare installs the locked npm dependencies. This is a Worker integration; do not select a Pages static-site preset.

Keep the existing Worker name and bindings. The encryption secret lives on the Worker, not in the repository. Apply any future D1 migrations before deploying code that needs them (`npm run migrate`). Keep the original `cloudflare/` folder and your new repository in sync deliberately; they will not automatically track each other after copying.

## Reporting and scheduler behavior

- A cohort is based on **Pipedrive person creation time**, inclusive start / exclusive end. It contains only people with at least one mapped UTM value. “Client” or another tag can be selected for matching. This is not historical deal-event attribution: tags are the current values at fetch time.
- Weeks start Monday. Custom end dates include the entire local day, clipped to the fetch time. Qualified/contacted percentages always use the full selected cohort, even when detail rows are filtered to matching people. No configured statistic tags means blank/“Not configured,” not zero.
- Tabs switch entirely in the browser, without URL navigation or rerunning an import. Export buttons use the active tab. The separate selected-groups export uses the explicitly selected groups. Name/phone search affects the visible table only; it does not silently narrow exports.
- CSVs include a UTF-8 BOM and escape spreadsheet formula prefixes. ZIP exports include people, summary, activity/transcripts, and the report settings. Emails attach those files to each recipient in a separate SendGrid personalization.
- Aircall covers cohort start through the job creation time, for people matching the report's selected tags. Search must be within Aircall's recent six-month window. A missing or forbidden transcript is labeled separately from call counts. Shared numbers within the cohort are not assigned ambiguously.
- The cron trigger checks every five minutes. Expect sends near the selected time, plus import/queue time, rather than to-the-second delivery. At most two jobs are dispatched concurrently; queued jobs run later.
- Schedule times use the selected zone. Spring clock gaps shift forward; the first occurrence is used for repeated fall clock times. Monthly day 29/30/31 is clamped to month-end. Every-N-hours intervals use elapsed time from the saved anchor.
- After downtime, one overdue slot is claimed and the schedule advances past the backlog. The report period is anchored to that scheduled slot, not whatever date processing eventually finishes. Slot claims are atomic and unique.
- Saving settings or refreshing fields pauses existing schedules. Review and re-enable them. A paused, deleted, or edited schedule is checked again immediately before submitting its email. An email already submitted cannot be recalled.
- A SendGrid `202` means **accepted**, not delivered. A network timeout/5xx or interrupted send becomes **uncertain** and is never automatically resubmitted. Check SendGrid Activity before manually sending again. At-most-once submission favors preventing duplicate mail over automatically recovering an interrupted send.
- API read errors may retry with backoff. A report that cannot be fetched completely is not published as a successful partial report.

## Limits and data management

The app intentionally stops with an actionable error instead of silently truncating: 20,000 people in a report period, a scan of 500,000 Pipedrive people, 500 Aircall matching people, 200 calls per person, 100,000 characters per transcript, 20 MB per stored report, and 25 MB per email request. Narrow the cohort or tag selection if needed. For substantially larger datasets, extend this design with partitioned reports and streamed exports before raising the limits.

Saved reports remain in the private R2 `reports/` prefix; report metadata, schedules, job history, and audit entries live in D1. Temporary imports and encrypted credential snapshots use `jobs/`. In R2 bucket **Settings → Object lifecycle rules**, you may add an expiration rule **only for the `jobs/` prefix**, for example 30 days. Keep it longer than your longest running Workflow. Never apply that temporary-data rule to `reports/`.

Back up D1, private report objects, and the encryption secret according to your retention requirements. Removing a report administratively requires removing both its D1 `reports` row and its R2 object. There is no public bucket URL or browser-facing credential readback endpoint.

Pipedrive/Aircall/SendGrid accounts with source-IP restrictions must allow your hosting arrangement. Cloudflare Workers do not provide a dedicated static outbound IP in this configuration.

## Development and verification

```sh
npm ci
npm test
npm run build
npx wrangler d1 migrations apply asb-utm-reports --local
```

The Node tests use an isolated SQLite database and mock provider responses. They cover JWT verification, role restrictions, CSRF, credential encryption, report calculations and scoped exports, DST scheduling, slot deduplication, durable import replay, cancelled schedules, and one-time email submission. They send no real mail and do not connect to Pipedrive/Aircall.

`npm run dev` starts the actual Worker locally, but it still requires a valid Access token. There is deliberately no production-reachable development login bypass. Use a separate Access-protected staging Worker for account/provider end-to-end testing.

Optional UI smoke test:

```sh
npx playwright install chromium
npm run test:ui
```

The UI smoke test serves fixture data locally, checks admin/user screens, switches UTM tabs without resetting scroll, and checks the scope of an export. It is not an authentication test; backend authorization is tested separately.

### Validation of this initial implementation

The automated backend and DOM tests pass, the Wrangler deployment dry run bundles successfully, and the D1 migration applies successfully to a local database. No live provider calls or real emails were used. Full Chromium visual testing was blocked in the development environment by a failed browser download, and the interactive Wrangler server could not start because the environment denied network-interface enumeration. Run the optional browser smoke test and verify both logins plus a small live report on your Access-protected deployment before enabling recurring mail.

## Official references

- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Validate Cloudflare Access JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
- [Cloudflare self-hosted Access applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
- [Workflows Workers API](https://developers.cloudflare.com/workflows/build/workers-api/)
- [R2 lifecycle rules](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)
- [Pipedrive Persons API](https://developers.pipedrive.com/docs/api/v1/Persons)
- [Aircall API](https://developers.aircall.io/api-references/)
- [SendGrid sandbox validation](https://www.twilio.com/docs/sendgrid/for-developers/sending-email/sandbox-mode)
