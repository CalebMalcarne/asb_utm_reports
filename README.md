# ASB UTM Reports — version 2

A local Python dashboard that uses Pipedrive as the source of truth for advertiser, campaign, and social-media lead reporting. GHL imports are no longer required.

## What it does

1. Fetch people created in Pipedrive during a day, week, month, or custom period.
2. Keep only people with at least one mapped UTM value.
3. Group by `utm_source` + `utm_campaign`, or any one mapped UTM dimension.
4. Show who has selected Pipedrive labels or custom tags (Qualified, Client, etc.).
5. Fetch Aircall calls and available transcripts for matching or manually selected people.
6. Export all or selected UTM groups to CSV or a report ZIP.
7. Send reports using Twilio SendGrid, manually or on a persistent local schedule.

## Start on Windows

- Install Python 3.11 or newer if necessary.
- Extract the ZIP completely and open `asb_utm_reports`.
- Double-click `start_windows.bat`. It also updates dependencies; UTM tabs require Streamlit 1.59 or newer.
- The first launch installs dependencies. The browser opens at http://127.0.0.1:8501.
- Keep the launcher terminal open. It runs both the dashboard and the schedule worker.
- Click **Load demo** to explore with synthetic data.

PowerShell alternative, from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

No environment activation or PowerShell execution-policy change is needed. On macOS/Linux use `python3 -m venv .venv`, then `.venv/bin/python -m pip install -r requirements.txt` and `.venv/bin/python launch.py`.

## Settings: connect the accounts

Open **Settings** and enter:

| Setting | Value |
| --- | --- |
| Pipedrive company | `yourcompany` or `https://yourcompany.pipedrive.com` |
| Pipedrive API token | Token belonging to a user who can see all relevant people |
| Aircall API ID/token | Your Aircall credentials |
| SendGrid API key | A SendGrid key with Mail Send permission; not a Twilio SMS Auth Token |
| Verified sender email | An address authorized by your SendGrid sender/domain verification |
| Sender name | For example, ASB Reports |
| SendGrid region | US/global by default; EU only for a regional EU subuser |
| Report timezone | Defaults to America/New_York |

Click **Save & connect Pipedrive / refresh fields**. This saves the values you just entered, checks the account credentials, then loads the fields. The app retrieves the actual account schema and infers exact-name UTM fields. Confirm or change the field mappings. Duplicate field names require explicit mapping; the API field code is displayed to distinguish them.

Map `utm_source`, `utm_campaign`, and any other available UTM fields. A person needs at least one nonempty mapped UTM value to enter the report. A missing value in the particular grouping dimension appears as `(missing)`; the person remains eligible when another mapped UTM exists.

Choose **Person tag field**:

- **Native person labels** for actual Pipedrive person labels.
- A custom set/single-option field, with options loaded from Pipedrive.
- A custom text field such as **Tags Aircall** or **Aircall Tags**, parsed as comma-, semicolon-, or newline-separated tags. Choices are discovered from people in the fetched period.

Those two similarly named fields are not assumed to be identical. Choose the one your integration actually updates. Matching uses exact tag IDs for option fields and exact case-insensitive text tokens for text fields. `Qualified` does not match `Unqualified`.

API secrets are encrypted in the local `.data/reports.sqlite3` settings record. The local encryption key lives in `.data/settings.key`; protect the entire folder with normal OS account permissions. Encryption is not protection against someone who can read both files. The folder is excluded from Git and from the deliverable. Scheduled recipients and run history also live in the database. Reports and transcripts are held in the current browser session until exported.

The app listens on localhost and has no multi-user authentication. Use it on your computer or a secured internal machine. Do not expose port 8501 directly to the internet.

### Pipedrive HTTP 401 troubleshooting

The app uses Pipedrive's documented `x-api-token` header. HTTP 401 means Pipedrive rejected authentication; it does not identify exactly which credential is wrong.

1. Open the same Pipedrive company whose subdomain you entered in Settings.
2. Go to **Personal preferences → API** and copy the current **personal API token**. Do not use an OAuth client ID/secret, password, or another service's key. Each company has its own token.
3. Paste it into the password field and click **Save & connect Pipedrive / refresh fields**. This now saves and tests the entered values in one action, avoiding tests with previously saved credentials.
4. If the account check succeeds but field loading fails, the app shows the successful credential check separately. Check API access with your Pipedrive admin if access is denied.

