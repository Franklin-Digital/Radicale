"""Radicale auth against the Franklin dashboard's Postgres `users` table.

WHY THIS EXISTS
---------------
DAYTRADE-778 AC #2: users authenticate with an EXISTING account, not a
Radicale-specific credential. Sal, 2026-09-15: "usernames and passwords should
be the same as for dashboard.franklinfinancial.ai".

Those users live in Postgres (`users`: username, email, password_hash), written
by `trading_desk.py::UserDB` and hashed with `werkzeug.security`. Radicale ships
no Postgres backend -- verified by `ls radicale/auth/`:

    denyall dovecot htpasswd http_remote_user http_x_remote_user
    imap    ldap    none     oauth2           pam  remote_user

So every stock option is wrong for us:

  * htpasswd  -- a SECOND credential store. Violates AC #2 outright, and any
                 sync job makes password changes silently diverge.
  * oauth2    -- the dashboard is not an OAuth2 provider. The Jira story
                 assumed Entra/M365; the dashboard does not use it.
  * ldap/pam  -- not the authority for these accounts.

Hence a plugin. `radicale.utils.load_plugin` accepts any importable module, and
`BaseAuth` requires exactly one method, so this stays small enough to re-verify
by reading.

ONE STORE, NOT A COPY. This reads the SAME rows the dashboard writes. A password
changed in the dashboard is effective here on the next request, because there is
nothing to propagate.

SECURITY NOTES
--------------
* Verification is `werkzeug.security.check_password_hash`, the same function the
  dashboard uses. Do not reimplement the comparison -- it is constant-time and
  scheme-aware (pbkdf2/scrypt), and a hand-rolled `==` would be neither.
* A user row with an empty/NULL `password_hash` is REFUSED, never treated as
  "no password set". Unknown and permitted are different things.
* Failures return "" (Radicale's "not authenticated") and are logged WITHOUT the
  password. A DB outage must read as auth-failed, never as auth-success.
"""

from __future__ import annotations

import logging

from radicale import config as radicale_config
from radicale.auth import BaseAuth

logger = logging.getLogger(__name__)

#: Read-only: this plugin never creates or mutates a user.
#:
#: EXACT match, not lower(). Two reasons, both load-bearing:
#:
#:   1. `users_username_key` is a CASE-SENSITIVE unique btree on `username`, so
#:      "Sal" and "sal" can both exist as separate accounts. A lower() lookup
#:      would match both and fetchone() would take an arbitrary one -- a login
#:      resolving to whichever row the planner happened to return.
#:   2. The dashboard does `WHERE username = %s` (trading_desk.py:1572). Matching
#:      loosely here would create a credential that works on the calendar and
#:      fails on the dashboard -- exactly the divergence this plugin prevents.
#:
#: Parameterised, so a username can never become SQL.
_LOOKUP_SQL = "SELECT password_hash FROM users WHERE username = %s"


class Auth(BaseAuth):
    """Validate a login against the dashboard's `users` table."""

    def __init__(self, configuration: "radicale_config.Configuration") -> None:
        super().__init__(configuration)
        self._dsn = dict(
            host=configuration.get("auth", "franklin_pg_host"),
            port=configuration.get("auth", "franklin_pg_port"),
            dbname=configuration.get("auth", "franklin_pg_dbname"),
            user=configuration.get("auth", "franklin_pg_user"),
            password=configuration.get("auth", "franklin_pg_password"),
        )

    def _login(self, login: str, password: str) -> str:
        """Return the username on success, "" on any failure."""
        # An empty password must never reach check_password_hash -- some hash
        # schemes accept "" against a malformed digest.
        if not login or not password:
            return ""

        try:
            import psycopg2
            from werkzeug.security import check_password_hash
        except ImportError:
            logger.error("franklin auth: psycopg2/werkzeug missing - refusing login")
            return ""

        conn = None
        try:
            conn = psycopg2.connect(connect_timeout=5, **self._dsn)
            with conn.cursor() as cur:
                cur.execute(_LOOKUP_SQL, (login,))
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - outage must fail CLOSED
            logger.error("franklin auth: user lookup failed for %r: %s", login, exc)
            return ""
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

        if row is None:
            logger.info("franklin auth: no such user %r", login)
            return ""

        stored = row[0]
        if not stored:
            # Present but unusable. NOT the same as "no password required".
            logger.warning("franklin auth: user %r has an empty password_hash "
                           "- refusing", login)
            return ""

        try:
            ok = check_password_hash(stored, password)
        except Exception as exc:  # noqa: BLE001 - unknown scheme, corrupt hash
            logger.error("franklin auth: hash check failed for %r: %s", login, exc)
            return ""

        if not ok:
            logger.info("franklin auth: bad password for %r", login)
            return ""

        logger.debug("franklin auth: %r authenticated", login)
        return login
