#!/usr/bin/env bash
# Create the `calendar-publisher` service account (DAYTRADE-778).
#
# This writes to the PRODUCTION `users` table that dashboard.franklinfinancial.ai
# authenticates against, so it is deliberately: idempotent, refuses to touch an
# existing row, and prints the generated password EXACTLY ONCE.
#
# Hashing uses werkzeug.generate_password_hash -- the same function the
# dashboard uses (trading_desk.py) -- so the account works in both places. A
# hand-rolled hash would authenticate here and fail there.
set -euo pipefail

SECRETS=/home/Franklin/secrets/franklin.env
set -a
# shellcheck disable=SC1090
. "$SECRETS"
set +a

: "${BENNY_PASSWORD:?BENNY_PASSWORD empty - refusing}"

/home/Franklin/venvs/franklin-radicale-prod/bin/python - <<'PY'
import os, secrets, string, sys
import psycopg2
from werkzeug.security import generate_password_hash

USER = "calendar-publisher"
EMAIL = "calendar-publisher@franklinfinancial.ai"

dsn = dict(host=os.environ.get("BENNY_PG_HOST", "localhost"),
           port=int(os.environ.get("BENNY_PG_PORT", "5432")),
           dbname=os.environ.get("BENNY_DB", "benny_prod"),
           user=os.environ.get("BENNY_USER", "benny_prod"),
           password=os.environ["BENNY_PASSWORD"])

conn = psycopg2.connect(connect_timeout=5, **dsn)
conn.autocommit = False
cur = conn.cursor()

cur.execute("SELECT id FROM users WHERE username = %s", (USER,))
if cur.fetchone():
    print(f"  {USER} already exists - NOT modifying it.")
    print("  If the password is unknown, reset it deliberately rather than")
    print("  letting this script overwrite a live credential.")
    conn.close()
    sys.exit(0)

# 32 chars from a 62-char alphabet ~= 190 bits. This is a service credential
# typed into a config once, so length costs nothing.
alphabet = string.ascii_letters + string.digits
password = "".join(secrets.choice(alphabet) for _ in range(32))

cur.execute(
    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
    (USER, EMAIL, generate_password_hash(password)))
new_id = cur.fetchone()[0]
conn.commit()
conn.close()

print(f"  created user {USER!r} (id={new_id})")
print()
print("  PASSWORD (shown ONCE - store it in franklin.env as CALENDAR_PUBLISHER_PASSWORD):")
print(f"    {password}")
PY
