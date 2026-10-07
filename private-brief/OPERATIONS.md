# Cloud-operated private brief

This service is separate from the existing GitHub Pages website. Keep runtime data,
drafts, source text, passwords, tokens and push subscriptions outside this public repo.
Do not put them in Actions inputs, artifacts, release notes, issues or logs.

## Private publishing contract

Base URL: `https://brief.nabi.be/api/publisher/v1`.
Only `Authorization: Bearer <dedicated publishing token>` authenticates this API.
It rejects Cookie and Origin headers; browser sessions never grant publisher access.
The existing browser login, server-side sessions, Origin checks and CSRF remain intact.
Every response (including errors and private drafts) is `Cache-Control: no-store, private`.
Cloudflare's existing brief-host cache bypass must remain configured.

| Method/path | Permission | JSON request | Result |
|---|---|---|---|
| POST `/drafts` | prepare | `{"brief":<supplied bundle>,"baseHash":null or current hash}` | date, exact draft hash, status=draft |
| GET `/drafts/YYYY-MM-DD` | review | none | date, hash, approved, private original brief |
| POST `/drafts/YYYY-MM-DD/approve` | approve | `{"hash":"<reviewed hash>"}` | approval of exactly this hash |
| POST `/drafts/YYYY-MM-DD/publish` | publish | `{"hash":"<approved hash>"}` | published, unchanged or not_due |
| GET `/status/YYYY-MM-DD` | status | none | hashes, approval, publication time, aggregate notification states |
| POST `/notifications/YYYY-MM-DD` | notify | `{"hash":"<published hash>"}` | providerAccepted, attempted, phoneDelivery=unknown |

The source bundle follows `fixtures/synthetic.json` and the existing README contract.
The server supplies the Europe/Brussels clock; clients cannot supply server time.
Only the current Brussels date is accepted. Preparation may precede 06:00;
publication is due at or after 06:00 Brussels local time (including DST). Verified sources and deal checks expire after
24 hours; naive/future timestamps, oversized text, nonfinite numbers, malformed
vocabulary/puzzles, missing evidence and source-hash mismatches are rejected.
Imported source text is limited to 12,000 characters per article without silent truncation.

Request bodies are limited to 1,000,000 bytes and source lists to 100 items.
The six scopes are the only supported permissions; there is no shell, file, media
upload, identity-management, deployment or general administration operation.
Machine credentials cannot read browser archives, subscriber endpoints or secrets.
401 means invalid/expired/revoked identity; 403 means insufficient scope or browser
headers; 413 means oversized input; 415 means wrong content type; 429 means rate limit.
Malformed JSON/shape/hash is 400. Invalid, stale or concurrently changed content is
409 with a generic reason that contains no personal data. Transport/5xx failures
can be retried with the same version, subject to these limits.

Draft hash = SHA256 of UTF-8 `json.dumps(bundle,sort_keys=True,ensure_ascii=False)`.
Use the server-returned hash instead of reimplementing serialization.
Changing a draft invalidates approval. `baseHash` implements compare-and-swap:
initial creation requires null; later edits require the reviewed current hash.
An identical retry returns the same hash and preserves approval. Approval and
publication share the exact saved version; publication holds an SQLite writer lock
through validation and commit. A published date is immutable through this API and the standard `publish` CLI command.
Retries of that version return unchanged; a different version for the date is denied.
The lower-level `pipeline.run` remains a library helper for synthetic tests, not an
unrestricted machine endpoint.

Notifications require a successfully published exact version. Each date/subscription
is durably claimed before network I/O, using an atomic insert. Duplicate/concurrent
requests cannot send twice. An ambiguous timeout/crash is never automatically retried;
this can lose an alert. Provider acceptance is not proof that a phone displayed it.
`sent` is retained only as a legacy alias for providerAccepted. Payload contains only
the date; the worker displays generic Dutch wording. Browser permission is requested
only when the user taps the opt-in button. HTTP 404/410 subscriptions are removed.

## Approved identity and protected cloud setup

Creating identities needs specific user approval. The local-only administrator tool:

```
BRIEF_DATABASE=/var/lib/nabi-brief/brief.sqlite3 /opt/nabi-brief-releases/current/.venv/bin/python -m brief.identity create --id dottie-YYYYMMDD --expires EXPLICIT_TIMEZONE_AWARE_ISO_EXPIRY --token-file /var/lib/nabi-brief/dottie-token.private
```

