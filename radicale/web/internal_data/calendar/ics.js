// Minimal iCalendar VEVENT parser for the Franklin web calendar view.
//
// Scope is deliberately small because the server does the hard part: every
// REPORT asks Radicale to <C:expand> the range, so recurrences arrive as
// concrete instances with UTC times. What remains is RFC 5545 line unfolding,
// text unescaping, and three DTSTART/DTEND shapes:
//   20260916T180000Z         UTC               -> exact instant
//   20260916                 VALUE=DATE        -> all-day, local midnight
//   20260916T080000 (+TZID)  floating/zoned    -> read as the browser's local
//                                                 time (a known approximation;
//                                                 expanded events are UTC)
// Kept dependency-free so it can be unit-tested with plain Node.

export function unfold(text) {
  return text.replace(/\r\n/g, "\n").replace(/\n[ \t]/g, "");
}

export function unescapeText(value) {
  return value.replace(/\\(\\|;|,|n|N)/g, (_, c) => (c === "n" || c === "N" ? "\n" : c));
}

function parseLine(line) {
  // NAME;PARAM=V;PARAM="V:x":value  -- the value starts at the first colon
  // that is not inside a quoted parameter value.
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') inQuote = !inQuote;
    else if (ch === ":" && !inQuote) {
      const head = line.slice(0, i).split(";");
      const params = {};
      for (const p of head.slice(1)) {
        const eq = p.indexOf("=");
        if (eq > 0) params[p.slice(0, eq).toUpperCase()] = p.slice(eq + 1).replace(/^"|"$/g, "");
      }
      return { name: head[0].toUpperCase(), params, value: line.slice(i + 1) };
    }
  }
  return null;
}

export function parseDate(value, params = {}) {
  const v = value.trim();
  let m = /^(\d{4})(\d{2})(\d{2})$/.exec(v);
  if (m || params.VALUE === "DATE") {
    m = m || /^(\d{4})(\d{2})(\d{2})/.exec(v);
    if (!m) return null;
    return { date: new Date(+m[1], +m[2] - 1, +m[3]), allDay: true };
  }
  m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/.exec(v);
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const date = m[7] ? new Date(Date.UTC(y, mo - 1, d, h, mi, s)) : new Date(y, mo - 1, d, h, mi, s);
  return { date, allDay: false };
}

export function parseEvents(icsText) {
  const events = [];
  let cur = null;
  let depth = 0;   // nesting inside a VEVENT (VALARM etc. must not leak props)
  for (const raw of unfold(icsText).split("\n")) {
    const line = raw.trimEnd();
    if (!line) continue;
    const upper = line.toUpperCase();
    if (upper === "BEGIN:VEVENT") { cur = {}; depth = 0; continue; }
    if (!cur) continue;
    if (upper.startsWith("BEGIN:")) { depth++; continue; }
    if (upper === "END:VEVENT") {
      if (cur.start) {
        events.push({
          uid: cur.uid || "",
          summary: cur.summary || "",
          description: cur.description || "",
          start: cur.start.date,
          end: cur.end ? cur.end.date : null,
          allDay: cur.start.allDay,
        });
      }
      cur = null;
      continue;
    }
    if (upper.startsWith("END:")) { depth = Math.max(0, depth - 1); continue; }
    if (depth > 0) continue;
    const p = parseLine(line);
    if (!p) continue;
    switch (p.name) {
      case "UID": cur.uid = p.value; break;
      case "SUMMARY": cur.summary = unescapeText(p.value); break;
      case "DESCRIPTION": cur.description = unescapeText(p.value); break;
      case "DTSTART": cur.start = parseDate(p.value, p.params); break;
      case "DTEND": cur.end = parseDate(p.value, p.params); break;
      default: break;
    }
  }
  return events;
}