Do not share tokens in chat, screenshots, or GitHub. An old token stops working if it has been regenerated; update this app with the current token. There is no need to regenerate a working token just to test this tool.

To update a local copy, stop the launcher, pull/download the latest repository code, and restart `start_windows.bat`. Keep the existing `.data` folder in the app folder to preserve credentials and schedules; never commit it.

## Build a report

1. In **Reports**, choose Today, Yesterday, This week, Last week, Last 7 days, This month, Last month, Last 30 days, or Custom.
2. Click **Fetch Pipedrive people**.
3. Choose one or more report tags. Qualified is selected automatically when the label is available with that exact name.
4. Choose Any selected tag or All selected tags.
5. Choose grouping. Default: source + campaign.
6. Select **All UTM codes** for combined stats and a comparison of every group, or choose an individual UTM tab. Labels show the actual code (or source / campaign when grouping by both).
7. Review that group’s qualified/contacted percentages and data sheet, then download its Summary CSV, People CSV, or Full report ZIP. Each download contains only the current tab.
8. Expand **Qualified / contacted statistic tags** to change which Pipedrive tags count toward each statistic. Defaults use exact Qualified and Contacted tag names.

Changing the date controls does not silently change a previously fetched report. Its loaded period is displayed; click Fetch again for new dates. Changing tags or grouping reuses the current data and any retrieved call evidence.

### Definition of the date range

The cohort is **Pipedrive person creation time (`add_time`)**. It is not the person's last update date, deal creation date, original form submission date, or the date a tag was applied. Weeks start Monday. Calendar boundaries use the configured timezone and handle daylight saving. Current-day periods end at fetch time.

Pipedrive's list API has update-time filters, which are not creation-time filters. This app pages people in descending creation order, skips newer records outside the period, and stops after reaching older records. It may need to read through many newer people to reach an old period. Results are limited by API-user permissions and existing, accessible records.

Tags represent the **current** state at fetch time. A Client tag in a September cohort can mean the person became a client in October. This version does not infer a historical qualification or conversion date. It also does not query Pipedrive deals; the person tags you select define the status being measured.

### Group tabs and statistics

**All UTM codes** is the first tab. It shows total people, matching people, and overall qualified/contacted percentages, a comparison table for every UTM group, and a combined people sheet. Overall percentages divide total matching people by total people; they do not average the individual group percentages. Each UTM group also has its own tab, data sheet, stats, and export buttons. Each tab has independent filters, Aircall retrieval controls, and export buttons. Tab switching happens in the browser without rerunning the app, avoiding the full-page refresh that caused a jump to the top. Downloads also avoid rerunning the page. Tab contents are prepared together from the loaded report; changing tabs does not make API requests. Group-specific filenames use the UTM values plus a short identifier to prevent collisions. Email and scheduling still support all groups or a selected set.

**Qualified %** = people with any configured Qualified statistic tag / all people in that UTM group × 100. **Contacted %** uses the configured Contacted tags with the same denominator. A person with several matching tags counts once per statistic. The two percentages can overlap; they are not stages of a historical funnel. A Qualified tag alone does not imply Contacted unless you include it in the Contacted selector. These stats use the selected Pipedrive person tag field, not call duration or Aircall retrieval success.

If no statistic tag is configured or an exact default tag is unavailable, show **—** and export blank statistics instead of implying 0%. With configured tags but no matching people, export 0%. Tag names are matched exactly, ignoring case; Unqualified does not count as Qualified. Percentage columns are numbers on a 0–100 scale.

The All UTM codes **Summary CSV** includes a `row_type=total` row followed by `row_type=utm_group` rows; do not sum the total row together with group rows. Individual-tab CSVs contain only their group. All-group email/scheduled summary exports use the same total-plus-groups format. The **Summary CSV** has `qualified_people`, `qualified_pct`, `contacted_people`, and `contacted_pct`. The **People CSV** repeats those group statistics on each person row as `group_qualified_people`, `group_qualified_pct`, `group_contacted_people`, `group_contacted_pct`, with `group_people` as the denominator. Repeated group totals are context columns; do not sum them across person rows. The summary CSV retains the group's stats even when a matching-only filter leaves no detail rows.

