#!/usr/bin/env bash
# Create the two shared calendars ONCE, under the calendar-publisher principal.
#
#   /calendar-publisher/earnings/              "Earnings Calendar"
#   /calendar-publisher/economic-indicators/   "Economic Indicator Calendar"
#
# NOT predefined_collections: that creates a PRIVATE copy under every user who
# logs in (radicale/app/__init__.py:625 uses principal_path + name), which is a
# per-user starter-calendar feature, not sharing.
#
# Idempotent: MKCALENDAR on an existing collection returns 405, treated as OK.
set -euo pipefail

BASE="${RADICALE_BASE:-http://127.0.0.1:5232}"
USER="calendar-publisher"

SECRETS=/home/Franklin/secrets/franklin.env
set -a
# shellcheck disable=SC1090
. "$SECRETS"
set +a

: "${CALENDAR_PUBLISHER_PASSWORD:?CALENDAR_PUBLISHER_PASSWORD not in franklin.env - run create-publisher-account.sh first and store the password it prints}"

mk() {
  local path="$1" display="$2"
  local body code
  body=$(cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set><D:prop>
    <D:displayname>${display}</D:displayname>
    <C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set>
  </D:prop></D:set>
</C:mkcalendar>
XML
)
  code=$(curl -s -o /dev/null -w "%{http_code}" -X MKCALENDAR \
    -u "${USER}:${CALENDAR_PUBLISHER_PASSWORD}" \
    -H "Content-Type: application/xml; charset=utf-8" \
    --data "$body" "${BASE}/${USER}/${path}/")

  case "$code" in
    201) echo "  created  /${USER}/${path}/  (${display})" ;;
    # MEASURED 2026-09-16: Radicale returns 409 Conflict for MKCALENDAR on an
    # existing collection, NOT the 405 this script originally expected. With
    # only 405 handled, a re-run failed as UNEXPECTED -- an idempotency claim
    # that was never true. Both are accepted now.
    409|405) echo "  exists   /${USER}/${path}/  (${code} - already a collection, fine)" ;;
    401) echo "  AUTH FAILED (401) for ${USER} - is the account created and the password right?"; exit 1 ;;
    *)   echo "  UNEXPECTED ${code} for /${USER}/${path}/"; exit 1 ;;
  esac
}

mk "earnings"            "Earnings Calendar"
mk "economic-indicators" "Economic Indicator Calendar"

echo
echo "  verifying both are discoverable..."
curl -s -X PROPFIND -u "${USER}:${CALENDAR_PUBLISHER_PASSWORD}" -H "Depth: 1" \
  "${BASE}/${USER}/" | grep -oE "<displayname>[^<]+" | sed "s|^|    |"
