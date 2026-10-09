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
#: CASE-INSENSITIVE, matching the dashboard (DayTradingAgent, onboarding
#: 2026-10-03): usernames are email addresses, and nobody types one in a fixed
#: case. This was an exact match while `users_username_key` was the only unique
#: index, because a lower() lookup could then match "Sal" AND "sal". Two things
#: keep that from happening now:
#:
#:   1. At most two rows are fetched and anything but exactly one is refused, so
#:      an ambiguous name signs nobody in instead of whichever row came first.
#:   2. onboarding sql/001 adds a unique index on lower(username), which makes
#:      the ambiguous case impossible.
#:
#: The STORED username is returned, never the typed one: Radicale keys
#: collections and rights on it, so "Sal.Cobian@Gmail.com" and
#: "sal.cobian@gmail.com" must land in the same /<user>/ tree.
#:
#: Parameterised, so a username can never become SQL.
#:
#: `status` (onboarding sql/001): only 'active' signs in. 'invited' has no
#: password yet; 'disabled' is off. Both get the same "" as a wrong password.
_LOOKUP_SQL = ("SELECT username, password_hash, status FROM users "
               "WHERE lower(username) = lower(%s) LIMIT 2")


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
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - outage must fail CLOSED
            logger.error("franklin auth: user lookup failed for %r: %s", login, exc)
            return ""
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

        if not rows:
            logger.info("franklin auth: no such user %r", login)
            return ""
        if len(rows) > 1:
            logger.error("franklin auth: %r matches more than one user "
                         "case-insensitively - refusing", login)
            return ""

        username, stored, status = rows[0]
        if status != "active":
            logger.info("franklin auth: %r is %s - refusing", username, status)
            return ""
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

        logger.debug("franklin auth: %r authenticated as %r", login, username)
        return username
