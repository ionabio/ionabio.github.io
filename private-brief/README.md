# Private morning brief for nabi.be

The existing root website remains GitHub Pages. This is a portable private companion, with a standard Python/Flask server, SQLite state, and Web Push. User chose Raspberry Pi deployment; no account, DNS, network setting, credential or production release has been changed. A reverse proxy is not an external connectivity solution: VPN or outbound tunnel remains to be chosen. No Cloudflare dependency.

## Try the synthetic demo

From `private-brief`, install `requirements.txt` in a virtual environment and run:

```
python -m brief.demo
```

Open http://127.0.0.1:8080 and sign in with **demo-only**. This is loopback-only, synthetic, temporary storage. It deliberately allows HTTP cookies only in TESTING mode. Never expose this demo to the internet. Stop with Ctrl+C. Production refuses HTTP and repository-local storage. Mobile layout uses a scrollable card feed, expandable puzzle answers and original-source links. No destructive swipe gestures are required.

## Daily flow and agent interface

1. Agent invokes cached ingestion helpers through an approved connector or local job; chat's Gmail/calendar access is not server OAuth access. Save normalized source input outside the checkout. Each source records `status=verified|unavailable`, `checkedAt`, and `items`. Verified timestamps expire after 24 hours. Calendar/todos need a fresh read, not remembered chat items.
2. Scripts normalize/de-duplicate. `brief.ingest` handles editorial MIME/HTML, a candidate RSS feed, task and calendar records. Tracking redirects are never exposed as article URLs. Unresolved canonical URLs remain null. Actual network adapters require approved OAuth/source access and are not silently enabled.
3. AI reviews the private cached text, selects articles (`include: false` omits one), orders them, writes ORIGINAL short Dutch summaries and evidence-grounded vocabulary/puzzles, and saves `reviewedLanguage` against SHA256 of the accessible text. Questions require an exact supporting span in `evidence`. Evidence is removed before publication. This checks provenance, not semantic truth: AI still has editorial responsibility. Read full articles at publisher, never copy full articles/translations/photos into this app.
4. `python -m brief prepare --input /var/lib/nabi-brief/input.json` persists a draft and returns its hash. `export-draft --date YYYY-MM-DD --output /var/lib/nabi-brief/draft.json` lets an agent edit it. Re-prepare to save changes; every content change invalidates approval.
5. `python -m brief approve --date YYYY-MM-DD --hash HASH` approves exactly that draft. This is the AI editorial checkpoint authorized by the recurring workflow, not an extra daily permission demand. User can ask AI to change preferences/selection at any time.
6. The authorized recurring cloud workflow targets 06:00 Europe/Brussels. The server accepts publication from 06:00 local time; the legacy systemd publication timer stays disabled. Only an approved current-date draft publishes. Transactions prevent overlapping generation. A crash rolls back publication. Repeated identical input is a no-op. Language outputs cache by source text, scope and learning preferences.
7. Notification attempts follow successful publication only. Date-only encrypted payload; generic Dutch lockscreen wording. Tap opens authenticated page. No calendar, email or task content in push. Network delivery is at-most-once attempted per date/subscription, not guaranteed exactly once. Ambiguous push failures are not retried, avoiding duplicate alerts; failed publication retries at bounded timer times. Revoked endpoints are removed on HTTP404/410. Explicit unsubscribe removes server keys and browser subscription. Permission requests happen only on button clicks.

CLI stdout contains statuses/hashes only. All real inputs, drafts, database, subscriptions and source text stay in `/var/lib/nabi-brief` with restricted permissions, never public Git, Pages, CI or public artifacts. `BRIEF_DATABASE` is required; CLI and production server reject storage inside this repo.

## Input and learning contract

See `fixtures/synthetic.json` for source statuses and `preferences.example.json` for conversationally editable defaults (B2, nl-BE, article counts, topics, weekday/weekend style). Preferences are guidance to AI; scripts enforce limits on articles and language output. No AI provider is configured, no automatic AI spend. Reviewed agent output is imported locally; a future provider adapter must enforce hard token/cost/time limits before activation. Two small bounded calls are the pipeline default for optional generators; imported reviewed output does not call a provider.

