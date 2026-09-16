// Franklin web calendar view (DAYTRADE-778).
//
// Read-only. Talks CalDAV to this same Radicale server:
//   PROPFIND /<user>/  (Depth 1)  -> the user's calendars, INCLUDING the shared
//                                    Earnings / Economic Indicator calendars
//                                    that share-by-group maps into every home
//   REPORT   <calendar>           -> events in the visible range, recurrences
//                                    expanded SERVER-side (<C:expand>), so the
//                                    parser below only sees concrete instances
//
// Credentials live in memory for the lifetime of the tab and are never written
// to localStorage/sessionStorage. Only the chosen VIEW and hidden-calendar set
// are remembered locally.

import { parseEvents } from "./ics.js";

const DAV = "DAV:";
const CALDAV = "urn:ietf:params:xml:ns:caldav";
const ICAL = "http://apple.com/ns/ical/";
const PALETTE = ["#0a66c2", "#2e7d32", "#c62828", "#6a1b9a", "#ef6c00", "#00838f", "#ad1457", "#5d4037"];

const state = { user: null, auth: null, calendars: [], hidden: new Set(), fc: null };

const $ = (id) => document.getElementById(id);

function store(key, value) {
  try { localStorage.setItem("franklin-cal." + key, JSON.stringify(value)); } catch { /* optional */ }
}
function recall(key, fallback) {
  try { const v = localStorage.getItem("franklin-cal." + key); return v === null ? fallback : JSON.parse(v); }
  catch { return fallback; }
}

function basicAuth(user, password) {
  const bytes = new TextEncoder().encode(`${user}:${password}`);
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return "Basic " + btoa(bin);
}

async function dav(method, path, body, depth) {
  const headers = { Authorization: state.auth, "Content-Type": "application/xml; charset=utf-8" };
  if (depth !== undefined) headers.Depth = String(depth);
  const res = await fetch(path, { method, headers, body, credentials: "omit", cache: "no-store" });
  return res;
}

// ── sign in ──────────────────────────────────────────────────────────

const PROPFIND_CALENDARS = `<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:I="http://apple.com/ns/ical/">
  <D:prop><D:displayname/><D:resourcetype/><I:calendar-color/></D:prop>
</D:propfind>`;

