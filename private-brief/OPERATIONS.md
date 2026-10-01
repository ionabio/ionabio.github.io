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
Only the current Brussels date is accepted. Preparation may precede 08:00;
publication is due at or after 08:00. Verified sources and deal checks expire after
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
through validation and commit. A published date is immutable through this API/CLI.
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
