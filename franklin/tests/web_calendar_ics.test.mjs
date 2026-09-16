// node --test franklin/tests/web_calendar_ics.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEvents, parseDate, unescapeText, unfold } from
  "../../radicale/web/internal_data/calendar/ics.js";

// A verbatim-shaped event as the publisher writes it (folded, escaped, UTC).
const FOMC = [
  "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Franklin Digital//Radicale Publisher//EN",
  "BEGIN:VEVENT",
  "UID:econ-0123456789abcdef0123@franklinfinancial.ai",
  "DTSTAMP:20260916T120000Z",
  "SUMMARY:\u{1F534} Fed Interest Rate Decision +6",
  "DTSTART:20260916T180000Z",
  "DTEND:20260916T181500Z",
  "DESCRIPTION:7 releases at 2:00 PM ET\\n\u{1F534} Fed Interest Rate Decision - est 4%\\, prev 3.75%\\nInterest Rate Projec",
  " tion - 1st Yr\\; prev 3.6%",
  "CATEGORIES:Economic",
  "END:VEVENT",
  "END:VCALENDAR", ""].join("\r\n");

test("UTC timestamp is an exact instant, not local time", () => {
  const [ev] = parseEvents(FOMC);
  assert.equal(ev.start.toISOString(), "2026-09-16T18:00:00.000Z");
  assert.equal(ev.end.toISOString(), "2026-09-16T18:15:00.000Z");
  assert.equal(ev.allDay, false);
});

test("summary keeps emoji; description unfolds and unescapes", () => {
  const [ev] = parseEvents(FOMC);
  assert.equal(ev.summary, "\u{1F534} Fed Interest Rate Decision +6");
  assert.equal(ev.description,
    "7 releases at 2:00 PM ET\n\u{1F534} Fed Interest Rate Decision - est 4%, prev 3.75%\n" +
    "Interest Rate Projection - 1st Yr; prev 3.6%");
});

test("VALUE=DATE earnings event is all-day on its calendar date", () => {
  const ics = "BEGIN:VEVENT\r\nUID:e1\r\nSUMMARY:GVH earnings\r\n" +
    "DTSTART;VALUE=DATE:20260917\r\nDTEND;VALUE=DATE:20260918\r\nEND:VEVENT\r\n";
  const [ev] = parseEvents(ics);
  assert.equal(ev.allDay, true);
  assert.deepEqual([ev.start.getFullYear(), ev.start.getMonth(), ev.start.getDate()], [2026, 8, 17]);
});

test("escaped backslash is not turned into a newline", () => {
  assert.equal(unescapeText("a\\\\nb"), "a\\nb");
  assert.equal(unescapeText("x\\,y\\;z\\nw"), "x,y;z\nw");
});

test("nested VALARM properties do not overwrite the event's", () => {
  const ics = "BEGIN:VEVENT\r\nUID:u\r\nSUMMARY:Real\r\nDTSTART:20261001T130000Z\r\n" +
    "BEGIN:VALARM\r\nDESCRIPTION:Reminder text\r\nSUMMARY:Alarm\r\nEND:VALARM\r\n" +
    "DESCRIPTION:Real notes\r\nEND:VEVENT\r\n";
  const [ev] = parseEvents(ics);
  assert.equal(ev.summary, "Real");
  assert.equal(ev.description, "Real notes");
});

test("colon inside a quoted parameter does not split the value", () => {
  const ics = 'BEGIN:VEVENT\r\nUID:u\r\nDTSTART;TZID="America/New_York:x":20261001T090000\r\nSUMMARY:T\r\nEND:VEVENT\r\n';
  const [ev] = parseEvents(ics);
  assert.ok(ev.start instanceof Date && !Number.isNaN(ev.start.getTime()));
});

test("events without DTSTART are dropped, multiple events all parsed", () => {
  const ics = "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:No start\r\nEND:VEVENT\r\n" +
    "BEGIN:VEVENT\r\nUID:b\r\nDTSTART:20261001T130000Z\r\nEND:VEVENT\r\n" +
    "BEGIN:VEVENT\r\nUID:c\r\nDTSTART:20261002T130000Z\r\nEND:VEVENT\r\n";
  assert.deepEqual(parseEvents(ics).map((e) => e.uid), ["b", "c"]);
});

test("unfold handles LF-only responses (Radicale returns LF)", () => {
  assert.equal(unfold("A:1\n 2\nB:3"), "A:12\nB:3");
  assert.equal(parseDate("garbage"), null);
});
