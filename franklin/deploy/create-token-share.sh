#!/bin/bash
# Create the secret, read-only, UNAUTHENTICATED feed URL for one shared
# calendar, for Confluence's "Subscribe by URL". DAYTRADE-778, approved by Sal
# 2026-10-04 (SMB Sessions first; Earnings and Economic Indicators the same day).
#
# Why a token: Confluence Cloud's subscriber never sends HTTP Basic credentials
# (the form's Username/Password and user:pass@ in the URL both arrived as
# "anonymous" -> 401), so a login-gated feed cannot be subscribed to.
#
# Needs `[sharing] collection_by_token = True` (franklin/config/config) and the
# rights rule [publisher-tokenable-calendars] (T on the three shared calendars
# only), deployed: render-config.sh + `docker restart franklin-radicale`.
#
# The URL is a secret: it is saved to franklin.env (timestamped backup first)
# as <CALENDAR>_FEED_URL -- SMB_SESSIONS_FEED_URL, EARNINGS_FEED_URL,
# ECONOMIC_INDICATORS_FEED_URL -- and printed ONCE. Idempotent: an existing
# token share is never duplicated or overwritten.
#
# Revoke: POST /.sharing/v1/token/delete PathOrToken=<the /.token/... path>
# as calendar-publisher. Confluence then shows the calendar as unreadable.
#
# Run as sal on mac-pro:
#   franklin/deploy/create-token-share.sh smb-sessions|earnings|economic-indicators
set -euo pipefail
ENV=/home/Franklin/secrets/franklin.env
set -a; . "$ENV"; set +a
: "${CALENDAR_PUBLISHER_PASSWORD:?CALENDAR_PUBLISHER_PASSWORD empty}"

BASE="${RADICALE_BASE:-http://127.0.0.1:5232}"
PUBLIC="${RADICALE_PUBLIC:-https://calendar.franklinfinancial.ai}"
AUTH="calendar-publisher:${CALENDAR_PUBLISHER_PASSWORD}"
case "${1:-}" in
  smb-sessions|earnings|economic-indicators) CAL="$1" ;;
  *) echo "usage: $0 smb-sessions|earnings|economic-indicators" >&2; exit 2 ;;
esac
TARGET="/calendar-publisher/$CAL/"
VAR="$(tr 'a-z-' 'A-Z_' <<<"$CAL")_FEED_URL"
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT

existing=$(curl -s -u "$AUTH" -H 'accept: text/csv' -d '' "$BASE/.sharing/v1/token/list" \
           | awk -F';' -v t="$TARGET" '$1=="token" && $3==t {print $2}')

if [ -n "$existing" ]; then
  if [ -n "${!VAR:-}" ]; then
    echo "exists   token share for $TARGET; $VAR already in franklin.env"
    token="${!VAR#"$PUBLIC"}"
  else
    echo "FAIL: a token share for $TARGET exists but $VAR is not in $ENV."
    echo "      Not creating a second one. Delete the old share first if the URL is lost."
    exit 1
  fi
else
  code=$(curl -s -o "$TMP" -w '%{http_code}' -u "$AUTH" -H 'accept: text/plain' \
      -d "PathMapped=$TARGET" -d "Permissions=r" "$BASE/.sharing/v1/token/create")
  [ "$code" = 200 ] || { echo "FAIL: token/create -> HTTP $code: $(head -c 300 "$TMP")"; exit 1; }
  token=$(grep -o "PathOrToken='[^']*'" "$TMP" | head -1 | sed "s/^PathOrToken='//; s/'\$//")
  case "$token" in /.token/*/) ;; *) echo "FAIL: no token in create response"; exit 1 ;; esac
  code=$(curl -s -o "$TMP" -w '%{http_code}' -u "$AUTH" -H 'accept: text/plain' \
      -d "PathOrToken=$token" "$BASE/.sharing/v1/token/enable")
  [ "$code" = 200 ] || { echo "FAIL: token/enable -> HTTP $code: $(head -c 300 "$TMP")"; exit 1; }
  cp -p "$ENV" "$ENV.bak-$(date +%Y%m%d-%H%M%S)"
  printf '\n# Secret read-only %s feed for Confluence "Subscribe by URL" (%s)\n%s=%s%s\n' \
      "$CAL" "$(date +%F)" "$VAR" "$PUBLIC" "$token" >> "$ENV"
  echo "created  token share for $TARGET (read-only); saved to $ENV as $VAR"
fi

# Verify what Confluence will see: anonymous, public URL, read-only.
n=$(curl -s "$PUBLIC$token" | grep -c '^BEGIN:VEVENT' || true)
[ "$n" -gt 0 ] || { echo "FAIL: anonymous GET of the feed returned 0 events"; exit 1; }
w=$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'content-type: text/calendar' \
    --data-binary $'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n' "$PUBLIC${token}probe-write.ics")
[ "$w" = 403 ] || { echo "FAIL: anonymous PUT via the token returned $w, expected 403"; exit 1; }
echo "verified: anonymous GET -> $n events; anonymous PUT -> 403"
if [ -z "$existing" ]; then
  echo
  echo "Paste into Confluence (Calendars > + > Subscribe by URL), Username/Password EMPTY:"
  echo "  $PUBLIC$token"
fi