async function loadCalendars() {
  const home = `/${encodeURIComponent(state.user)}/`;
  const res = await dav("PROPFIND", home, PROPFIND_CALENDARS, 1);
  if (res.status === 401 || res.status === 403) {
    const err = new Error("Username or password is incorrect.");
    err.auth = true;
    throw err;
  }
  if (res.status !== 207) throw new Error(`Server returned ${res.status} listing calendars.`);
  const doc = new DOMParser().parseFromString(await res.text(), "application/xml");
  const calendars = [];
  for (const r of doc.getElementsByTagNameNS(DAV, "response")) {
    const href = r.getElementsByTagNameNS(DAV, "href")[0]?.textContent;
    if (!href || href === home) continue;
    const ok = [...r.getElementsByTagNameNS(DAV, "propstat")].find(
      (ps) => /\s200\s/.test(ps.getElementsByTagNameNS(DAV, "status")[0]?.textContent || ""));
    if (!ok) continue;
    if (!ok.getElementsByTagNameNS(CALDAV, "calendar").length) continue;   // not a calendar
    const name = ok.getElementsByTagNameNS(DAV, "displayname")[0]?.textContent
      || decodeURIComponent(href.replace(/\/$/, "").split("/").pop());
    const rawColor = ok.getElementsByTagNameNS(ICAL, "calendar-color")[0]?.textContent || "";
    calendars.push({ href, name, color: /^#[0-9a-f]{6}/i.test(rawColor) ? rawColor.slice(0, 7) : null });
  }
  // Franklin's shared calendars first, then the user's own, alphabetically.
  calendars.sort((a, b) => (/\/franklin-/.test(b.href) - /\/franklin-/.test(a.href))
    || a.name.localeCompare(b.name));
  calendars.forEach((c, i) => { c.color = c.color || PALETTE[i % PALETTE.length]; });
  return calendars;
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = $("user").value.trim();
  const password = $("password").value;
  $("login-error").textContent = "";
  const button = e.submitter || e.target.querySelector("button");
  button.disabled = true;
  state.user = user;
  state.auth = basicAuth(user, password);
  try {
    state.calendars = await loadCalendars();
    $("password").value = "";
    showApp();
  } catch (err) {
    state.user = state.auth = null;
    $("login-error").textContent = err.auth ? err.message : `Could not sign in: ${err.message}`;
  } finally {
    button.disabled = false;
  }
});

$("logout").addEventListener("click", () => {
  state.user = state.auth = null;
  state.calendars = [];
  if (state.fc) { state.fc.destroy(); state.fc = null; }
  $("calendars").replaceChildren();
  $("app").hidden = true;
  $("login").hidden = false;
  $("user").focus();
});

// ── calendar view ────────────────────────────────────────────────────

function fmtUTC(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

function reportFor(start, end) {
  const s = fmtUTC(start), e = fmtUTC(end);
  return `<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><C:calendar-data><C:expand start="${s}" end="${e}"/></C:calendar-data></D:prop>
  <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">
    <C:time-range start="${s}" end="${e}"/>
  </C:comp-filter></C:comp-filter></C:filter>
</C:calendar-query>`;
}

function sourceFor(cal) {
  return {
    id: cal.href,
    color: cal.color,
    async events(info, success, failure) {
      try {
        const res = await dav("REPORT", cal.href, reportFor(info.start, info.end), 1);
        if (res.status !== 207) throw new Error(`${cal.name}: HTTP ${res.status}`);
        const doc = new DOMParser().parseFromString(await res.text(), "application/xml");
        const out = [];
        for (const node of doc.getElementsByTagNameNS(CALDAV, "calendar-data")) {
          for (const ev of parseEvents(node.textContent || "")) {
            out.push({
              id: `${cal.href}|${ev.uid}|${ev.start.toISOString()}`,
              title: ev.summary || "(no title)",
              start: ev.start,
              end: ev.end || undefined,
              allDay: ev.allDay,
              extendedProps: { notes: ev.description, calendar: cal.name },
            });
          }
        }
        success(out);
      } catch (err) {
        setStatus(`Could not load ${cal.name}: ${err.message}`);
        failure(err);
      }
    },
  };
}

function setStatus(msg) { $("status").textContent = msg || ""; }

function renderCalendarList() {
  const box = $("calendars");
  box.replaceChildren();
  for (const cal of state.calendars) {
    const label = document.createElement("label");
    label.className = "cal-toggle";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !state.hidden.has(cal.href);
    const sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = cal.color;
    const name = document.createElement("span");
    name.textContent = cal.name;
    cb.addEventListener("change", () => {
      if (cb.checked) {
        state.hidden.delete(cal.href);
        state.fc.addEventSource(sourceFor(cal));
      } else {
        state.hidden.add(cal.href);
        state.fc.getEventSourceById(cal.href)?.remove();
      }
      store("hidden", [...state.hidden]);
    });
    label.append(cb, sw, name);
    box.append(label);
  }
  if (!state.calendars.length) {
    const p = document.createElement("div");
    p.textContent = "No calendars yet.";
    box.append(p);
  }
}

function fmtWhen(ev) {
  if (ev.allDay) {
    return ev.start.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })
      + " (all day)";
  }
  const opts = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  const local = ev.start.toLocaleString(undefined, { ...opts, timeZoneName: "short" });
  const et = ev.start.toLocaleString(undefined, { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
  return `${local}  ·  ${et} ET`;
}

function openDetail(ev) {
  $("detail-title").textContent = ev.title;
  $("detail-meta").textContent = `${fmtWhen(ev)}  ·  ${ev.extendedProps.calendar}`;
  $("detail-notes").textContent = ev.extendedProps.notes || "";
  $("detail").hidden = false;
  $("detail-close").focus();
}
function closeDetail() { $("detail").hidden = true; }
$("detail-close").addEventListener("click", closeDetail);
$("detail").addEventListener("click", (e) => { if (e.target.id === "detail") closeDetail(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });

function showApp() {
  $("login").hidden = true;
  $("app").hidden = false;
  $("who").textContent = state.user;
  $("tz-note").textContent = "Times shown in " +
    Intl.DateTimeFormat().resolvedOptions().timeZone + ". Event details also show ET.";
  state.hidden = new Set(recall("hidden", []));
  renderCalendarList();

  const narrow = window.matchMedia("(max-width: 720px)").matches;
  state.fc = new FullCalendar.Calendar($("calendar"), {
    // List is the most readable view of dense market calendars, so it is the
    // default; the last view a user picked is remembered.
    initialView: recall("view", "listWeek"),
    headerToolbar: narrow
      ? { left: "prev,next today", center: "title", right: "listWeek,timeGridDay" }
      : { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,timeGridDay,listWeek" },
    buttonText: { today: "Today", month: "Month", week: "Week", day: "Day", list: "List" },
    height: "auto",
    nowIndicator: true,
    slotMinTime: "04:00:00",
    slotEventOverlap: false,
    dayMaxEvents: 4,
    eventTimeFormat: { hour: "numeric", minute: "2-digit", meridiem: "short" },
    // In the week/day grid a 15-minute block has room for a few words: spend
    // them on the title. Position shows the time; the detail shows it exactly.
    views: { timeGrid: { displayEventTime: false } },
    eventSources: state.calendars.filter((c) => !state.hidden.has(c.href)).map(sourceFor),
    loading: (busy) => { if (busy) setStatus("Loading…"); else if ($("status").textContent === "Loading…") setStatus(""); },
    datesSet: (info) => store("view", info.view.type),
    eventClick: (info) => { info.jsEvent.preventDefault(); openDetail(info.event); },
  });
  state.fc.render();
}