Scheduled CSVs use the saved statistic tag selections; save an existing schedule again to update those selections. Older schedules without explicit statistic settings use the exact Qualified and Contacted names.

### Counting

Each Pipedrive person ID is one person, once per group. Distinct duplicate CRM people remain distinct; the tool does not silently merge them by name or phone. A group summary includes all UTM people, the count matching the tag rule, the match percentage, and counts for every available/discovered tag. Tag columns can overlap and are not additive.

Selecting **only matching people** filters the detail attachment/table. It does not remove nonmatching people from the summary denominator. This avoids turning a selected Qualified-only export into a misleading 100% qualification rate.

## Aircall activity and transcripts

The default retrieval selection is the people matching your report tags in the current UTM tab. You can manually add/remove people. **Activity from / through default to the loaded person creation period**, including its exact fetch-time cutoff for a partial current day. An exclusive midnight end is displayed as the preceding inclusive date. Changing either date opts into a custom activity window. Fetching a new cohort resets the defaults; ordinary reruns preserve your edits. Click **Fetch calls & transcripts** to apply the window. Each fetched person's activity window is recorded in their people CSV row.

- Searches every normalized phone number on each selected Pipedrive person.
- Uses the other party's phone (`raw_digits`), not ASB's own number.
- Includes all call directions, durations, human agents, and AI/system calls returned by the API.
- Retrieves asynchronous transcripts only for the returned matching calls.
- Shows unavailable, forbidden, empty, and not-requested transcript states explicitly.
- A shared phone among multiple people in this report is flagged and skipped rather than attributed to several people.
- Phone numbers default to US formatting; international numbers need a country code.
- Repeated imports replace selected people's evidence, preventing duplicate call rows.

The table shows answered-to-end elapsed seconds; this can include hold time and is not a verified pure talk-time measure. An answered timestamp does not prove a human conversation.

Aircall provides six months of call history through this API. Earlier live activity requests are rejected to avoid presenting an incomplete history as complete. Requests near the 10,000-record cap are split into smaller date windows. If a single second exceeds the cap, the import fails explicitly. Transcript availability depends on your Aircall configuration, permissions, and whether that call has a generated transcript. A shared/recycled phone outside the selected Pipedrive cohort cannot be identified from this cohort alone.

### Full report ZIP

Enter a **Report name** above the UTM tabs. Each tab's Full report ZIP uses that tab's UTM scope and includes:

| File | Contents |
| --- | --- |
| `Aircall_Activity.csv` | All fetched calls for people in the selected tab |
| `all_People.csv` | Everyone in that tab, including nonmatching people |
| `Match_people.csv` | Only people matching the selected Report tags and Match rule |
| `Match_aircall_activity.csv` | Fetched calls belonging to those matching people |
| `UTM_summary.csv` | UTM group statistics, plus the overall row in the All UTM codes tab |
| `Overall Summary.pdf` | Report name, cohort dates, fetch/export timestamps, company, UTM information, searched tags, qualified/matched/contacted counts and percentages, and a table of qualified **or** matching people |
| `transcripts/First_Last_<CALL ID>.txt` | Available transcript text, using the person's name and Aircall call ID |

Empty data CSVs and blank/unavailable transcripts are omitted. The PDF still explains an empty scope. Names with more than two words retain those words separated by underscores; missing names use `Unknown_Person_<PERSON ID>_<CALL ID>.txt`. Unsafe filename characters are removed.

The full ZIP always separates **all** people from **matching** people, regardless of the matching-only checkbox. That checkbox continues to filter the standalone People CSV and data sheet. All files respect the selected UTM tab; only the All UTM codes tab exports all groups. The PDF's qualified-or-matching table is a union, without duplicate people. Its percentages use all people in the tab, and “contacted” continues to mean the configured Pipedrive contacted tags. Call files contain only activity already fetched, not an automatic new Aircall pull. Optional email attachment selections retain their existing behavior.