Use the `nabi-brief` service account. The token is random, stored only in that new
mode-0600 file; the database stores its SHA256. The tool never prints it. Expiry must
be at most 30 days away. By default, it gets only prepare/review/approve/publish/status/notify.
`--scopes` can narrow this list. Rate limit is 60 requests/minute/identity, persisted in
SQLite; invalid authentication has a shared 120/minute budget. To revoke immediately:

```
BRIEF_DATABASE=/var/lib/nabi-brief/brief.sqlite3 /opt/nabi-brief-releases/current/.venv/bin/python -m brief.identity revoke --id dottie-YYYYMMDD
```

Chosen supported credential path: **Codex Cloud network secret**, key
`BRIEF_PUBLISH_TOKEN`, destination **only `brief.nabi.be`**, HTTPS port 443.
Personal vault supports restricting the value to a selected private cloud environment.
The task receives a placeholder and the cloud proxy substitutes the credential only
for the allowed service. Raw credentials need not be in task files or commands.
This is documented at https://learn.chatgpt.com/docs/environments/cloud-environments.
It is NOT automatically available just because a browser is logged in. Verify the
actual dottie environment exposes Network secrets/Personal vault before creating an
identity. No custom MCP/OAuth server is claimed or configured by this code.

Secure setup after approval: in Settings > Codex Cloud > Environments, open dottie's
private environment, request `BRIEF_PUBLISH_TOKEN` under Network secrets, allow
`brief.nabi.be`, then save the personal value in Personal vault for that selected
environment. Enter it in the protected UI using a secure local terminal/password
manager; never paste it into chat. Republish and start a fresh cloud task. After a
successful test, remove the one-time token transfer file. Never use a public/shared
environment or supply the browser password/session secret. If this facility is absent,
stop credential setup and report it; an OAuth MCP connection would be a separate project.

Cloud task commands (private source/review files outside checkout, no verbose HTTP):

```
python -m brief.cloud_client status --date YYYY-MM-DD
python -m brief.cloud_client prepare --date YYYY-MM-DD --input /private/input.json
python -m brief.cloud_client review --date YYYY-MM-DD --output /private/new-review.json
python -m brief.cloud_client approve --date YYYY-MM-DD --hash REVIEWED_HASH
python -m brief.cloud_client publish --date YYYY-MM-DD --hash APPROVED_HASH
python -m brief.cloud_client notify --date YYYY-MM-DD --hash PUBLISHED_HASH
```

Only statuses/hashes print. Use `--base-hash CURRENT_HASH` when changing an existing
draft. The client refuses redirects and repository-local private review output.
Review exports include private source text; read them only inside the private task.
Do not notify when publish reports not_due or fails. Verify status first if transport
failed, then retry the same version. A phone delivery claim needs a user observation.

## Controlled code deployment

`brief-ci.yml` tests on a GitHub-hosted ARM64 runner. `brief-release.yml` builds a
versioned ARM64/Python 3.13 archive with pinned dependencies and an offline wheelhouse.
It copies only code/assets/templates, requirements and BUILD.json, never production
files or fixture content. The approval manifest binds commit, SHA256, run ID and a
24-hour validity window. GitHub Sigstore attests it. Releases are `brief-production-RUN_ID`.

Cloud trigger available in this session: the connected GitHub tools can create a
branch, update a repository file, create a PR and merge it. No workflow-dispatch or
deployment-approval tool is exposed. Dottie can therefore propose a code-only change
to `private-brief/deploy/production.json`:

```
{"commit":"EXACT_40_CHARACTER_REVIEWED_MAIN_COMMIT"}
```

Merge that request into main to start the workflow. An existing reviewed commit on
main is mandatory. The user must approve the waiting **brief-production** job using
GitHub's protected environment UI. Manual workflow_dispatch is an additional human/CLI
route, not an assumed dottie connector capability. Trigger permission alone cannot
publish content; the publishing identity cannot deploy code. No new GitHub PAT is needed.

Before first production release, specifically approve creating GitHub environment
`brief-production`, a required human reviewer (ionabio for the current sole-owner repo),
main-only deployment branch policy and variable `BRIEF_RELEASE_APPROVAL_ENABLED=true`.
For a sole-owner account, self-review must remain permitted because the connector's
request and the human approval use the same GitHub account. Dottie has no environment
approval tool. If a separate trusted human reviewer is available, enable prevention
of self-review and use that reviewer instead. No broad GitHub token is supplied to dottie.
The workflow refuses release signing if the reviewer gate is absent.

