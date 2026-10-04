"""Share-by-token for SMB Sessions, through the REAL rights file and the REAL
[sharing] section of franklin/config/config (DAYTRADE-778).

Confluence Cloud's "Subscribe by URL" never sends HTTP Basic credentials, so
the SMB Sessions feed it subscribes to is a secret token URL. These tests pin
what that may and may not open:

  * calendar-publisher can create a token for smb-sessions, and it reads
    anonymously and is read-only;
  * NO token can be created for any other calendar, by anyone;
  * the normal paths stay login-only.
"""
import configparser
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))          # franklin/
sys.path.insert(0, os.path.join(HERE, "..", ".."))    # repo root (radicale)

from radicale.tests import BaseTest  # noqa: E402

RIGHTS = os.path.join(HERE, "..", "config", "rights")
CONFIG = os.path.join(HERE, "..", "config", "config")

PUB = "calendar-publisher:pubpw"
USER = "sal.cobian:salpw"

EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//franklin//test//EN
BEGIN:VEVENT
UID:%(uid)s
DTSTAMP:20261004T000000Z
DTSTART:20261005T201500Z
DTEND:20261005T204500Z
SUMMARY:Easy Money Trades Meeting
END:VEVENT
END:VCALENDAR
"""


def _real_sharing_section() -> dict:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CONFIG)
    return dict(cp["sharing"])


class TestSmbTokenShare(BaseTest):

    def setup_method(self) -> None:
        BaseTest.setup_method(self)
        htpasswd = os.path.join(self.colpath, ".htpasswd")
        with open(htpasswd, "w") as f:
            f.write("calendar-publisher:pubpw\nsal.cobian:salpw\n")
        self.configure({
            "auth": {"type": "htpasswd", "htpasswd_filename": htpasswd,
                     "htpasswd_encryption": "plain", "delay": "0"},
            "rights": {"type": "from_file", "file": RIGHTS},
            "sharing": _real_sharing_section(),
        })
        for name in ("smb-sessions", "earnings", "economic-indicators"):
            path = f"/calendar-publisher/{name}/"
            self.mkcalendar(path, login=PUB)
            self.put(path + "e1.ics", EVENT % {"uid": f"{name}-e1"}, login=PUB)

    def _create_token(self, target: str, login: str, check: int) -> str:
        _, _, answer = self.request(
            "POST", "/.sharing/v1/token/create", check=check, login=login,
            data=f"PathMapped=/calendar-publisher/{target}/",
            content_type="application/x-www-form-urlencoded", accept="text/plain")
        m = re.search(r"PathOrToken='([^']+)'", answer)
        return m.group(1) if m else ""

    def _enable(self, token: str) -> None:
        self.request("POST", "/.sharing/v1/token/enable", check=200, login=PUB,
                     data=f"PathOrToken={token}",
                     content_type="application/x-www-form-urlencoded", accept="text/plain")

    def test_real_config_resolves_tokens_but_does_not_permit_creating_them(self):
        s = _real_sharing_section()
        assert s.get("collection_by_token", "").lower() == "true"
        # Creation must stay rights-gated (T), never globally permitted.
        assert s.get("permit_create_token", "false").lower() == "false"

    def test_publisher_token_for_smb_sessions_reads_anonymously(self):
        token = self._create_token("smb-sessions", PUB, 200)
        assert token.startswith("/.token/")
        self._enable(token)
        _, _, body = self.request("GET", token, check=200)   # no login
        assert "UID:smb-sessions-e1" in body
        assert "earnings-e1" not in body

    def test_token_is_read_only(self):
        token = self._create_token("smb-sessions", PUB, 200)
        self._enable(token)
        self.put(token + "e2.ics", EVENT % {"uid": "intruder"}, check=403)
        self.delete(token + "e1.ics", check=403)

    def test_no_token_for_other_calendars(self):
        for name in ("earnings", "economic-indicators"):
            assert self._create_token(name, PUB, 403) == ""

    def test_ordinary_user_cannot_create_tokens(self):
        assert self._create_token("smb-sessions", USER, 403) == ""
        self.mkcalendar("/sal.cobian/private/", login=USER)
        _, _, answer = self.request(
            "POST", "/.sharing/v1/token/create", check=403, login=USER,
            data="PathMapped=/sal.cobian/private/",
            content_type="application/x-www-form-urlencoded", accept="text/plain")

    def test_normal_paths_stay_login_only(self):
        self.request("GET", "/calendar-publisher/smb-sessions/", check=401)
        self.request("GET", "/.token/v1/notarealtoken/", check=403)

    def test_list_csv_shape_that_create_smb_token_share_sh_parses(self):
        """create-smb-token-share.sh finds an existing share with
        awk -F';' '$1=="token" && $3==<target> {print $2}'."""
        token = self._create_token("smb-sessions", PUB, 200)
        _, _, csv = self.request("POST", "/.sharing/v1/token/list", check=200, login=PUB,
                                 data="", content_type="application/x-www-form-urlencoded",
                                 accept="text/csv")
        rows = [line.split(";") for line in csv.splitlines()]
        hits = [r[1] for r in rows
                if len(r) > 2 and r[0] == "token" and r[2] == "/calendar-publisher/smb-sessions/"]
        assert hits == [token]
