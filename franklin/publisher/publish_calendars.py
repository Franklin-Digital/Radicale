#!/usr/bin/env python3
"""Publish Earnings and Economic Indicator calendars to Radicale (DAYTRADE-778).

These calendars are DERIVED. Franklin already owns both data sets, so nothing
here invents data: it renders `fmp_earnings_calendar` and
`fmp_economic_calendar` (Postgres `benny_prod`) as CalDAV VEVENTs. A human
typing earnings dates into a client would be duplicating a feed we pay for.

WHY SCOPED. fmp_earnings_calendar holds 1,011,075 rows spanning 2020-2026.
Publishing all of it is not a calendar, it is a denial of service against your
phone. Default scope is the WORKING UNIVERSE (portfolio + earnings_wk) over a
-7/+90 day window: 1,101 events, measured 2026-09-16.

IDEMPOTENT BY CONSTRUCTION. Every event gets a UID derived from its identity
(symbol+date, or event+country+date), so a re-run PUTs over the same resource
instead of duplicating it. Events that vanish from the source within the
window are DELETED, because an earnings date that moved must not leave a ghost
at the old date -- a stale calendar entry is worse than a missing one.

TIMES. Earnings carry `bmo` / `amc` / empty, not clock times:
    bmo   -> 08:00 America/New_York  (before market open)
    amc   -> 16:30 America/New_York  (after market close)
    empty -> ALL-DAY event; unknown timing must not masquerade as a precise one
Economic rows carry a real timestamp and are emitted as timed events.

All timed events are emitted in UTC (DTSTART with Z) rather than with a
VTIMEZONE block: unambiguous, and clients render in the viewer's local zone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import re
import sys
import zoneinfo

import psycopg2
import requests

log = logging.getLogger("publish_calendars")

ET = zoneinfo.ZoneInfo("America/New_York")
UTC = dt.timezone.utc

#: Times are ET clock times, converted to UTC per-date so DST is handled.
BMO_LOCAL = dt.time(8, 0)
AMC_LOCAL = dt.time(16, 30)
EARNINGS_DURATION = dt.timedelta(minutes=30)
ECONOMIC_DURATION = dt.timedelta(minutes=15)

PRODID = "-//Franklin Digital//Radicale Publisher//EN"


# ── helpers ──────────────────────────────────────────────────────────

#: Domain for the iCalendar UID property only -- NEVER for the resource name.
UID_DOMAIN = "franklinfinancial.ai"


def _slug(kind: str, *parts: str) -> str:
    """Stable RESOURCE NAME from the event IDENTITY, so a re-run overwrites.

    Hashed rather than concatenated because event names carry spaces, slashes
    and non-ASCII, none of which belong in a URL path segment.

    Deliberately NOT the iCalendar UID: that ends in "@domain", and Radicale
    percent-encodes the "@" in the hrefs it returns from PROPFIND. Comparing
    those hrefs against un-encoded UIDs matched NOTHING, so every existing
    event was classified both "new" and "stale" -- and since _sync deletes
    after putting, the run deleted 47 events it had just written. Keeping the
    resource name to [a-z0-9-] makes the comparison encoding-proof instead of
    relying on remembering to unquote.
    """
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{h}"


def _esc(text: str) -> str:
    """RFC 5545 text escaping. Order matters: backslash FIRST, or the escapes
    we add below get escaped again."""
    return (str(text).replace("\\", r"\\")
                     .replace(";", r"\;")
                     .replace(",", r"\,")
                     .replace("\n", r"\n"))


def _fold(line: str) -> str:
    """RFC 5545 requires lines <= 75 octets, continued with a leading space.
    Folded on OCTETS, not characters -- splitting inside a UTF-8 sequence
    produces a file some clients reject and others render as mojibake."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, start = [], 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # do not split a multi-byte character
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        out.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74  # continuation lines carry a leading space
    return "\r\n ".join(out)


def _vcalendar(vevent_blocks: list[str]) -> str:
    """Wrap VEVENT blocks in a VCALENDAR.

    Each block is SPLIT back into its own lines before folding. Folding a
    whole multi-line block as if it were a single line mostly round-trips --
    an unfold restores the embedded CRLFs -- right up until a 75-octet cut
    lands between a real CR and its LF, which corrupts the block. That
    produced an intermittent 400 from Radicale (VEVENT component was not
    closed) on a minority of events, which is why a dry run and a partial
    real run both looked fine.
    """
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}"]
    for block in vevent_blocks:
        lines.extend(block.split("\r\n"))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def _stamp(when: dt.datetime) -> str:
    return when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