## SendGrid authentication troubleshooting

Use **Settings → Save & check SendGrid (no email)** after entering a new key. This saves the current form values and calls the same Mail Send endpoint using SendGrid sandbox mode. A successful check returns HTTP 200 and requests no delivery; it does not prove inbox delivery or all sender/account sending checks. The normal **Send test email** button remains a real send to the address you enter. No real emails are sent automatically by a connection check.

The Settings and Test email screens show the endpoint and a SHA-256 fingerprint of the saved key, never the key itself. A changed key produces a different fingerprint. Copying surrounding whitespace, a Bearer prefix, or matching outer quotes is handled; embedded whitespace is rejected locally. Keys are not shortened to a fixed length.

For **Maximum credits exceeded**, the blocker is the account’s sending allowance or billing state, even though SendGrid returns HTTP 401. Full key permissions do not increase credits. Check **Settings → Account Details → Your Products** for the **Email API** plan, usage/reset date, trial status, and successful payment. Wait for the applicable reset or resolve the plan/billing restriction; upgrading Marketing Campaigns does not increase Email API credits. If an active paid Email API plan has remaining credits but still fails, contact SendGrid support.

For other **HTTP 401** errors, read the **SendGrid says** detail before creating another key:

- Use the complete secret displayed once at creation in **SendGrid Settings → API Keys**. The API Key ID/name shown later in the list is not the secret. Twilio SMS account credentials cannot authenticate SendGrid Mail Send.
- Enable **Mail Send** access on the key. Extra account-management permissions are not needed for this diagnostic.
- Choose **US** for the global endpoint (`api.sendgrid.com`). Choose **EU** only for an EU regional subuser (`api.eu.sendgrid.com`). The app never retries against another region automatically.
- If SendGrid reports a disabled/restricted account or IP access issue, resolve that account restriction. A new key does not resolve an account-level restriction.
- If the error says the authorization grant is invalid, expired, or revoked, confirm the full current secret was saved in this app and belongs to the selected regional account.

**HTTP 403** has separate guidance for permission, verified sender, and access restrictions. The provider's structured message is displayed with recognized secrets redacted; HTML/error page bodies are not shown. You can share the displayed error message for troubleshooting, but never share the API key. Request failures are not automatically retried; ambiguous real-send failures still require checking SendGrid before resending.