News record: publisher, newsletterName, receivedAt, publishedAt|null, headline, canonicalUrl|null, contentScope=headline|teaser|full_authorized, authorizedText. `full_authorized` requires accessVerified. Newsletter receipt never proves subscriber login. Original summaries/exercises stay within accessible scope; do not answer an article question if only its headline/teaser was accessible. `reviewedLanguage={sourceHash,summaryNl,vocabulary:[{word,meaning,example?}],puzzle:[{question,answer,explanation,evidence}]}`. Missing language output produces an explicit unavailable message, not fabricated learning content.

Confirmed editorial sender contracts: dshelpdesk@mail.standaard.be (weekly Wednesday Helpdesk), nieuwsblad@mail.nieuwsblad.be (Gezondheid, cadence unverified). Exclude service marketing senders. HTML parser discards script/style/nav/footer/comments/images and all link attributes. This is a conservative extraction helper, not a guarantee that every newsletter template footer is recognized. Agent must review extracted text. Tokenized `interactief.*` links are rejected; canonical links need actual authorized resolution/exact-title verification outside this helper. DS feed access remains unverified. Nieuwsblad RSS is a candidate; helper fails closed when unavailable.

Deals: retailer, product, real price, quantity, kg/l/stuk, validFrom/validTo, sourceUrl, checkedAt, availability, conditions; referencePrice optional and must be verified. Unit price is calculated; no reference price means no discount claim. Belgian AH and Delhaize source allowlists are extensible in `brief.adapters`. Empty/unavailable feeds show an honest fallback. Fixtures intentionally contain no pretend live offers. Future live adapters must respect retailer terms, rate limits, geography and membership conditions; no scraping/paywall bypass is implemented.

## Security and deployment

`brief.web.create_app` requires HTTPS origin, session secret, scrypt password hash and a private database path. Server-side session identifiers are random, stored hashed in SQLite, expire after 12h and are revoked on logout. Cookies Secure/HttpOnly/SameSite Strict. Every page, asset, archive/API and subscription endpoint requires login; only login and health are public. Mutations require exact Origin and CSRF token. Login has persistent rate limiting. Responses no-store/private, CSP forbids external scripts/frames, UI inserts text rather than HTML. Worker has no cache/fetch handler. Push destinations have allowlisted hosts to prevent arbitrary SSRF.

Pi plan: Python3.11+, dedicated unprivileged account, `/opt/nabi-brief` read-only app, `/var/lib/nabi-brief` private persistent state, root-owned environment file, systemd service bound to loopback, Caddy reverse proxy at chosen HTTPS private hostname. Templates in `deploy/` are reviewable, not deployed. `install.sh` does not generate credentials, enable services, configure networking or request certificates. Configure approved connectivity first. VPN-only works if phone VPN reaches hostname and HTTPS is valid; managed tunnel adds provider dependency; an outbound tunnel to your own publicly reachable server requires that server. DNS ownership alone cannot traverse home NAT. No inbound web forwarding is assumed.

Before live: secure SSH connection, Pi OS/hardware/storage and reachability, approved connection route/TLS/DNS, approved login/session/VAPID secrets, verified real source access, login/logout and anonymous page+API checks from outside network, phone permission-denied/revoked/unsubscribe and push tests, backups/retention and disk-space monitoring. Keep existing GitHub Pages and PR1 untouched. Do not merge or deploy the public repo without final scope approval.

The recurring cloud workflow targets 06:00 Brussels, using the Europe/Brussels timezone rather than a fixed UTC offset. NTP and an online Pi are required; exact wall-clock delivery cannot be guaranteed during outages. Persistent timer catches up after downtime but requires a current approved fresh draft. Other Spendee/nightly automations are outside this service.

## Verification

`python -m unittest discover -s tests -v` exercises authentication, cookies, CSRF, rate limiting, DST, source freshness, approval invalidation, caching, scope, canonical sanitization, unit pricing and notification dedup/unsubscribe. Synthetic only. No live Gmail/OAuth, SSH deployment or phone push was tested at authoring time. Browser QA and runtime dependency installation are separately reported in the delivery.

