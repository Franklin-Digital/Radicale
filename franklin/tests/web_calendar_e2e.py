"""End-to-end browser test of /.web/calendar/ against a STAGING Radicale.

Usage: python3.10 e2e.py <base_url> <screenshot_dir>
Test users are staging-only htpasswd accounts; nothing touches production auth.
"""
import sys
from playwright.sync_api import sync_playwright, expect

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(timezone_id="America/Los_Angeles", locale="en-US",
                              viewport={"width": 1400, "height": 1000},
                              color_scheme="dark")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(f"{BASE}/.web/calendar/")
    check("sign-in form renders", page.locator("#login-form").is_visible())

    # wrong password
    page.fill("#user", "alice")
    page.fill("#password", "WRONG")
    page.click("#login-form button")
    expect(page.locator("#login-error")).to_contain_text("incorrect", timeout=15000)
    check("wrong password is rejected", page.locator("#app").is_hidden(),
          page.locator("#login-error").inner_text())

    # The rejected attempt above logs one expected 401 in the console.
    errors.clear()

    # right password
    page.fill("#password", "alicepw")
    page.click("#login-form button")
    page.wait_for_selector("#app:not([hidden])", timeout=15000)
    names = page.locator(".cal-toggle span:not(.swatch)").all_inner_texts()
    check("both shared calendars listed", "Earnings Calendar" in names
          and "Economic Indicator Calendar" in names, str(names))
    check("password field cleared after sign-in", page.input_value("#password") == "")

    # default view is List
    page.wait_for_selector(".fc-list-event", timeout=20000)
    check("default view is List", page.locator(".fc-listWeek-button.fc-button-active").count() == 1)

    # week view, today = 2026-09-16 on this host
    page.click(".fc-timeGridWeek-button")
    page.wait_for_selector(".fc-timegrid-event", timeout=20000)
    fed = page.locator(".fc-timegrid-event", has_text="Fed Interest Rate Decision")
    check("FOMC event present in week view", fed.count() == 1, f"count={fed.count()}")
    check("grid blocks spend their space on the title, not the time",
          fed.first.locator(".fc-event-time").count() == 0 and "Fed Interest Rate Decision" in fed.first.inner_text())
    page.screenshot(path=f"{OUT}/web-calendar-week.png", full_page=False)

    # click -> detail
    fed.first.click()
    page.wait_for_selector("#detail:not([hidden])")
    notes = page.inner_text("#detail-notes")
    meta = page.inner_text("#detail-meta")
    check("detail lists all releases", notes.startswith("7 releases at 2:00 PM ET"), notes[:60])
    check("detail shows 11:00 AM Pacific = 2:00 PM ET", "11:00 AM PDT" in meta and "2:00 PM ET" in meta, meta)
    page.screenshot(path=f"{OUT}/web-calendar-detail.png")
    page.keyboard.press("Escape")
    check("Escape closes detail", page.locator("#detail").is_hidden())

    # list view
    page.click(".fc-listWeek-button")
    page.wait_for_selector(".fc-list-event", timeout=20000)
    check("list view shows events", page.locator(".fc-list-event").count() > 10,
          f"rows={page.locator('.fc-list-event').count()}")
    page.screenshot(path=f"{OUT}/web-calendar-list.png", full_page=False)

    # toggle off economic calendar
    before = page.locator(".fc-list-event").count()
    page.locator(".cal-toggle", has_text="Economic Indicator Calendar").locator("input").uncheck()
    page.wait_for_timeout(1500)
    after = page.locator(".fc-list-event").count()
    check("hiding a calendar removes its events", after < before, f"{before} -> {after}")
    page.locator(".cal-toggle", has_text="Economic Indicator Calendar").locator("input").check()

    # credentials never persisted
    stored = page.evaluate("() => JSON.stringify({l: {...localStorage}, s: {...sessionStorage}})")
    check("password never written to browser storage", "alicepw" not in stored and "Basic" not in stored, stored[:120])

    # sign out
    page.click("#logout")
    check("sign out returns to form", page.locator("#login-form").is_visible())

    check("no JavaScript errors", not errors, "; ".join(errors)[:200])
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