References: [API keys](https://www.twilio.com/docs/sendgrid/ui/account-and-settings/api-keys), [sandbox mode](https://www.twilio.com/docs/sendgrid/for-developers/sending-email/sandbox-mode), [Mail Send endpoints](https://www.twilio.com/docs/sendgrid/api-reference/mail-send).

## Email

Open **Email & scheduling**.

**Test email** sends a generic message to the one address you enter. It includes no client data. Use it after configuring your verified sender and SendGrid key.

**Send current report** sends the loaded report to the explicitly entered recipients. Choose all or some UTM groups. Summary CSV is always attached; individual people, fetched call activity, and transcript text are opt-in attachments. Each recipient gets a separate personalization so other recipient addresses are not disclosed in a shared To field.

The API is SendGrid v3 `POST /mail/send`. It uses the same SendGrid service as the Python-library example in the request, implemented directly over HTTPS to avoid an extra SDK dependency. The app does not use Twilio's SMS API.

A 202 response is recorded as **accepted by SendGrid**, not confirmed delivery. Use SendGrid Email Activity or your inbox to verify delivery. Requests that time out or return a server error are marked uncertain and are not automatically retried because the first request may already have been accepted. Definite rejection codes are recorded as failed. The application limits the serialized message to 25 MB to leave room under SendGrid's whole-message limit.

No live email has been sent as part of building/testing this package. The test suite mocks SendGrid.

## Scheduling

Create a live report first and choose its tag/grouping rules. In **Schedules**:

- Name the schedule and enter recipient addresses.
- Choose Daily, Weekly (weekday), Monthly (day), or Every N hours.
- Set timezone and local send time. Every N hours uses elapsed time; the first run is one interval after the anchor.
- Select Yesterday, Last week, Last month, or Previous N complete days as the report period.
- Select all or particular UTM groups. All groups also includes new UTM groups appearing in later runs.
- Select whether to include people, Aircall calls, and transcript text.
- Aircall activity can stop at the report period end or continue through send time.
- Check **Enable automatic sending** and save, or save paused.

Each scheduled run fetches fresh Pipedrive people and tag state. It uses saved field mappings and filters, not whatever is currently open in your browser. Calls/transcripts are fetched only for matching people in the scheduled UTM groups. Replacing a schedule deliberately uses the current report's tag/grouping rules, which is stated on the form.

Schedules are saved in SQLite and survive restarts. A transactional job claim prevents two workers from sending the same scheduled occurrence. The next occurrence is advanced before preparation. Pausing or editing a schedule during preparation cancels its pending send. A crash/timeout is not automatically retried. Inspect stale preparing/sending or uncertain jobs in Send history before manually resending.

**The machine must be awake with a worker running.** Closing the browser is fine. Closing the launcher terminal stops the worker. The sidebar shows a heartbeat. If the worker was offline, it sends one fresh catch-up report at restart and skips accumulated missed occurrences. It does not backfill one email for every missed week. Monthly dates beyond month length use the last day. A nonexistent spring-forward send time shifts forward through the DST gap; a repeated fall-back time uses the first occurrence only.

### Always-on worker on Windows

For scheduling independent of the UI, use Windows Task Scheduler:

1. Create a task under your own Windows account, triggered at logon (or another appropriate trigger).
2. Program/script: the full path to `asb_utm_reports\.venv\Scripts\python.exe`.
3. Arguments: the full quoted path to `asb_utm_reports\scheduler.py`.
4. Start in: the full `asb_utm_reports` folder path.
5. Configure restart on failure. Disable the generic time-limit setting that would stop a long-running worker. Configure power/sleep behavior to match your reporting needs.

Alternatively trigger `scheduler.py --once` every few minutes. Each run handles at most one due schedule, so use a frequent trigger if you configure several. Running more than one worker does not duplicate a claimed occurrence. The UI can then be launched separately with `python -m streamlit run app.py`.

The app does not install an OS scheduled task automatically, deploy a server, or enable any schedule on your computer during installation.

## Repository and tests

The GitHub connection was inspected. `CalebMalcarne/Contact-Tool` is a different contact-selection application, so it was not modified. This package is ready to add to the intended repository once that repository is identified. Do not commit `.data`, credentials, or client reports.

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest pypdf
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests cover UTM selection and denominators, native/custom tag parsing, person-creation date filters, pagination, phone matching, transcript availability, scheduler timezone behavior, atomic claims, pause-before-send, SendGrid payloads, and dashboard navigation. Real account credentials were not available; verify the live field mappings and one known week before relying on production totals.

## Code organization

- `app.py`: dashboard, settings menu, export/email/schedule controls.
- `api.py`: read-only Pipedrive/Aircall clients, pagination, rate-limit retries.
- `core.py`: periods, UTM grouping, tag parsing, phone normalization, safe CSV.
- `service.py`: shared interactive/scheduled report pipeline and transcript retrieval.
- `pdf_summary.py`: paginated PDF summary for full-report ZIPs (ReportLab, generated in memory).
- `mailer.py`: SendGrid payload construction and one-attempt sending.
- `storage.py`: encrypted settings, schedule persistence, transactional job claiming.
- `scheduler.py`: recurrence calculation, fresh report generation, background worker.
- `launch.py`: starts UI and worker together.
- `demo.py`: synthetic example records.

## Official API references checked October 13, 2026

- Pipedrive persons: https://developers.pipedrive.com/docs/api/v1/Persons
- Pipedrive field schema: https://developers.pipedrive.com/docs/api/v1/PersonFields
- Pipedrive v2 custom field syntax: https://pipedrive.readme.io/docs/pipedrive-api-v2-migration-guide
- Aircall search/transcripts: https://developers.aircall.io/api-references
- SendGrid Mail Send: https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send
- SendGrid limits: https://www.twilio.com/docs/sendgrid/api-reference/mail-send