The Pi polls GitHub outbound; no public SSH, router forwarding, Windows host or general
self-hosted runner. Specifically approve the one-time bootstrap from the reviewed
checkout: `sudo sh bootstrap-updater.sh` in private-brief/deploy. It installs a pinned
GitHub CLI 2.102.0 with vendor SHA256 and root-owned updater policy, creates a release
directory/current symlink and service overrides. It preserves the old `/opt/nabi-brief`
tree, environment, tunnel and private database. It enables only the code updater timer.

The release updater requires `/usr/bin/python3.13`, including `venv` and `ensurepip`.
Bootstrap and updater both check this before persistent changes; bootstrap never
silently installs or substitutes another Python version. The updater service,
candidate venv and authentication verifier all explicitly use Python 3.13, matching
the CPython 3.13 wheelhouse. Install that interpreter through reviewed host maintenance
first if it is absent; the older general installation guide does not set this release
updater's interpreter compatibility policy.

The updater checks cryptographic signature, exact main-branch workflow certificate,
attested source commit, locally pinned workflow file hash, manifest expiry, monotonic
run ID, archive SHA256, ARM64/Python target and safe archive members. Workflow policy
changes require another local administrator review/update of its root-owned pin.
No GitHub credential is stored on Pi; bundled attestations permit public verification.
It serializes using flock, stages a root-owned release, installs dependencies offline,
runs production-mode auth/CSRF regression with isolated synthetic state, takes a private
SQLite backup, switches current atomically, restarts and verifies live health, deployed
commit and anonymous API denial. On failure it restores the previous code and restarts.
Schema additions are backward compatible; rollback never overwrites private writes.
Preflight/deployment failure requires a newly approved release run; rejected run IDs are
not repeatedly applied. Keep disk capacity monitored; backups/releases are retained
until a separately reviewed retention policy is implemented.

Release discovery follows all API pages and examines candidates in descending run-ID
order, above the durable highest-run floor. Each candidate must pass both attestation
checks, signed manifest/date validation, local workflow policy and archive digest
verification before selection. Missing/invalid higher releases are skipped without
advancing that floor; they cannot hide a lower approved eligible release. After a
verified release enters staging, deployment failure still consumes its run ID and
requires a newly approved run; it never falls back to an older deployment.

Paths and units after bootstrap:

- `/opt/nabi-brief-releases/current`: active pinned release; legacy code retained in `/opt/nabi-brief`.
- `/usr/local/lib/nabi-release`: root-owned updater/verifier/workflow pin.
- `/var/lib/nabi-release/state.json`: public version/status metadata, private backup files beside it (0700 parent).
- `/var/lib/nabi-brief/brief.sqlite3`: existing private state, additive tables only.
- `/etc/nabi-brief/environment`: existing login/session settings; untouched by code releases.
- `nabi-brief.service`, `nabi-cloudflared.service`: existing app/tunnel.
- `nabi-release-update.service`, `nabi-release-update.timer`: approved code polling.
- `nabi-publish.service`, `nabi-publish.timer`: publication timer stays disabled.

## VAPID and verification

VAPID key creation requires separate explicit approval. Reviewed helper
`deploy/setup-vapid.py` creates a P-256 identity only if none exists, saves it in the
root-owned mode-0600 environment file, sets subject `https://brief.nabi.be`, and restarts
the app. It prints no key and sends no alert. After approval/configuration, sign in on
the phone at https://brief.nabi.be and tap the notification button. On iPhone, use the
installed Home Screen web app. Phone permission/opt-in is the user's separate choice.

Required validation: full synthetic suite on ARM64, including Linux rollback;
invalid/expired/revoked/scoped auth; oversized/stale/malformed input; stale evidence;
concurrent edits; exact-hash approval; duplicate/concurrent/ambiguous notification;
private no-store response; browser cookie/CSRF/logout; anonymous page/API/media denial.
CI includes a credential-free HTTPS health/anonymous-denial probe from the cloud,
independent of Windows. Authenticated cloud status and a signed outbound release
deployment still require actual setup/testing; neither can be inferred from local tests.
Do not enable any daily schedule until live source access, cloud operation, publication
and phone opt-in are verified. Do not report today's brief published unless status proves it.

## Read-only OfferHunter source handoff