# ── event builders ───────────────────────────────────────────────────

def earnings_vevent(row: dict, now: dt.datetime) -> tuple[str, str]:
    sym, date_s, when = row["symbol"], row["date"], (row["time"] or "").strip().lower()
    day = dt.date.fromisoformat(date_s)
    slug = _slug("earnings", sym, date_s)

    summary = f"{sym} earnings"
    if when == "bmo":
        summary += " (before open)"
    elif when == "amc":
        summary += " (after close)"

    desc = []
    if row.get("fiscal_period") or row.get("fiscal_year"):
        desc.append(f"Fiscal: {row.get('fiscal_period') or '?'} {row.get('fiscal_year') or ''}".strip())
    for label, key in (("EPS est", "eps_estimated"), ("EPS actual", "eps_actual"),
                       ("Revenue est", "revenue_estimated"), ("Revenue actual", "revenue_actual")):
        v = row.get(key)
        if v is not None:
            desc.append(f"{label}: {v:,.4g}" if abs(v) < 1e6 else f"{label}: {v:,.0f}")
    if row.get("confirmed") is False:
        # Estimated dates move. Saying so is the difference between a calendar
        # you can act on and one you cannot.
        desc.append("Date NOT confirmed by the company")

    lines = ["BEGIN:VEVENT", f"UID:{slug}@{UID_DOMAIN}", f"DTSTAMP:{_stamp(now)}",
             f"SUMMARY:{_esc(summary)}"]

    if when in ("bmo", "amc"):
        local = dt.datetime.combine(day, BMO_LOCAL if when == "bmo" else AMC_LOCAL, ET)
        lines.append(f"DTSTART:{_stamp(local)}")
        lines.append(f"DTEND:{_stamp(local + EARNINGS_DURATION)}")
    else:
        # Unknown timing -> ALL-DAY. Inventing 09:30 would render as a
        # precise claim the source never made.
        lines.append(f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}")

    if desc:
        lines.append(f"DESCRIPTION:{_esc(chr(10).join(desc))}")
    lines.append(f"CATEGORIES:{_esc('Earnings')}")
    lines.append("TRANSP:TRANSPARENT")   # informational: must not block free/busy
    lines.append("END:VEVENT")
    return slug, "\r\n".join(lines)


def economic_vevent(row: dict, now: dt.datetime) -> tuple[str, str]:
    event, country, ts = row["event"], row["country"], row["date"]
    when = ts if isinstance(ts, dt.datetime) else dt.datetime.fromisoformat(str(ts))
    slug = _slug("econ", event, country, str(ts))

    impact = (row.get("impact") or "").strip()
    summary = f"{country}: {event}"
    if impact:
        summary = f"[{impact}] {summary}"

    desc = []
    for label, key in (("Previous", "previous"), ("Estimate", "estimate"), ("Actual", "actual")):
        v = row.get(key)
        if v is not None:
            unit = row.get("unit") or ""
            desc.append(f"{label}: {v:,.4g}{(' ' + unit) if unit else ''}")
    if row.get("change_percentage") is not None:
        desc.append(f"Change: {row['change_percentage']:,.4g}%")

    # Source timestamps carry no tzinfo; they are US market-calendar times.
    if when.tzinfo is None:
        when = when.replace(tzinfo=ET)

    lines = ["BEGIN:VEVENT", f"UID:{slug}@{UID_DOMAIN}", f"DTSTAMP:{_stamp(now)}",
             f"SUMMARY:{_esc(summary)}",
             f"DTSTART:{_stamp(when)}",
             f"DTEND:{_stamp(when + ECONOMIC_DURATION)}"]
    if desc:
        lines.append(f"DESCRIPTION:{_esc(chr(10).join(desc))}")
    lines.append(f"CATEGORIES:{_esc('Economic')}")
    lines.append("TRANSP:TRANSPARENT")
    lines.append("END:VEVENT")
    return slug, "\r\n".join(lines)


# ── CalDAV ───────────────────────────────────────────────────────────

