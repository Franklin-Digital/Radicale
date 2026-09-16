# Applying the exposure steps — DAYTRADE-778

Both steps below need privileges this session does not have, and that is
deliberate: they touch production networking.

* `sudo` on mac-pro **requires a password** (`sudo -n true` → *a password is
  required*), so `/etc/cloudflared/config.yml` cannot be edited from a
  non-interactive session.
* `CLOUDFLARE_ACCESS_TOKEN` is **DNS-scoped**. It created the `calendar` CNAME
  fine, but returns `Authentication error` on the rulesets API and
  *Unauthorized* on zone settings — so it cannot create a rate-limit rule.

**Order matters: rate limit FIRST, ingress SECOND.** The ingress rule is what
makes the hostname reachable. Adding it before the rate limit exposes Basic
auth over real user passwords with ~4 guesses/sec of protection.

---

## Step 1 — Rate limit (do this BEFORE step 2)

Dashboard → **Security → WAF → Rate limiting rules → Create rule**, on zone
`franklinfinancial.ai`:

| Field | Value |
|---|---|
| Rule name | `calendar-caldav-bruteforce` |
| If incoming requests match | `Hostname` **equals** `calendar.franklinfinancial.ai` |
| Characteristics | **IP** (`ip.src`) |
| Period | **1 minute** |
| Requests | **60** |
| Action | **Block** |
| Duration | **10 minutes** |

**The action must be `Block`, not `Managed Challenge`.** A challenge serves an
interstitial that assumes a browser; CalDAV clients (macOS Calendar, DAVx5,
Thunderbird) cannot solve it and will simply fail to sync. This is the same
reason Access must be Bypass rather than enforcing.

**Why 60/min is the right shape.** Normal CalDAV clients poll on a ~15 minute
timer and burst maybe 10–30 requests during a sync, so 60/min/IP is well clear
of legitimate use while cutting brute force from thousands/min to 60. It is a
ceiling, not a tuned number — if a real client trips it, raise it rather than
switching to a challenge.

**Better, if the plan includes Advanced Rate Limiting:** count only responses
with status `401` instead of all requests. That targets failed auth precisely
and leaves successful sync traffic entirely unthrottled. Whether this zone's
plan has it is **unverified** — I could not read zone settings with the
available token.

Equivalent API call, if you mint a token with **Zone → Ruleset → Edit**:

```bash
ZID=426e971142895111f898d82035f47515
curl -X PUT "https://api.cloudflare.com/client/v4/zones/$ZID/rulesets/phases/http_ratelimit/entrypoint" \
  -H "Authorization: Bearer $RULESET_TOKEN" -H "Content-Type: application/json" \
  --data '{
    "rules": [{
      "description": "calendar-caldav-bruteforce (DAYTRADE-778)",
      "expression": "(http.host eq \"calendar.franklinfinancial.ai\")",
      "action": "block",
      "ratelimit": {
        "characteristics": ["ip.src", "cf.colo.id"],
        "period": 60,
        "requests_per_period": 60,
        "mitigation_timeout": 600
      }
    }]
  }'
```

> `PUT` on the entrypoint **replaces every rule in that phase**. If the zone
> already has rate-limit rules, GET the entrypoint first and PUT the merged
> list, or you will silently delete them.

---

## Step 2 — Tunnel ingress

A validated config is staged at
`franklin/deploy/cloudflared-config.proposed.yml`. It differs from live in
exactly two ways:

1. **adds** the `calendar` rule, positioned before the catch-all `404` (after
   it, the rule would be unreachable)
2. **removes** the stale `⚠ Known issue (2026-07-16)` second-daemon comment —
   DevOps verified that unit no longer exists (`is-enabled` = *not-found*,
   inactive; one connector, PID 4946)

Already validated, by `cloudflared` itself rather than by eye:

```
cloudflared --config cloudflared-config.proposed.yml tunnel ingress validate   -> OK
... tunnel ingress rule https://calendar.franklinfinancial.ai/   -> Matched rule #10 -> http://localhost:5232
... tunnel ingress rule https://dashboard.franklinfinancial.ai/  -> Matched rule #5  -> http://localhost:8051  (control: unchanged)
```

Apply:

```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak-$(date +%F-%H%M)
sudo cp /home/Franklin/franklin-radicale/franklin/deploy/cloudflared-config.proposed.yml \
        /etc/cloudflared/config.yml
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo systemctl restart cloudflared && sleep 5 && systemctl is-active cloudflared
```

Optional cleanup DevOps flagged — nothing reads it, and it misled us once:

```bash
rm -f /home/sal/.cloudflared/config.yml
```

---

## Step 3 — Verify (do not skip; a 200 is not proof)

Radicale is not running yet, so expect `502` until the container is up. Once it
is:

```bash
# 401 is the HEALTHY answer: the app is up AND auth is enforced.
curl -s -o /dev/null -w "%{http_code}\n" https://calendar.franklinfinancial.ai/
```

A **200 here means auth is off** — treat it as a failure, not a success.

Then the acceptance criterion that actually matters (AC #4): subscribe from a
real CalDAV client with a dashboard username/password and confirm both
calendars appear. Say which client was used. A URL that returns 401 to `curl`
is not a working calendar.

Watch for, all currently **unverified**:

* `http://` URLs in PROPFIND responses — Radicale only trusts
  `X-Forwarded-Proto` when `X-Forwarded-Host` is also present
  (`radicale/utils.py:593`), and cloudflared sends the former but preserves
  `Host`. Breaks discovery on strict clients.
* `524` on a long `REPORT` — Cloudflare's ~100s edge timeout.
* WAF flagging XML request bodies; CalDAV `REPORT` bodies are XML.
