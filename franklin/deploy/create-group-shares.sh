#!/bin/bash
# Share the Earnings and Economic Indicator calendars into EVERY user's own
# calendar home, read-only, so CalDAV clients and the web UI discover them with
# no per-user setup. DAYTRADE-778.
#
# Mechanism: Radicale share-by-group. One entry per calendar, owned by
# calendar-publisher, User=":franklin", PathOrToken="/{user}/<name>/".
# Group membership comes from franklin_radicale.group_all_users (every
# authenticated user except calendar-publisher). For a group share Radicale
# forces Enabled / not Hidden / read-only for the member.
#
# Idempotent: an existing share returns 409 and is treated as present; the
# script then asserts via the list API that BOTH shares exist, so "exit 0"
# means the shares are really there, not merely that nothing errored.
#
# Run as sal on mac-pro:  franklin/deploy/create-group-shares.sh
set -euo pipefail
set -a; . /home/Franklin/secrets/franklin.env; set +a
: "${CALENDAR_PUBLISHER_PASSWORD:?CALENDAR_PUBLISHER_PASSWORD empty}"

BASE="${RADICALE_BASE:-http://127.0.0.1:5232}"
AUTH="calendar-publisher:${CALENDAR_PUBLISHER_PASSWORD}"

# share name in each user's home  ->  real calendar under calendar-publisher
SHARES=(
  "franklin-earnings:earnings"
  "franklin-economic-indicators:economic-indicators"
)

for pair in "${SHARES[@]}"; do
  name="${pair%%:*}"; target="${pair##*:}"
  code=$(curl -s -o /tmp/share-create.$$ -w '%{http_code}' -u "$AUTH" \
      -H 'accept: text/plain' \
      -d "PathOrToken=/{user}/${name}/" \
      -d "PathMapped=/calendar-publisher/${target}/" \
      -d "User=:franklin" -d "Enabled=True" -d "Hidden=False" \
      "$BASE/.sharing/v1/map/create")
  case "$code" in
    200) echo "created  /{user}/${name}/ -> /calendar-publisher/${target}/" ;;
    409) echo "exists   /{user}/${name}/ -> /calendar-publisher/${target}/" ;;
    *)   echo "FAIL: create /{user}/${name}/ -> HTTP $code: $(head -c 300 /tmp/share-create.$$)"
         rm -f /tmp/share-create.$$; exit 1 ;;
  esac
done
rm -f /tmp/share-create.$$

# Verify from the source of truth, not from the create responses.
list=$(curl -s -u "$AUTH" -H 'accept: text/csv' -d '' "$BASE/.sharing/v1/map/list")
for pair in "${SHARES[@]}"; do
  name="${pair%%:*}"; target="${pair##*:}"
  if ! grep -q "^map;/{user}/${name}/;/calendar-publisher/${target}/;.*;calendar-publisher;:franklin;" <<<"$list"; then
    echo "FAIL: share /{user}/${name}/ not present in sharing list:"; echo "$list"; exit 1
  fi
done
echo "verified: both group shares present"