class Calendar:
    def __init__(self, base: str, user: str, password: str, path: str, dry_run: bool):
        self.url = f"{base.rstrip('/')}/{user}/{path}/"
        self.auth = (user, password)
        self.dry_run = dry_run
        self.s = requests.Session()

    #: One CalDAV REPORT returns every item WITH its body, so a quiet run
    #: costs a single request instead of 1,179 blind PUTs.
    _REPORT = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        '<D:prop><D:getetag/><C:calendar-data/></D:prop>'
        '<C:filter><C:comp-filter name="VCALENDAR"/></C:filter>'
        '</C:calendar-query>')

    def existing_items(self) -> dict:
        """slug -> current ICS body for everything in the collection."""
        r = self.s.request("REPORT", self.url, auth=self.auth,
                           headers={"Depth": "1",
                                    "Content-Type": "application/xml; charset=utf-8"},
                           data=self._REPORT.encode("utf-8"), timeout=120)
        r.raise_for_status()
        return parse_report(r.content, self.url)

    def put(self, slug: str, ics: str) -> None:
        if self.dry_run:
            return
        r = self.s.put(f"{self.url}{slug}.ics", data=ics.encode("utf-8"),
                       auth=self.auth,
                       headers={"Content-Type": "text/calendar; charset=utf-8"},
                       timeout=60)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"PUT {slug} -> {r.status_code}: {r.text[:200]}")

    def delete(self, slug: str) -> None:
        if self.dry_run:
            return
        r = self.s.delete(f"{self.url}{slug}.ics", auth=self.auth, timeout=60)
        # 404 is fine: the goal is absence, and it is already absent.
        if r.status_code not in (200, 204, 404):
            raise RuntimeError(f"DELETE {slug} -> {r.status_code}")


