"""Tests for the calendar publisher.

Each test is written so it can FAIL: the assertions name a specific wrong
output that the corresponding bug would produce, not just "something is there".
"""
import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import publish_calendars as pc
import pytest

NOW = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.timezone.utc)


def _row(**kw):
    base = dict(symbol="AAPL", date="2026-10-30", time="", eps_estimated=None,
                eps_actual=None, revenue_estimated=None, revenue_actual=None,
                fiscal_period=None, fiscal_year=None, confirmed=None)
    base.update(kw)
    return base


# ── time mapping ─────────────────────────────────────────────────────

def test_bmo_is_0800_et_in_daylight_time():
    _, ics = pc.earnings_vevent(_row(time="bmo"), NOW)
    # Oct 30 is EDT (UTC-4) -> 12:00Z. A naive UTC assumption gives 0800Z.
    assert "DTSTART:20261030T120000Z" in ics, ics


def test_bmo_is_0800_et_in_standard_time():
    _, ics = pc.earnings_vevent(_row(date="2026-12-10", time="bmo"), NOW)
    # Dec 10 is EST (UTC-5) -> 13:00Z. A fixed offset would give 12:00Z here
    # too, which is the DST bug this test exists to catch.
    assert "DTSTART:20261210T130000Z" in ics, ics


def test_amc_is_1630_et():
    _, ics = pc.earnings_vevent(_row(time="amc"), NOW)
    assert "DTSTART:20261030T203000Z" in ics, ics


def test_unknown_time_is_all_day_not_a_guessed_clock_time():
    _, ics = pc.earnings_vevent(_row(time=""), NOW)
    assert "DTSTART;VALUE=DATE:20261030" in ics, ics
    assert "DTSTART:2026" not in ics, "unknown timing must not become a timestamp"


def test_all_day_dtend_is_exclusive_next_day():
    _, ics = pc.earnings_vevent(_row(time=""), NOW)
    assert "DTEND;VALUE=DATE:20261031" in ics, ics


def test_unrecognised_time_token_falls_back_to_all_day():
    # A new vendor token must not be silently treated as bmo.
    _, ics = pc.earnings_vevent(_row(time="premarket"), NOW)
    assert "DTSTART;VALUE=DATE:20261030" in ics, ics


# ── UID stability ────────────────────────────────────────────────────

def test_uid_is_stable_across_runs():
    # Same day + same timing bucket -> same resource, so a re-run UPDATES. A
    # landed EPS actual, a later DTSTAMP, or another company joining the day
    # must not fork it.
    a, _ = pc.earnings_vevent(_row(time="bmo"), NOW)
    b, _ = pc.earnings_vevent(_row(time="bmo", eps_actual=1.5), NOW + dt.timedelta(days=1))
    c, _ = pc.earnings_group_vevent([_row(time="bmo"), _row(symbol="MSFT", time="bmo")], NOW)
    assert a == b == c


def test_before_open_and_after_close_are_separate_events():
    a, _ = pc.earnings_vevent(_row(time="bmo"), NOW)
    b, _ = pc.earnings_vevent(_row(time="amc"), NOW)
    assert a != b

def test_uid_differs_for_a_moved_date():
    a, _ = pc.earnings_vevent(_row(date="2026-10-30"), NOW)
    b, _ = pc.earnings_vevent(_row(date="2026-10-31"), NOW)
    assert a != b


def test_slug_is_url_safe():
    uid, _ = pc.economic_vevent(
        dict(event="Non/Farm Payrolls, s.a.", country="US",
             date=dt.datetime(2026, 10, 2, 8, 30), previous=None, estimate=None,
             actual=None, impact="High", unit=None, change_percentage=None), NOW)
    assert "/" not in uid and " " not in uid and "," not in uid, uid


# ── escaping / folding ───────────────────────────────────────────────

