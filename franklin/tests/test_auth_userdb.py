"""Tests for the Franklin UserDB auth plugin.

These assert the FAILURE paths hardest, because an auth plugin that fails OPEN
is worse than one that does not exist: every wrong answer is a granted login.
No test here touches production Postgres -- the connection is stubbed.
"""
import sys
import types

import pytest

sys.path.insert(0, "franklin")


class _FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, row=None, raise_on_connect=False):
        self._row = row
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._row)

    def close(self):
        self.closed = True


def _auth(monkeypatch, row=None, connect_exc=None, check_result=True,
          check_exc=None):
    """Build an Auth with psycopg2/werkzeug stubbed."""
    from franklin_radicale import auth_userdb

    conn = _FakeConn(row)

    def fake_connect(**kw):
        if connect_exc:
            raise connect_exc
        return conn

    def fake_check(stored, password):
        if check_exc:
            raise check_exc
        return check_result

    monkeypatch.setitem(sys.modules, "psycopg2",
                        types.SimpleNamespace(connect=fake_connect))
    monkeypatch.setitem(sys.modules, "werkzeug",
                        types.ModuleType("werkzeug"))
    monkeypatch.setitem(sys.modules, "werkzeug.security",
                        types.SimpleNamespace(check_password_hash=fake_check))

    a = auth_userdb.Auth.__new__(auth_userdb.Auth)
    a._dsn = dict(host="h", port="5432", dbname="d", user="u", password="p")
    return a, conn


# ── the happy path ────────────────────────────────────────────────────

def test_valid_credentials_return_the_username(monkeypatch):
    a, _ = _auth(monkeypatch, row=("pbkdf2:sha256:1000$abc$def",),
                 check_result=True)
    assert a._login("sal", "correct-horse") == "sal"


# ── fail-closed paths: each of these MUST return "" ───────────────────

def test_unknown_user_is_refused(monkeypatch):
    a, _ = _auth(monkeypatch, row=None)
    assert a._login("nobody", "pw") == ""


def test_wrong_password_is_refused(monkeypatch):
    a, _ = _auth(monkeypatch, row=("hash",), check_result=False)
    assert a._login("sal", "wrong") == ""


@pytest.mark.parametrize("stored", ["", None])
def test_empty_password_hash_is_refused_not_treated_as_no_password(
        monkeypatch, stored):
    """A row with no usable hash must NOT mean 'no password required'.
    Unknown and permitted are different things."""
    a, _ = _auth(monkeypatch, row=(stored,), check_result=True)
    assert a._login("sal", "anything") == ""


@pytest.mark.parametrize("pw", ["", None])
def test_empty_password_never_reaches_the_hash_check(monkeypatch, pw):
    """Some schemes accept '' against a malformed digest, so short-circuit."""
    a, _ = _auth(monkeypatch, row=("hash",), check_result=True)
    assert a._login("sal", pw) == ""


def test_empty_login_is_refused(monkeypatch):
    a, _ = _auth(monkeypatch, row=("hash",), check_result=True)
    assert a._login("", "pw") == ""


def test_database_outage_fails_CLOSED(monkeypatch):
    """THE critical one. A DB outage must read as auth-FAILED. If this ever
    returns a username, every request during an outage is authenticated."""
    a, _ = _auth(monkeypatch, connect_exc=RuntimeError("connection refused"))
    assert a._login("sal", "pw") == ""


def test_corrupt_hash_fails_closed(monkeypatch):
    a, _ = _auth(monkeypatch, row=("$weird$",),
                 check_exc=ValueError("unknown scheme"))
    assert a._login("sal", "pw") == ""


def test_missing_dependencies_fail_closed(monkeypatch):
    """psycopg2/werkzeug absent must refuse, not crash into a granted login."""
    from franklin_radicale import auth_userdb
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    a = auth_userdb.Auth.__new__(auth_userdb.Auth)
    a._dsn = {}
    # `import psycopg2` yields None -> attribute access raises -> caught
    assert a._login("sal", "pw") == ""


# ── shape of the query ────────────────────────────────────────────────

def test_lookup_is_exact_match_not_case_folded():
    """`users_username_key` is a CASE-SENSITIVE unique index, so "Sal" and "sal"
    can both exist. A lower() lookup would match BOTH, and fetchone() would take
    an arbitrary row -- a login resolving to whichever the planner returned.

    The dashboard does `WHERE username = %s` (trading_desk.py:1572). Matching
    loosely here would create a credential that works on the calendar and fails
    on the dashboard, which is the divergence this plugin exists to prevent."""
    from franklin_radicale import auth_userdb
    assert "WHERE username = %s" in auth_userdb._LOOKUP_SQL
    assert "lower(" not in auth_userdb._LOOKUP_SQL.lower()


def test_lookup_is_parameterised():
    """A username must never be able to become SQL."""
    from franklin_radicale import auth_userdb
    assert "%s" in auth_userdb._LOOKUP_SQL
    assert chr(39) not in auth_userdb._LOOKUP_SQL


def test_plugin_never_writes():
    """This plugin authenticates; it must not create or mutate users."""
    from franklin_radicale import auth_userdb
    sql = auth_userdb._LOOKUP_SQL.upper()
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER"):
        assert verb not in sql