`GET /api/publisher/v1/sources/offerhunter` uses the existing bearer `review` permission and grant expiry/revocation/rate checks. Browser cookies and Origin remain rejected. The reader opens only `/var/lib/nabi-offerhunter-summary/top-offers.json`; request arguments cannot choose another file. No credential, grant, timer, refresh, delivery acknowledgement or publication action is added.

The response is `ready`, `stale` or `unavailable`, with the current Brussels `date`; waiting states include `retryAfterSeconds=10`. Ready requires schema version 2 for `daily-brief`, timezone-aware preparation/refresh timestamps from today's 06:00 Brussels onwards, not in the future. Missing, malformed, oversized or inconsistent snapshots fail closed. Selected returning offers precede top offers and deduplicate; public product/provenance fields are allowlisted and private source records/watch IDs/unknown fields are removed. Coverage retains blocked/partial states; invalid or expired records increment `rejectedRecords`. Upcoming records are separate and never contain `briefDeal`. Current-price alerts remain labelled current prices, not dated promotions.

Only active exact-variant offers with supported retailer/pack/validity/provenance receive a `briefDeal` candidate. Action uses official Belgian product URLs; family cards retain their conditions but receive no invented unit comparison. Feed these candidates into the ordinary current-date `deals` input and re-prepare/review/approve/publish through the existing exact-hash flow. The shared freshness and date validation still applies; this endpoint never changes the stored draft or publication. Keep actual summary data out of public source, CI artifacts and logs.

The Pi's existing OfferHunter timer owns refresh. The cloud workflow reads/waits for its fresh summary and does not create another refresh timer. Exposing `get_offerhunter_summary` in the existing owner-only Sites bridge requires a code/schema update in that bridge's source; it must call this fixed GET through its existing bearer transport and enforce the existing owner identity check. No new permission or token is needed. The five publishing actions and status response remain unchanged.

## Prepared phone enrollment and automatic renewal (disabled)

This extension is a draft and is **not active in production**. Its runtime flag
`BRIEF_ENROLLMENT_ENABLED=true` must not be set until the bridge and backend use
this contract, hosted credential storage has been explicitly approved, and the
signed release has passed the existing human environment gate. The existing
Sites integration and owner-only audience are reused; Developer mode stays off.

A phone can visit `/connect` after normal portal sign-in, enter the code shown by
the owner-only Sites bridge, review the five scopes, and approve. This consent
never authorizes notifications, identity administration, server access or code
deployment. Never initiate a real enrollment while the owner is asleep, and never
approve an unsolicited code: the client identifier is public, so the owner must
verify that the code was requested in their existing private Sites connection.

The machine exchange uses RFC 8628 device authorization and the refresh-token
rotation guidance in RFC 9700. Only the fixed client identifier
`appgprj_6abe75379874819189b7c95bebf9e21c` is accepted. No client registration,
arbitrary scope, callback URI, destination URL or authorization redirect exists.
Requests must omit Cookie and Origin; the browser consent/revocation route keeps
normal login, exact Origin and CSRF protection. All responses remain private/no-store.

Fixed endpoint origin: `https://brief.nabi.be:443`. The bridge must disable redirects,
send `User-Agent: NabiBrief-cloud/1`, and enforce a 4 KiB request limit. All requests
below are `application/x-www-form-urlencoded`; duplicate or extra fields are rejected.

- POST `/api/publisher/v1/enrollment/device`: `client_id` only. Reply contains
  `device_code` (private), `user_code` (phone consent code), `verification_uri`
  (the fixed portal `/connect`), `expires_in=600`, `interval=5`.
- POST `/api/publisher/v1/enrollment/token`: `client_id`,
  `grant_type=urn:ietf:params:oauth:grant-type:device_code`, `device_code`.
  Poll no faster than interval; `slow_down` adds five seconds to the interval.
- POST the same token endpoint to renew: `client_id`, `grant_type=refresh_token`,
  `refresh_token`. Only one serialized request may use a refresh token.

Token response: `access_token`, `refresh_token`, `token_type=Bearer`, `expires_in`
(up to 900 seconds), `scope=approve prepare publish review status`,
`refresh_expires_at` (Unix seconds), `grant_id`, `grant_expires_at` (Unix seconds).
Access is at most 15 minutes; refresh expires after 30 days without successful
renewal; consent has an absolute one-year cap, disclosed on the phone page.
Successful renewal never extends the absolute cap. Annual reconnect, prolonged
inactivity, revocation or ambiguous failed renewal can still require phone consent.
Do not promise permanent operation without any future owner involvement.