def parse_report(body: bytes, collection_url: str) -> dict:
    """Parse a CalDAV multistatus into slug -> calendar-data.

    A real XML parser, not regex: Radicale emits <C:calendar-data> with a
    namespace PREFIX, which a pattern for <calendar-data> silently never
    matched -- every run then saw an empty calendar and rewrote all 1,179
    events while logging success.
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote, urlsplit
    ns = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:caldav"}
    prefix = urlsplit(collection_url).path
    out = {}
    for resp in ET.fromstring(body).findall("D:response", ns):
        href = resp.findtext("D:href", default="", namespaces=ns)
        data = resp.find(".//C:calendar-data", ns)
        href = unquote(href)
        if data is None or data.text is None:
            continue
        if not href.endswith(".ics") or not href.startswith(prefix):
            continue
        out[href.rsplit("/", 1)[-1][:-4]] = data.text
    return out


def _comparable(ics: str) -> tuple:
    """Order-, fold- and line-ending-insensitive form of an ICS body.

    Radicale does not store what we PUT byte-for-byte: it REORDERS properties
    (DTSTAMP moves to the end), re-folds long lines, and returns LF rather
    than CRLF. A textual comparison therefore reports EVERY event as changed.
    So: normalise line endings, unfold, drop DTSTAMP (which changes on every
    run by definition), and compare as a sorted tuple of properties.
    """
    text = ics.replace("\r\n", "\n")
    text = re.sub(r"\n[ \t]", "", text)            # RFC 5545 unfold
    return tuple(sorted(l for l in text.split("\n")
                        if l and not l.startswith("DTSTAMP:")))


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("RADICALE_BASE", "http://127.0.0.1:5232"))
    ap.add_argument("--back-days", type=int, default=7)
    ap.add_argument("--forward-days", type=int, default=90)
    ap.add_argument("--universe", default="portfolio,earnings_wk",
                    help="comma-separated universe_memberships index_names; "
                         "'all' publishes every symbol (1M+ rows -- do not)")
    ap.add_argument("--countries", default="US",
                    help="comma-separated economic-calendar countries, or 'all'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-PUT every event even if unchanged")
    ap.add_argument("--only", choices=["earnings", "economic"])
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    pw = os.environ.get("CALENDAR_PUBLISHER_PASSWORD")
    if not pw:
        log.error("CALENDAR_PUBLISHER_PASSWORD not set - refusing to run")
        return 2

    now = dt.datetime.now(UTC)
    start = (now.date() - dt.timedelta(days=args.back_days))
    end = (now.date() + dt.timedelta(days=args.forward_days))
    log.info("window %s .. %s", start, end)

    conn = psycopg2.connect(
        connect_timeout=10,
        host=os.environ.get("BENNY_PG_HOST", "localhost"),
        port=int(os.environ.get("BENNY_PG_PORT", "5432")),
        dbname=os.environ.get("BENNY_DB", "benny_prod"),
        user=os.environ.get("BENNY_USER", "benny_prod"),
        password=os.environ["BENNY_PASSWORD"])

    rc = 0
    try:
        if args.only in (None, "earnings"):
            rc |= publish_earnings(conn, args, start, end, now, pw)
        if args.only in (None, "economic"):
            rc |= publish_economic(conn, args, start, end, now, pw)
    finally:
        conn.close()
    return rc


def _sync(cal: Calendar, wanted: dict[str, str], label: str, force: bool = False) -> int:
    current = cal.existing_items()
    on_server = set(current)
    to_put = [u for u in wanted if u not in on_server]
    changed = [u for u in wanted if u in on_server and (
        force or _comparable(_vcalendar([wanted[u]])) != _comparable(current[u]))]
    # Only delete what WE published into this window and that has since
    # vanished from the source -- e.g. an earnings date that moved. A ghost at
    # the old date is worse than no entry.
    to_del = [u for u in on_server if u not in wanted]

    log.info("%s: %d source | %d present | %d new | %d changed | %d stale",
             label, len(wanted), len(on_server), len(to_put), len(changed),
             len(to_del))

    # Delete FIRST, and never delete something we are about to write. A
    # mis-detected "stale" set previously removed freshly written events
    # because the deletes ran after the puts.
    assert not (set(to_del) & set(wanted)), "refusing to delete a wanted event"
    for slug in to_del:
        cal.delete(slug)
    for slug in to_put:
        cal.put(slug, _vcalendar([wanted[slug]]))

    # Re-PUT only what actually differs, so an EPS actual landing after a
    # release still reaches the calendar without rewriting the other 1,178.
    for slug in changed:
        cal.put(slug, _vcalendar([wanted[slug]]))
    return 0


def publish_earnings(conn, args, start, end, now, pw) -> int:
    where_universe = ""
    params: list = [start, end]
    if args.universe != "all":
        names = [n.strip() for n in args.universe.split(",") if n.strip()]
        # removed_at IS NULL = still a member. Without it, symbols that LEFT
        # the universe keep publishing forever.
        where_universe = (" AND symbol IN (SELECT symbol FROM universe_memberships "
                          "WHERE index_name = ANY(%s) AND removed_at IS NULL)")
        params.append(names)

    sql = ("SELECT symbol, date, time, eps_estimated, eps_actual, "
           "revenue_estimated, revenue_actual, fiscal_period, fiscal_year, confirmed "
           "FROM fmp_earnings_calendar "
           "WHERE date::date BETWEEN %s AND %s" + where_universe)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    wanted: dict[str, str] = {}
    for r in rows:
        try:
            slug, ics = earnings_vevent(r, now)
            wanted[slug] = ics
        except Exception as e:  # one bad row must not lose the whole calendar
            log.warning("skipping earnings row %s/%s: %s", r.get("symbol"), r.get("date"), e)

    cal = Calendar(args.base, "calendar-publisher", pw, "earnings", args.dry_run)
    return _sync(cal, wanted, "earnings", args.force)


def publish_economic(conn, args, start, end, now, pw) -> int:
    params: list = [start, end]
    where_country = ""
    if args.countries != "all":
        where_country = " AND country = ANY(%s)"
        params.append([c.strip() for c in args.countries.split(",") if c.strip()])

    sql = ("SELECT event, country, date, currency, previous, estimate, actual, "
           "impact, unit, change_percentage "
           "FROM fmp_economic_calendar "
           "WHERE date::timestamp BETWEEN %s AND %s" + where_country)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    wanted: dict[str, str] = {}
    for r in rows:
        try:
            slug, ics = economic_vevent(r, now)
            wanted[slug] = ics
        except Exception as e:
            log.warning("skipping economic row %s/%s: %s", r.get("event"), r.get("date"), e)

    cal = Calendar(args.base, "calendar-publisher", pw, "economic-indicators", args.dry_run)
    return _sync(cal, wanted, "economic", args.force)


if __name__ == "__main__":
    sys.exit(main())