## Official references

- [GitHub Pages is static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Google Web Push subscription guide](https://web.dev/articles/push-notifications-subscribing-a-user)
- [Google Web Push protocol](https://web.dev/articles/push-notifications-web-push-protocol)
- [Nieuwsblad RSS terms](https://www.nieuwsblad.be/nieuws/rss-feeds-en-nieuwstickers/54272636.html)

Authoring QA: 18 synthetic tests passed; Chrome headless verified mobile (390×844) and desktop (1280×900), no horizontal overflow, login/logout, expandable answer, disabled unconfigured push and no JavaScript errors. Actual Pi/Linux service/TLS/network/OAuth and phone delivery remain untested.

Dedicated private deals page: /deals, sharing the authenticated archive and validated data. A mocked Chrome denied-permission test also passed: controls disabled and no implicit permission prompt. Actual phone delivery remains untested.

## Multimedia

News cards now have original local topic illustrations (reading, health, science), labelled “Illustratie · geen nieuwsfoto”. Set article `media={kind:"illustration",theme:"news"|"health"|"science"}`. No remote requests, image generation spend, or subscriber photos are involved. Set `BRIEF_MEDIA_DIR` to a private directory outside the checkout for optional photos. `brief.media.import_image` imports PNG/JPEG/WebP files using a content hash; SVG uploads are refused. Article photo metadata requires `kind:"photo", imageId, extension, rightsVerified:true, credit, license, alt`. Photos are served only after server authentication, with no-store responses, and credits shown on the card. Verify permissions yourself/through the approved source workflow; metadata is an assertion, not an automated copyright determination. Never download/reuse publisher photography merely because it is visible in an email or on an article page. No image URLs are hotlinked and browser requests reveal no interests to external image hosts.

User explicitly authorized merging the initial skeleton; PR2 was merged on 2026-09-30. Pi deployment remains pending secure SSH/connectivity/TLS/source setup.

Language limits: missing, null or empty values use defaults (3 articles, 6 vocabulary items, 3 questions). Integer zero disables that section. Maximums are 10 articles, 12 vocabulary items and 5 questions; invalid values reject publication. Reviewed exercises validate hashes and evidence against the complete retained source (up to 12,000 characters); only optional provider input is shortened to 6,000 characters. Lower counts trim reviewed outputs and preference changes invalidate the language cache.

## Cloud publishing and approved releases

See [OPERATIONS.md](OPERATIONS.md) for the new scoped HTTPS contract, protected Codex Cloud network-secret setup, exact-hash approval/publication, at-most-once notification requests, reviewer-gated ARM64 releases and outbound Pi updater. Installation and credential approvals remain explicit. The daily publication timer remains disabled until live operation is verified.

## Daily reading and weekly offers

The owner dashboard puts the supplied agenda and dated tasks first, labels incomplete source coverage, and keeps article illustrations and language exercises behind disclosures. Archive dates stay selected when moving between the daily brief and offers. Headline-only input stays labelled headline-only; the layout cannot supply missing article text or source data.

Offers use their inclusive `validFrom`/`validTo` dates independently of `checkedAt`. A weekly promotion can appear in each daily brief while valid. Reuse the verified product, total bundle price/quantity, validity, retailer source and membership conditions; recheck the official Belgian source before carrying it into the next daily input and update `checkedAt` only after that check. Both source and offer checks must remain within the existing 24-hour freshness window. Remove expired offers and do not publish future offers early. An unavailable source is unknown, not proof that stores have no promotions.

For folder-based offers, verify the PDF cover dates: an alias can serve last week's folder while the retailer's next-week link serves the new edition. Retain the exact PDF URL and page evidence in the private source bundle/draft, with the allowed retailer page as `sourceUrl`. Check stock, online/in-store price differences and loyalty/multibuy conditions. Do not alter an already published date merely to add offers. No additional schedule, scraping service or credentials are introduced by this UI change.
