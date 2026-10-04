#!/usr/bin/env python3
"""Fetch the SMB Inside Access session calendar (rt.smbtraining.com/calendar).

Runs on the DGX ONLY. The rt.smbtraining.com session cookie exists there and
nowhere else (Sal, 2026-09-22), so this script is the one place that talks to
rt. It prints a SANITISED JSON list on stdout, which the calendar-publisher
job carries back to mac-pro for publish_calendars.py --smb-json.

What is kept: the meeting id, title, type, webinar_type, start/end (UTC),
status, duration.
What is DROPPED: extendedProps.url (the webinar join link), webinar_id and
occurrence_id. A join link belongs to a member account and is not ours to
republish on a calendar every Franklin user can read; the event points at
rt.smbtraining.com/calendar instead, where a signed-in member joins.

Standard library only: the DGX runs it with the system python3.

  fetch_smb_meetings.py [--back-days 7] [--forward-days 60] > meetings.json

Exit: 0 ok · 1 error · 3 cookie rejected (refresh: smb-cookie smb)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://rt.smbtraining.com/api/meetings"
COOKIE = os.path.expanduser("~/.config/franklin/smb_cookie")
KEEP = ("id", "title", "type", "start", "end")


def sanitise(items: list) -> list:
    """Reduce rt's FullCalendar events to what a calendar needs. Never the join link."""
    out = []
    for i in items:
        ep = i.get("extendedProps") or {}
        if not isinstance(ep, dict):
            ep = {}
        m = {k: i.get(k) for k in KEEP}
        m["status"] = ep.get("status")
        m["duration"] = ep.get("duration")
        # webinar_type, not type: rt reports Easy Money Trades with
        # type=monster_trades but webinar_type=easy_money. The dispatcher maps
        # this to an archive category, so it needs the distinguishing one.
        m["webinar_type"] = ep.get("webinar_type") or i.get("type")
        if not (m["id"] and m["title"] and m["start"] and m["end"]):
            continue                      # an event with no time is not a calendar entry
        out.append(m)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--back-days", type=int, default=7)
    ap.add_argument("--forward-days", type=int, default=60)
    a = ap.parse_args()

    try:
        cookie = open(COOKIE).read().strip()
    except OSError as e:
        print(f"error: no rt cookie at {COOKIE} ({e.strerror})", file=sys.stderr)
        return 1
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=a.back_days)
    end = today + dt.timedelta(days=a.forward_days)
    req = urllib.request.Request(f"{API}?start={start}&end={end}", headers={
        "Cookie": cookie, "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            items = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"COOKIE REJECTED (HTTP {e.code}) by rt.smbtraining.com. "
                  f"Refresh it: smb-cookie smb", file=sys.stderr)
            return 3
        print(f"error: {API} -> HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:                                    # noqa: BLE001
        print(f"error: {API} -> {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not isinstance(items, list):
        print(f"error: expected a JSON list from {API}, got {type(items).__name__}", file=sys.stderr)
        return 1

    meetings = sanitise(items)
    json.dump({"window": [str(start), str(end)],
               "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "meetings": meetings}, sys.stdout, indent=1)
    print(f"{len(meetings)} meeting(s) {start} .. {end}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