The issuer stores only SHA256 hashes of random credentials. Consume/rotation and
family revocation are serialized with SQLite BEGIN IMMEDIATE. A used refresh
replayed by any caller revokes its entire grant and every associated access token.
Concurrent refresh therefore fails closed; the bridge must hold a durable single-
writer lock and persist the newly returned pair before issuing an API request.
A successful response lost during transport cannot safely be recovered: never
retry a possibly committed device exchange/refresh; mark reconnect_required.
Ordinary draft approval/publication exact-hash idempotency is unchanged.

Standard device errors are HTTP 400 with `error` authorization_pending, slow_down,
access_denied, expired_token or invalid_grant. Scope/media/size/header rejection
continues to use HTTP 400/403/413/415. Known device polling and renewal use separate persistent 120/minute budgets per
device and grant. Publishing uses a grant-stable 60/minute budget across rotation.
New device requests are limited to ten/hour per source, valid for ten minutes.
For the verified loopback-only Cloudflare Tunnel listener, enable
`BRIEF_ENROLLMENT_TRUST_CLOUDFLARE=true` to use Cloudflare-validated client IPs;
never enable this behind an untrusted proxy or a remotely reachable listener.
Revocation also cancels all pending and approved unredeemed device codes.
This fixed integration has one active grant; renewal replaces its access token.

The owner-only `/connect` page can revoke all grants immediately. Local administrator
`brief.identity revoke --id GENERATED_ACCESS_ID` also revokes that access token's
whole grant, so renewal cannot circumvent administrative revocation. Existing static
identities retain their original scope/expiry/revocation semantics.

**Hosted storage review remains required.** The Sites runtime secret setter cannot
be populated opaquely from an issuer response by the current tools. A private hosted
database protected by provider encryption at rest is a separate design choice from
the runtime secret facility originally requested. Database/editor access holders and
server code can retrieve its credentials. Do not activate that alternative without
explicit informed approval, do not call provider encryption application-level key
separation, and preserve owner-only access. Never expose tokens/device_code through
MCP results, chat, Git, source logs or public endpoint responses. The bridge may
return only consent code/link and safe connection status to its owner.

Required validation before activation: ARM64/Python 3.13 tests/package verification,
expiry/denial/CSRF/machine Cookie+Origin rejection, duplicate/oversized form rejection,
concurrent device redemption/refresh, replay family revocation, absolute/idle limits,
no plaintext credentials in issuer database, and live read-only cloud status first.
Do not publish a brief, send notifications or enable the daily publishing schedule
as part of enrollment validation.

Standards: https://www.rfc-editor.org/rfc/rfc8628.html and
https://www.rfc-editor.org/rfc/rfc9700.html .

## Explicit owner-requested same-day refresh

When the owner explicitly asks to replace today's content, use local administrator maintenance after the normal prepare, full draft review and exact-hash approval. The machine/browser APIs remain unchanged and cannot replace an already published date. No new publisher scope, identity, endpoint or notification permission is added.

1. Read the current `publishedHash` and `draftHash` using the existing status API. Prepare the improved, genuinely sourced current-day bundle with the correct `baseHash`; do not treat tomorrow's promotions as current offers.
2. Review the entire exported draft, verify source scope/freshness and the server-returned hash, then approve exactly that hash through the existing workflow.
3. Run the local command as the existing `nabi-brief` account, with both reviewed hashes explicitly supplied:

```
python -m brief replace-published --database /var/lib/nabi-brief/brief.sqlite3 --date YYYY-MM-DD --hash EXACT_NEW_APPROVED_HASH --published-hash EXACT_CURRENT_PUBLISHED_HASH
```

The current Brussels date and 06:00 gate apply. A changed/unapproved draft, mismatched canonical hash, stale source or changed current publication is rejected. The original publication is saved in the private `brief_revisions` table and the new approved payload becomes the current date's user-facing brief in one transaction. A failure rolls back both steps. Repeating the exact replacement returns `unchanged` without another revision. Source/draft content and snapshots remain in the private database; only statuses/hashes print.

No notification is sent or delivery state reset. `--notify` is explicitly refused for this command. Existing API status automatically reports the active replacement's `publishedHash`, and the owner archive URL for that date reads the active payload. This command is for an explicit owner-requested correction, not automatic recurring replacement or a way to skip review.