def test_special_characters_are_escaped():
    assert pc._esc("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_backslash_escaped_first():
    # If ';' were escaped before '\\', the added backslash would be doubled.
    assert pc._esc(";") == "\\;"
    assert pc._esc("\\;") == "\\\\\\;"


def test_fold_respects_75_octets():
    line = "DESCRIPTION:" + "x" * 200
    out = pc._fold(line)
    for seg in out.split("\r\n "):
        assert len(seg.encode()) <= 75, seg


def test_fold_does_not_split_a_utf8_character():
    line = "SUMMARY:" + "é" * 80
    out = pc._fold(line)
    # Would raise UnicodeDecodeError inside _fold if it split mid-sequence.
    assert "".join(out.split("\r\n ")) == line


def test_long_event_name_survives_a_round_trip():
    row = dict(event="E" * 300, country="US", date=dt.datetime(2026, 10, 2, 8, 30),
               previous=None, estimate=None, actual=None, impact="High",
               unit=None, change_percentage=None)
    _, ics = pc.economic_vevent(row, NOW)
    ics = pc._vcalendar([ics])
    unfolded = ics.replace("\r\n ", "")
    assert "E" * 300 in unfolded


# ── content ──────────────────────────────────────────────────────────

def test_unconfirmed_date_is_flagged():
    _, ics = pc.earnings_vevent(_row(confirmed=False), NOW)
    assert "date not confirmed" in ics


def test_confirmed_date_is_not_flagged():
    _, ics = pc.earnings_vevent(_row(confirmed=True), NOW)
    assert "not confirmed" not in ics


def test_events_are_transparent():
    # Informational events must not make the user look busy.
    _, ics = pc.earnings_vevent(_row(), NOW)
    assert "TRANSP:TRANSPARENT" in ics


def _econ(event, ts, impact="High", **kw):
    row = dict(event=event, country="US", date=ts, previous=None, estimate=None,
               actual=None, impact=impact, unit=None, change_percentage=None)
    row.update(kw)
    return row


@pytest.mark.parametrize("stored, et_clock", [
    # FMP stores UTC. Release clock times are public and fixed:
    ("2026-09-16 18:00:00", "20260916T180000Z"),   # FOMC decision, 14:00 EDT
    ("2026-09-16 12:30:00", "20260916T123000Z"),   # retail sales, 08:30 EDT
    ("2026-12-11 13:30:00", "20261211T133000Z"),   # 08:30 EST in winter
])
def test_economic_timestamps_are_utc_not_eastern(stored, et_clock):
    _, ics = pc.economic_vevent(_econ("X", dt.datetime.fromisoformat(stored)), NOW)
    # Reading them as Eastern put every event 4h late (5h in winter).
    assert f"DTSTART:{et_clock}" in ics, ics


def test_fomc_description_states_the_eastern_clock_time():
    _, ics = pc.economic_vevent(_econ("Fed Interest Rate Decision",
                                      dt.datetime(2026, 9, 16, 18, 0)), NOW)
    assert "2:00 PM ET" in ics.replace("\r\n ", "")

def test_vcalendar_wraps_and_terminates():
    _, ev = pc.earnings_vevent(_row(), NOW)
    out = pc._vcalendar([ev])
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.endswith("END:VCALENDAR\r\n")
    assert out.count("BEGIN:VEVENT") == 1


# ── structural validity (the 400 Bad Request regression) ─────────────
#
# Radicale parses uploads with vobject and rejects anything it cannot read.
# The publisher once folded each whole VEVENT block as if it were a single
# line; an unfold restored the embedded CRLFs, so MOST events round-tripped
# and only those where a 75-octet cut landed between a CR and its LF were
# corrupted. Sweeping the length is what makes this reproducible -- a single
# fixed-size fixture passes straight through the bug.

import pytest

#: Parse with RADICALE's reader, not plain vobject.readOne(). vobject is
#: lenient: it accepted the corrupted payload (a blank line plus a folded
#: "END:VEVENT") that Radicale rejected with 400. A test oracle weaker than
#: the server it stands in for reports a pass the server will not honour --
#: which is exactly how the first version of these tests went green against
#: the very bug they were written for.
from radicale.item import read_components


def _parse(ics):
    comps = read_components(ics)
    assert len(comps) == 1, comps
    return comps[0]


def _body_lines(ics):
    assert ics.endswith("\r\n")
    return ics[:-2].split("\r\n")


def test_output_contains_no_blank_lines():
    """The 400 regression: a fold landing on a CRLF emitted an empty line."""
    row = _row(fiscal_period="Q2", fiscal_year="2026", eps_estimated=-0.465,
               revenue_estimated=14057370.0, confirmed=True)
    _, ev = pc.earnings_vevent(row, NOW)
    for line in _body_lines(pc._vcalendar([ev])):
        assert line != "", "blank line in ICS output"


def test_structural_lines_are_never_folded():
    """BEGIN/END markers are short; if one gets folded, the block is corrupt."""
    row = _row(fiscal_period="Q2", fiscal_year="2026", eps_estimated=-0.465,
               revenue_estimated=14057370.0, confirmed=True)
    _, ev = pc.earnings_vevent(row, NOW)
    lines = _body_lines(pc._vcalendar([ev]))
    for marker in ("BEGIN:VCALENDAR", "BEGIN:VEVENT", "END:VEVENT", "END:VCALENDAR"):
        assert marker in lines, f"{marker} is not an intact line: {lines}"


@pytest.mark.parametrize("n", range(0, 260, 7))
def test_earnings_event_parses_at_every_length(n):
    row = _row(symbol="A" * max(1, n % 12 + 1), fiscal_period="Q" + "x" * n,
               fiscal_year="2026", eps_estimated=1.2345)
    _, ev = pc.earnings_vevent(row, NOW)
    cal = _parse(pc._vcalendar([ev]))
    assert len(cal.vevent_list) == 1


@pytest.mark.parametrize("n", range(0, 260, 7))
def test_economic_event_parses_at_every_length(n):
    row = dict(event="E" * (n + 1), country="US",
               date=dt.datetime(2026, 10, 2, 8, 30), previous=1.0,
               estimate=2.0, actual=3.0, impact="High", unit="%",
               change_percentage=0.5)
    _, ev = pc.economic_vevent(row, NOW)
    cal = _parse(pc._vcalendar([ev]))
    assert len(cal.vevent_list) == 1


def test_parsed_values_survive_folding_intact():
    long_name = "Nonfarm Payrolls, seasonally adjusted; " + "Q" * 200
    row = dict(event=long_name, country="US",
               date=dt.datetime(2026, 10, 2, 8, 30), previous=None,
               estimate=None, actual=None, impact="High", unit=None,
               change_percentage=None)
    _, ev = pc.economic_vevent(row, NOW)
    ve = _parse(pc._vcalendar([ev])).vevent
    # Round-trips through fold AND through the ; and , escaping.
    assert ve.summary.value == "\U0001F534 " + long_name


# ── the exact payload Radicale rejected, plus a 1-byte alignment sweep ──
#
# Folding corruption only appears when a 75-octet cut lands exactly on a
# CRLF. Whether that happens depends on the TOTAL byte length of everything
# before it, so a fixture with arbitrary field values will usually miss it --
# two earlier versions of these tests went green against this very bug. The
# sweep below steps length by ONE byte, which cannot skip an alignment.

ANAB = dict(symbol="ANAB", date="2026-09-16", time="bmo", eps_estimated=-0.465,
            eps_actual=None, revenue_estimated=14057370.0, revenue_actual=None,
            fiscal_period="Q2", fiscal_year="2026", confirmed=True)
ANAB_STAMP = dt.datetime(2026, 9, 16, 7, 39, 23, tzinfo=dt.timezone.utc)


def test_the_exact_event_radicale_rejected_is_well_formed():
    _, ev = pc.earnings_vevent(ANAB, ANAB_STAMP)
    ics = pc._vcalendar([ev])
    assert "\r\n\r\n" not in ics, "blank line: a fold cut landed on a CRLF"
    assert "\r\n END:VEVENT" not in ics, "END:VEVENT was folded"
    _parse(ics)


@pytest.mark.parametrize("pad", range(0, 90))
def test_no_fold_alignment_corrupts_the_block(pad):
    row = dict(ANAB, fiscal_period="Q" + "x" * pad)
    _, ev = pc.earnings_vevent(row, ANAB_STAMP)
    ics = pc._vcalendar([ev])
    assert "\r\n\r\n" not in ics, f"blank line at pad={pad}"
    lines = ics[:-2].split("\r\n")
    for marker in ("BEGIN:VEVENT", "END:VEVENT", "END:VCALENDAR"):
        assert marker in lines, f"{marker} folded at pad={pad}"
    _parse(ics)


@pytest.mark.parametrize("pad", range(0, 90))
def test_economic_no_fold_alignment_corrupts_the_block(pad):
    row = dict(event="CPI" + "x" * pad, country="US",
               date=dt.datetime(2026, 10, 2, 8, 30), previous=1.0, estimate=2.0,
               actual=3.0, impact="High", unit="%", change_percentage=0.5)
    _, ev = pc.economic_vevent(row, ANAB_STAMP)
    ics = pc._vcalendar([ev])
    assert "\r\n\r\n" not in ics, f"blank line at pad={pad}"
    lines = ics[:-2].split("\r\n")
    for marker in ("BEGIN:VEVENT", "END:VEVENT", "END:VCALENDAR"):
        assert marker in lines, f"{marker} folded at pad={pad}"
    _parse(ics)


# ── resource-name safety and the self-deletion regression ────────────
#
# Radicale percent-encodes hrefs. The publisher originally used the full
# iCalendar UID ("...@franklinfinancial.ai") as the resource name, so the
# "@" came back as "%40", matched nothing, and every event was classified
# BOTH new and stale -- deleting 47 events it had just written.

import string
import re as _re

_SAFE = set(string.ascii_lowercase + string.digits + "-")


@pytest.mark.parametrize("bad", [
    "Non-Farm Payrolls, s.a.", "A/B Test", "caf\u00e9 earnings", "a b c",
    "100% of GDP", "x?y&z", "q#1", "back" + chr(92) + "slash", "plus+one",
])
def test_slug_is_always_url_safe(bad):
    slug = pc._slug("econ", bad, "US", "2026-10-02 08:30:00")
    assert set(slug) <= _SAFE, slug
    # Must survive a percent-encoding round trip UNCHANGED, or the href
    # returned by PROPFIND will not equal the name we wrote.
    from urllib.parse import quote, unquote
    assert quote(slug, safe="") == slug
    assert unquote(slug) == slug


def test_uid_property_keeps_the_domain_but_resource_name_does_not():
    slug, ics = pc.earnings_vevent(_row(), NOW)
    assert "@" not in slug
    assert f"UID:{slug}@{pc.UID_DOMAIN}" in ics


def test_slug_matches_the_href_basename_radicale_returns():
    """The exact comparison _sync makes, against a real Radicale href."""
    from urllib.parse import quote, unquote
    slug, _ = pc.economic_vevent(
        dict(event="Non-Farm Payrolls, s.a.", country="US",
             date=dt.datetime(2026, 10, 2, 8, 30), previous=None,
             estimate=None, actual=None, impact="High", unit=None,
             change_percentage=None), NOW)
    href = "/calendar-publisher/economic-indicators/" + quote(slug + ".ics")
    recovered = unquote(href).rsplit("/", 1)[-1][:-4]
    assert recovered == slug


# ── change detection against a REAL Radicale REPORT response ─────────
#
# fixtures_report_earnings.xml is a verbatim 207 from our server (224 items).
# Two bugs lived here: a regex that missed the <C:calendar-data> namespace
# prefix (every run saw an empty calendar and rewrote everything), and a
# textual comparison that could never match because Radicale reorders
# properties, re-folds, and returns LF.

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_report_earnings.xml")
COLL = "http://127.0.0.1:5232/calendar-publisher/earnings/"


def _fixture_items():
    with open(FIXTURE, "rb") as f:
        return pc.parse_report(f.read(), COLL)


def test_parse_report_reads_every_item_from_the_real_response():
    items = _fixture_items()
    assert len(items) == 224
    assert all(set(k) <= _SAFE for k in items)
    assert all("BEGIN:VEVENT" in v for v in items.values())


def test_parse_report_ignores_hrefs_outside_the_collection():
    with open(FIXTURE, "rb") as f:
        assert pc.parse_report(f.read(), "http://h/calendar-publisher/economic-indicators/") == {}


GROUPED_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_report_earnings_grouped.xml")
GROUPED_ROWS = [
    dict(symbol="ANAB", date="2027-01-20", time="bmo", eps_estimated=-0.465, eps_actual=None,
         revenue_estimated=14057370.0, revenue_actual=None, fiscal_period="Q4",
         fiscal_year="2026", confirmed=True),
    dict(symbol="ALOT", date="2027-01-20", time="bmo", eps_estimated=0.04, eps_actual=None,
         revenue_estimated=29189000.0, revenue_actual=None, fiscal_period="Q2",
         fiscal_year="2027", confirmed=False),
]


def _grouped_stored():
    """Verbatim Radicale REPORT for GROUPED_ROWS (captured from a staging
    server by PUTting exactly this group), so the comparison is against real
    server output: reordered properties, re-folded, LF line endings."""
    with open(GROUPED_FIXTURE, "rb") as f:
        items = pc.parse_report(f.read(), "http://127.0.0.1:5299/calendar-publisher/fixture-test/")
    assert len(items) == 1, items.keys()
    return next(iter(items.items()))


def test_server_reordered_body_compares_equal_to_what_we_generate():
    stored_slug, stored = _grouped_stored()
    slug, ev = pc.earnings_group_vevent(list(reversed(GROUPED_ROWS)), NOW, {"ANAB"})  # other order, other DTSTAMP
    assert slug == stored_slug
    assert pc._comparable(pc._vcalendar([ev])) == pc._comparable(stored)

def test_a_real_content_change_is_detected():
    _, stored = _grouped_stored()
    rows = [dict(GROUPED_ROWS[0], eps_actual=-0.40), GROUPED_ROWS[1]]   # an actual landed
    _, ev = pc.earnings_group_vevent(rows, NOW, {"ANAB"})
    assert pc._comparable(pc._vcalendar([ev])) != pc._comparable(stored)

# ── grouping: one event per release time ─────────────────────────────

RETAIL = dt.datetime(2026, 9, 16, 12, 30)


def _retail_group():
    return [
        _econ("Retail Sales YoY (Aug)", RETAIL, "Medium"),
        _econ("Retail Sales MoM (Aug)", RETAIL, "High", estimate=0.3, previous=0.5, unit="%"),
        _econ("Retail Sales Ex Autos MoM (Aug)", RETAIL, "High"),
        _econ("Import Prices MoM (Aug)", RETAIL, "Low"),
    ]


def test_rows_at_the_same_time_collapse_to_one_event():
    groups = pc.group_economic_rows(_retail_group() + [_econ("FOMC", dt.datetime(2026, 9, 16, 18))])
    assert sorted(len(v) for v in groups.values()) == [1, 4]


def test_title_is_the_most_important_release_plus_count():
    _, ics = pc.economic_release_vevent(_retail_group(), NOW)
    ve = _parse(pc._vcalendar([ics])).vevent
    # highest impact first, then the shortest (most general) name
    assert ve.summary.value == "\U0001F534 Retail Sales MoM (Aug) +3"


def test_every_release_is_listed_in_the_notes_most_important_first():
    _, ics = pc.economic_release_vevent(_retail_group(), NOW)
    desc = _parse(pc._vcalendar([ics])).vevent.description.value.split("\n")
    assert desc[0] == "4 releases at 8:30 AM ET"
    assert [l.split(" ", 1)[1].split(" - ")[0] for l in desc[1:]] == [
        "Retail Sales MoM (Aug)", "Retail Sales Ex Autos MoM (Aug)",
        "Retail Sales YoY (Aug)", "Import Prices MoM (Aug)"]
    assert "est 0.3%, prev 0.5%" in desc[1]


def test_group_slug_is_stable_regardless_of_row_order_or_values():
    rows = _retail_group()
    a, _ = pc.economic_release_vevent(rows, NOW)
    b, _ = pc.economic_release_vevent(list(reversed(rows)), NOW)
    changed = [dict(r, actual=0.7) for r in rows]
    c, _ = pc.economic_release_vevent(changed, NOW)
    assert a == b == c


def test_non_us_release_names_its_country():
    _, ics = pc.economic_release_vevent([dict(_econ("CPI YoY", RETAIL), country="GB")], NOW)
    assert "SUMMARY:GB: " in ics


def test_headline_prefers_the_release_with_a_consensus_estimate():
    ts = dt.datetime(2026, 9, 16, 18, 0)
    rows = [_econ("FOMC Economic Projections", ts),
            _econ("Fed Interest Rate Decision", ts, estimate=4.0)]
    _, ics = pc.economic_release_vevent(rows, NOW)
    summary = _parse(pc._vcalendar([ics])).vevent.summary.value
    assert summary == "\U0001F534 Fed Interest Rate Decision +1"


# ── earnings grouping: one event per day and timing bucket ───────────

def test_same_day_same_bucket_collapses_other_buckets_do_not():
    rows = [_row(symbol=x, time="bmo") for x in ("AAA", "BBB", "CCC")] + \
           [_row(symbol="DDD", time="amc"), _row(symbol="EEE", time=""),
            _row(symbol="FFF", time=None), _row(symbol="GGG", date="2026-10-31", time="bmo")]
    groups = pc.group_earnings_rows(rows)
    assert sorted((k, len(v)) for k, v in groups.items()) == [
        (("2026-10-30", "amc"), 1), (("2026-10-30", "bmo"), 3),
        (("2026-10-30", "tbd"), 2), (("2026-10-31", "bmo"), 1)]


def test_title_stars_portfolio_first_and_counts_the_rest():
    rows = [_row(symbol=x, time="bmo") for x in ("ZZZ", "AAA", "NVDA", "BBB", "CCC")]
    _, ics = pc.earnings_group_vevent(rows, NOW, {"NVDA"})
    ve = _parse(pc._vcalendar([ics])).vevent
    assert ve.summary.value == "\U0001F305 Before open: \u2605NVDA, AAA, BBB +2"


def test_notes_list_every_company_with_estimates():
    rows = [_row(symbol="AAA", time="amc", eps_estimated=1.25, revenue_estimated=2_500_000_000.0,
                 fiscal_period="Q3", fiscal_year="2026"),
            _row(symbol="BBB", time="amc", confirmed=False)]
    _, ics = pc.earnings_group_vevent(rows, NOW)
    desc = _parse(pc._vcalendar([ics])).vevent.description.value.split("\n")
    assert desc[0] == "2 companies report after the close (4:30 PM ET)"
    assert desc[1] == "AAA - Q3 2026 \u00b7 EPS est 1.25 \u00b7 Rev est 2,500,000,000"
    assert desc[2] == "BBB - date not confirmed"


def test_unparseable_date_row_is_skipped_not_fatal():
    groups = pc.group_earnings_rows([_row(date="not-a-date"), _row(symbol="OK")])
    assert [len(v) for v in groups.values()] == [1]
