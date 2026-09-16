# CLAUDE.md — franklin-radicale

Self-hosted CalDAV/CardDAV for Franklin Digital. **DAYTRADE-778.**

This repo is a **fork of [Kozea/Radicale](https://github.com/Kozea/Radicale)**, not
a greenfield project. Treat upstream code as read-only.

| | |
|---|---|
| origin | `ssh://git@github.com/Franklin-Digital/Radicale.git` |
| upstream | `ssh://git@github.com/Kozea/Radicale.git` |
| checkout (mac-pro) | `/home/Franklin/franklin-radicale` |
| forked at | `085f4484` (upstream `master`, 2026-09-15) |

## Branch convention — MANDATORY

**Every Franklin customization lives on a branch prefixed `franklin_*`.**
`master` tracks upstream and MUST stay mergeable from it.

* First/current production branch: **`franklin_1.0prod`**
* Never commit Franklin config to `master` — it makes every upstream pull a
  conflict, and the value of forking rather than vendoring is that
  `git merge upstream/master` stays cheap.

## ⚠️ BLOCKER: `calendar.franklindigital.ai` cannot resolve — the domain is not registered

Measured 2026-09-15:

```
dig +short NS franklindigital.ai      -> (empty, no delegation)
whois franklindigital.ai              -> Domain not found.
whois franklinfinancial.ai            -> Cloudflare, created 2026-05-02
```

`franklindigital.ai` is **unregistered** — not merely undelegated. Nobody owns
it. The Cloudflare tunnel on mac-pro (`/etc/cloudflared/config.yml`) routes
**only `franklinfinancial.ai`** hostnames; there is no `franklindigital.ai`
ingress rule and there cannot be one until the zone exists in the account.

Two ways forward, Sal's call:

1. **Register `franklindigital.ai`** and add it to the same Cloudflare account,
   then add the ingress rule below.
2. **Use `calendar.franklinfinancial.ai`**, which works today with no purchase.

Everything else in this repo is independent of which is chosen — only the
`hostname:` line and the Access application change.

## Deployment

Docker container managed by **systemd**, never `docker --restart` flags
(house rule; see root `CLAUDE.md`). Infra source of truth for units and
compose is **`franklin-infra`** — this repo holds the Radicale *config*, that
repo holds how it runs.

Tunnel ingress (add to `/etc/cloudflared/config.yml`, which is deployed by
`sudo cp` then `sudo systemctl restart cloudflared`):

```yaml
  - hostname: calendar.franklinfinancial.ai   # or .franklindigital.ai once registered
    service: http://localhost:5232
```

> The config file warns of a SECOND daemon (`cloudflared-dashboard.service`)
> running the same tunnel with a different ingress. Cloudflare load-balances
> across both connectors, so a hostname defined in only one config **404s
> roughly half the time**. Add the rule to whichever configs are live, or
> disable the second daemon first.

## Auth — the open question, not yet settled

AC #2 requires **no Radicale-specific credentials**. The authoritative identity
source is still undecided (Entra ID vs LDAP vs local). Backends this build
actually ships — verified by `ls radicale/auth/`, not assumed:

```
denyall  dovecot  htpasswd  http_remote_user  http_x_remote_user
imap     ldap     none      oauth2            pam  remote_user
```

`oauth2.py` **is** present, so the story's Entra/M365 plan is viable.

**Until the identity source is confirmed, bring-up uses `htpasswd`.** That is a
deliberate stopgap and it does **NOT** satisfy AC #2 — do not mark that
criterion met while htpasswd is in force.

## Calendars

Two to start (Sal, 2026-09-15):

1. **Earnings Calendar**
2. **Economic Indicator Calendar**

Both are created declaratively via `[storage] predefined_collections` (JSON,
see `config`) rather than by hand through a client, so the deployment is
reproducible.

**These two are DERIVED, not hand-maintained.** Franklin already owns both data
sets — FMP earnings calendar and economic indicators. A publisher that writes
VEVENTs from those tables is the intended source; a human typing earnings dates
into a CalDAV client would be duplicating a feed we already pay for. The
publisher is not built yet.

## Rights

`[rights] type = from_file`, file at `/etc/radicale/rights`. Pattern per AC #3:

* shared calendars — read-only for all authenticated users, read-write for the
  publisher account
* personal calendars — private, read-write to the owner only

## Verification

A CalDAV endpoint that returns 200 to a browser is not a working calendar.
Verify with an actual client sync (macOS Calendar or DAVx5) against the
collection URL, per AC #4 — and say which client was used.

## Related

* Jira: DAYTRADE-778
* Root `/Users/Sal/Projects/Franklin/CLAUDE.md` — infra conventions, tunnel table
* `franklin-infra` — systemd units, docker compose

## ⛔ DO NOT EXPOSE until an edge rate-limit rule exists

Cloudflare Access cannot be enforcing in front of this hostname: CalDAV clients
(macOS Calendar, DAVx5, Thunderbird) cannot run the interactive login, and the
UIs give you nowhere to put `CF-Access-Client-Id`/`Secret` service-token
headers. So the Access app must be **Bypass**, and the origin is the only gate.

That origin gate is HTTP Basic against **real user passwords** — the same rows
`dashboard.franklinfinancial.ai` authenticates. Internet-facing, that is a
brute-force target.

**Radicale has no rate limiting.** What it does have, and what each is actually
for — verified by reading the code, not the config comments:

| Mechanism | Where | What it does |
|---|---|---|
| `auth.delay` | `app/__init__.py:353`, `:583` | sleeps per failed/missing auth, **with jitter** (`delay * (0.5 + random())`) so the pause is not an oracle |
| constant-exec padding | `auth/__init__.py:218` | pads failed logins to a constant time — defeats **timing** attacks (telling "no such user" from "wrong password"). Does **nothing** to slow repeated guessing |
| `server.max_connections` | `server.py:341` | caps parallel connections, so caps parallel guessing |

`auth.delay` is declared in three modules and enforced in only one of them
(`app`, not `auth`) — reading a single file suggests it is dead config. It is
not. Check the whole tree before claiming a setting is inert.

Combined, `max_connections / delay` ≈ **8 / 2s ≈ 4 guesses/sec**. That is a
speed bump, not a defence. Before this hostname is reachable, one of:

* a **Cloudflare rate-limiting rule** scoped to `calendar.franklinfinancial.ai`
  (preferred — stops it at the edge, before the origin spends a DB round-trip
  per guess), or
* **fail2ban** at the origin against the Radicale log.

## Access IS enforcing — and a local probe can never see it

**Resolved 2026-09-15.** This section previously said enforcement was
unconfirmed, because no probe from mac-pro saw an Access redirect on any of 7
hostnames. That reading was wrong, and the reason matters more than the answer:

**mac-pro, the DGX and the MacBook all share the house egress IP, and that IP
is BYPASSED.** So every local probe returned the bypassed answer. The test was
structurally incapable of returning the failing value — the same shape as a
query that cannot return the row that would disprove it.

Sal loaded `questdb.franklinfinancial.ai` from a phone on cellular and got the
Cloudflare Access window. Access is live on the zone.

**To verify anything about Access posture, you must be off-network** — a phone
on cellular with VPN off, or any host outside the house. A local `curl` proves
nothing either way.

### Which makes the Access Bypass a HARD blocker, not a design preference

Sal's phone test *is* the CalDAV client test. A DAVx5 or macOS Calendar client
on cellular hits exactly that Access window, cannot complete an interactive
browser flow, and fails to sync. So:

**No client will work until a per-Application Bypass exists for
`calendar.franklinfinancial.ai`.** An Application is a hostname (+ optional
path); a Bypass/Everyone policy scoped to this one touches nothing else.

Creating it needs an **account-scoped** token with *Access: Apps & Policies
Edit*. There is no Cloudflare API token in `franklin.env` at all, and the
DNS-scoped `CLOUDFLARE_ACCESS_TOKEN` cannot do it — so this happens in the Zero
Trust dashboard.

### Status code alone cannot tell you whether Access is in front

Grafana returns `302`, which reads like an Access login redirect. Its
`Location` is `/login` — Grafana's *own* page. Discriminate on the redirect
TARGET containing `cloudflareaccess.com`, or on `cf-access-*` headers. Never on
the code.

## Tunnel ingress — one config, not two

`cloudflared-dashboard.service` is **decommissioned** (unit file gone, inactive);
one connector runs, PID 4946, `--config /etc/cloudflared/config.yml`. DevOps
measured 20/20 identical responses on an existing hostname and 6/6 `404`s on
`calendar` — a probe that *can* return 404, so the negative is real.

So add the rule to `/etc/cloudflared/config.yml` **only** (needs sudo):

```yaml
  - hostname: calendar.franklinfinancial.ai
    service: http://localhost:5232
```

Two cleanups belong in the same edit: delete the stale ⚠ second-daemon warning
comment (it cost a design question), and remove `/home/sal/.cloudflared/config.yml`,
which nothing reads.

## Unverified, carry as open

* **Edge timeout ~100s** — a long `REPORT` against a large calendar surfaces as
  `524`. Not measured for this zone.
* **WAF managed rules** may flag XML request bodies; CalDAV `REPORT` bodies are
  XML. If WAF is on, check for false positives before blaming Radicale.
* **`X-Forwarded-Host`** — see the HTTPS note above; Radicale advertises
  `http://` URLs unless that header is present.

## Publisher job — `franklin/publisher/publish_calendars.py`

Renders `fmp_earnings_calendar` and `fmp_economic_calendar` (Postgres
`benny_prod`) into the two shared calendars as `calendar-publisher`.

**Scheduled and run from Jenkins, nowhere else:** `1. Franklin / Day Trading
Agent / calendar-publisher`, hourly (`H * * * *`), with DRY_RUN / FORCE / ONLY
parameters for manual runs. Build descriptions show the new/changed/stale
counts. The job definition is `franklin/deploy/jenkins/config.xml` (a bootstrap
that syncs this checkout to `origin/franklin_1.0prod` as `sal` and `load`s
`franklin/deploy/jenkins/calendar-publisher.groovy`). No deploy key exists for
this fork, hence not pipeline-from-SCM. A systemd timer existed for ~1 hour on
2026-09-16 and was removed: Sal wants Jenkins for visibility and manual runs,
and `disableConcurrentBuilds()` cannot see a systemd run. Do not re-add it.

To recreate the job: POST `config.xml` to
`/job/1.%20Franklin/job/Day%20Trading%20Agent/createItem?name=calendar-publisher`
with `Content-Type: application/xml; charset=utf-8`.

**Scope (defaults).** Earnings: active members (`removed_at IS NULL`) of
`portfolio` + `earnings_wk`; economic: `country = 'US'`; window −7/+90 days.
Measured 2026-09-16: **224 earnings, 955 economic.** The unscoped table is
1M+ rows. `earnings_wk` is a rolling list (858 rows, 93 active), so ignoring
`removed_at` quadruples the calendar with names that left the universe. Of the
52 portfolio symbols, 37 are ETFs/leveraged funds with no earnings — 17
portfolio events is complete, not a gap.

**Times.** `bmo` → 08:00 ET, `amc` → 16:30 ET, anything else → ALL-DAY. An
unknown time is never rendered as a clock time. Emitted in UTC, DST per date.

**Cost.** One CalDAV REPORT per calendar, then PUT only new/changed and DELETE
only stale. A quiet run is ~5 s. A full rewrite (`--force`, or first run) is
~1,200 PUTs at ~0.4 s each — ~10 min — which is why change-detection is
load-bearing for an hourly timer.

**Verified live, both directions (2026-09-16):** tampering one SUMMARY →
`1 changed` → repaired → `0 changed`; planting a foreign item → `1 stale` →
deleted, 224 remain.

### Four bugs this shipped through — each passed something that looked like a test

1. **Rights `{0}` → HTTP 500.** `{0}` is the first CAPTURE GROUP of the user
   pattern, not the username; `user = .+` has none. It only fired on item
   paths, because every earlier request matched a publisher rule first. Use
   `{user}`. (`radicale/rights/from_file.py:121`)
2. **Folding a multi-line block → intermittent 400.** Only when a 75-octet cut
   landed exactly on a CRLF. `vobject.readOne` ACCEPTS the corrupt form;
   Radicale's `read_components` rejects it. Tests parse with Radicale's reader
   and sweep length by 1 byte — two earlier test versions went green against
   this exact bug.
3. **`@` in resource names → the job deleted its own events.** Radicale
   percent-encodes hrefs; `%40` never matched, so everything was both "new"
   and "stale", and deletes ran after puts: 47 freshly written events gone.
   Resource names are now `[a-z0-9-]` slugs (the UID *property* keeps
   `@franklinfinancial.ai`), deletes run first, and deleting a wanted slug is
   an assertion.
4. **Change-detection that could never see the calendar.** A regex for
   `<calendar-data>` missed Radicale's `<C:calendar-data>`, so every run saw an
   empty calendar and rewrote everything while logging success. And a textual
   comparison would never match anyway: Radicale reorders properties,
   re-folds, and returns LF. Now: XML parser + order/fold-insensitive compare,
   tested against a verbatim server response (`fixtures_report_earnings.xml`).

Also: a mutation run whose `str.replace` silently matched nothing reported the
tests as catching a bug they did not catch. Mutation scripts must assert the
mutation applied.

## Shared calendars appear automatically (share-by-group)

Every user sees **Earnings Calendar** and **Economic Indicator Calendar** in
their OWN calendar home (`/<user>/franklin-earnings/`,
`/<user>/franklin-economic-indicators/`), read-only, with no per-user setup.
Before this, the calendars lived only under `/calendar-publisher/`, which no
client discovers, and each had to be added by URL.

Mechanism (Radicale >= 3.8 map sharing):
* `franklin_radicale.group_all_users` puts every authenticated user in group
  `franklin`, **except `calendar-publisher`** (it must keep seeing only the
  real collections it writes).
* `franklin/deploy/create-group-shares.sh` creates two share entries owned by
  `calendar-publisher`, `User=:franklin`, `PathOrToken=/{user}/<name>/`.
  Idempotent; verifies via the list API. Stored in
  `franklin-radicale-data/collections/collection-db/sharing.csv`.
* Rights: `calendar-publisher` holds `M` on its calendars (permit to create
  map shares); `[sharing] collection_by_map = True`.
* **FRANKLIN PATCH in `radicale/app/__init__.py`:** upstream only called the
  group module for type `htgroup`, so a custom group plugin was loaded but
  never consulted on CalDAV requests. Shares could be CREATED (the sharing API
  calls the plugin itself) but silently resolved for nobody. Guarded by
  `franklin/tests/test_group_all_users.py`; re-check after any upstream merge.

Verified on a staging instance with test users (alice, bob): both see both
calendars (224 / 955 events); PUT, DELETE, PROPPATCH through the share are
403; cross-user 403; anonymous 401; publisher writes appear to users
immediately. Known, harmless: a user's MKCALENDAR at exactly a share path
returns 201 and creates an empty shadowed folder — the share still wins.

### ⚠️ Never leave uncommitted work in `/home/Franklin/franklin-radicale`

The `calendar-publisher` Jenkins job syncs THIS checkout with
`git reset --hard origin/franklin_1.0prod` on every run (hourly, and on every
manual build). On 2026-09-16 two builds at 04:59 wiped uncommitted edits to
four tracked files (the app patch, rights, config, Dockerfile) seconds after
the image had been built from them — production ran correct code while the
first commit of the feature was missing half of it. Untracked files and the
gitignored rendered `franklin/deploy/config/` survive a reset; tracked edits do
not. Commit and push BEFORE building or triggering the job, or edit in a
separate worktree.
