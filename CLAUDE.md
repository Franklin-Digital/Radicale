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
