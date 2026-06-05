"""
macro_calendar.py
-----------------
Fetches today's high-impact macroeconomic data releases relevant to energy
trading (oil, gas, UK, USA) and returns an HTML section for insertion into
the Morning Energy Report email.

Two-layer approach:
  1. Investing.com scrape  — live high-impact USD/GBP events for the target date
  2. Built-in schedule     — guaranteed weekly/monthly energy releases
     (EIA petroleum, EIA gas storage, Baker Hughes rig count)

Usage:
  Standalone:  python macro_calendar.py [--date YYYY-MM-DD]
  As a module: from macro_calendar import calendar_section_html
"""

import datetime as dt
import html
import re
import requests
from bs4 import BeautifulSoup

# Reuse colour constants from the main report if available
try:
    from morning_report import COL_ACCENT, COL_TITLE, log
except ImportError:
    COL_ACCENT = "#0B6B3A"
    COL_TITLE  = "#13386B"

    def log(msg: str) -> None:
        print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")


# ---------------------------------------------------------------------------
# 1. Investing.com scraper
# ---------------------------------------------------------------------------
IC_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
IC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.investing.com/economic-calendar/",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.investing.com",
}

# Investing.com country IDs: 5=USA, 4=UK
IC_COUNTRIES = ["5", "4"]

# Importance: 3=High, 2=Medium
IC_IMPORTANCE = ["3", "2"]

# Map star count in HTML to label
IC_IMPACT_MAP = {3: "High", 2: "Medium", 1: "Low"}

WANTED_CURRENCIES = {"USD", "GBP"}


def _scrape_investing(target_date: dt.date) -> list[dict]:
    """
    Returns calendar events from Investing.com for target_date.
    Each dict: {time, currency, impact, title, forecast, previous}
    Returns [] on any error.
    """
    date_str = target_date.strftime("%Y-%m-%d")
    payload = {
        "dateFrom": date_str,
        "dateTo":   date_str,
        "timeZone": "55",  # London (UTC+1 BST / UTC+0 GMT) — Investing.com zone ID
        "timeFilter": "timeRemain",
        "currentTab": "custom",
        "submitFilters": "1",
        "limit_from": "0",
    }
    for c in IC_COUNTRIES:
        payload[f"country[]"] = c  # requests handles repeated keys via list below

    # requests doesn't handle repeated keys from a plain dict; build as list of tuples
    data = [(f"country[]", c) for c in IC_COUNTRIES]
    data += [(f"importance[]", i) for i in IC_IMPORTANCE]
    data += [(k, v) for k, v in payload.items()]

    try:
        r = requests.post(IC_URL, headers=IC_HEADERS, data=data, timeout=20)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        log(f"  macro_calendar: Investing.com fetch failed ({e})")
        return []

    table_html = raw.get("data", "")
    if not table_html:
        log("  macro_calendar: Investing.com returned empty data")
        return []

    soup = BeautifulSoup(table_html, "html.parser")
    events = []
    current_time = ""

    for row in soup.find_all("tr"):
        classes = row.get("class", [])
        # Skip header/separator rows
        if "theDay" in classes or "spacer" in classes:
            continue

        # Time
        time_el = row.find("td", class_="time")
        if time_el:
            t = time_el.get_text(strip=True)
            if t:
                current_time = t

        # Currency
        currency_el = row.find("td", class_="flagCur")
        if not currency_el:
            continue
        currency = currency_el.get_text(strip=True).upper()
        if currency not in WANTED_CURRENCIES:
            continue

        # Impact (count filled stars)
        impact_el = row.find("td", class_="sentiment")
        impact = "Medium"
        if impact_el:
            filled = len(impact_el.find_all("i", class_=re.compile(r"grayFullBullishU|bullishU")))
            impact = IC_IMPACT_MAP.get(filled, "Medium")

        # Title
        title_el = row.find("a", class_="eventRowTitle") or row.find("td", class_="event")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Forecast / previous
        forecast, previous = "", ""
        cells = row.find_all("td")
        for cell in cells:
            c = " ".join(cell.get("class", []))
            if "fore" in c:
                forecast = cell.get_text(strip=True)
            elif "prev" in c:
                previous = cell.get_text(strip=True)

        events.append({
            "time":     current_time,
            "currency": currency,
            "impact":   impact,
            "title":    title,
            "forecast": forecast,
            "previous": previous,
            "source":   "Investing.com",
        })

    log(f"  macro_calendar: Investing.com returned {len(events)} USD/GBP events")
    return events


# ---------------------------------------------------------------------------
# 2. Built-in schedule for known weekly energy releases
# ---------------------------------------------------------------------------
# These are fixed by schedule (not easily scraped) so we always include them.
# day_of_week: 0=Mon … 6=Sun

WEEKLY_SCHEDULE = [
    # (day_of_week, approx_uk_time, title, note)
    (1, "21:30",  "API Crude Oil Stock Change",
     "Unofficial US inventory snapshot from American Petroleum Institute; "
     "sets direction ahead of Wednesday's EIA release."),
    (2, "15:30",  "EIA Weekly Petroleum Status Report",
     "Official US crude, gasoline & distillate inventory data (EIA). "
     "Biggest weekly scheduled oil-price mover."),
    (3, "15:30",  "EIA Natural Gas Storage Change",
     "Weekly US gas storage injection/withdrawal vs. consensus (EIA). "
     "Moves Henry Hub and influences TTF."),
    (4, "18:00",  "Baker Hughes US Oil Rig Count",
     "Active US oil drilling rigs — leading supply indicator watched by "
     "OPEC+ and shale analysts."),
]

# Monthly schedule (approximate; included only when date range matches)
# Format: (month_day_start, month_day_end, title, note)
MONTHLY_SCHEDULE = [
    (1,  7,   "US Non-Farm Payrolls",
     "First Friday of the month. Biggest monthly US jobs report — demand "
     "proxy; strong print supports oil, weak print pressures it."),
    (10, 16,  "OPEC Monthly Oil Market Report",
     "OPEC's own supply/demand balance, production figures, and price "
     "outlook for the month ahead."),
    (11, 17,  "IEA Oil Market Report",
     "Independent global oil supply/demand forecasts from the International "
     "Energy Agency."),
    (8,  16,  "US CPI (Consumer Price Index)",
     "Inflation data driving Fed rate expectations → USD strength → crude "
     "prices (inverse)."),
    (13, 21,  "UK CPI (Consumer Price Index)",
     "Drives BoE rate expectations, GBP, and UK energy-price outlook."),
    (25, 31,  "US GDP (advance/preliminary/final)",
     "Quarterly GDP estimate — broad demand picture for energy."),
]


def _builtin_events(today: dt.date) -> list[dict]:
    """Return built-in schedule events that apply to today."""
    out = []
    dow = today.weekday()
    dom = today.day

    for day, time_, title, note in WEEKLY_SCHEDULE:
        if dow == day:
            out.append({
                "time":     time_,
                "currency": "USD" if "EIA" in title or "Baker" in title or "API" in title else "USD",
                "impact":   "High",
                "title":    title,
                "forecast": "",
                "previous": "",
                "note":     note,
                "source":   "schedule",
            })

    for d_start, d_end, title, note in MONTHLY_SCHEDULE:
        if d_start <= dom <= d_end:
            out.append({
                "time":     "varies",
                "currency": "GBP" if "UK" in title else "USD",
                "impact":   "High",
                "title":    title,
                "forecast": "",
                "previous": "",
                "note":     note,
                "source":   "schedule",
            })

    return out


# ---------------------------------------------------------------------------
# 3. Merge and deduplicate
# ---------------------------------------------------------------------------
def _normalise(s: str) -> str:
    """Lower-case, strip punctuation for fuzzy dedup."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def get_todays_events(target_date: dt.date | None = None) -> list[dict]:
    """
    Returns merged list of relevant events for target_date (default: today).
    Investing.com live data takes priority; built-in schedule fills any gaps.
    """
    today = target_date or dt.date.today()
    ff_events = _scrape_investing(today)
    builtin = _builtin_events(today)

    # Dedup: skip built-in if a similar title already appears from FF
    ff_titles = {_normalise(e["title"]) for e in ff_events}
    merged = list(ff_events)
    for ev in builtin:
        norm = _normalise(ev["title"])
        # Accept if no token overlap with any FF title
        tokens = set(norm.split())
        if not any(len(tokens & set(t.split())) >= 2 for t in ff_titles):
            merged.append(ev)

    # Sort: put time-unknown entries last
    def sort_key(e):
        t = e.get("time", "")
        try:
            parsed = dt.datetime.strptime(t.lower().replace(" ", ""), "%I:%M%p")
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            return 9999

    merged.sort(key=sort_key)
    log(f"  macro_calendar: {len(merged)} total events for today")
    return merged


# ---------------------------------------------------------------------------
# 4. HTML rendering
# ---------------------------------------------------------------------------
IMPACT_COLOURS = {
    "High":   "#c0392b",
    "Medium": "#e67e22",
    "Low":    "#7f8c8d",
}

FLAG_MAP = {
    "USD": "🇺🇸",
    "GBP": "🇬🇧",
}

COL_CAL_BG  = "#f7f9fc"
COL_CAL_HDR = "#13386B"


def _row_html(ev: dict) -> str:
    time_s    = html.escape(ev.get("time", ""))
    currency  = html.escape(ev.get("currency", ""))
    flag      = FLAG_MAP.get(ev.get("currency", ""), "")
    impact    = ev.get("impact", "Medium")
    imp_col   = IMPACT_COLOURS.get(impact, "#888")
    title     = html.escape(ev.get("title", ""))
    note      = html.escape(ev.get("note", ""))
    forecast  = html.escape(ev.get("forecast", ""))
    previous  = html.escape(ev.get("previous", ""))

    # Build the detail line (forecast/previous or note)
    detail_parts = []
    if forecast:
        detail_parts.append(f"Forecast: <b>{forecast}</b>")
    if previous:
        detail_parts.append(f"Prev: {previous}")
    detail = " &nbsp;·&nbsp; ".join(detail_parts) if detail_parts else note

    return f"""
      <tr style="border-bottom:1px solid #eef;">
        <td style="padding:7px 8px;font-size:12px;color:#555;white-space:nowrap;width:60px;">{time_s}</td>
        <td style="padding:7px 6px;font-size:12px;text-align:center;width:36px;">{flag} {currency}</td>
        <td style="padding:7px 6px;width:10px;">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;
            background:{imp_col};"></span>
        </td>
        <td style="padding:7px 8px;">
          <div style="font-size:14px;font-weight:600;color:#222;">{title}</div>
          {'<div style="font-size:12px;color:#666;margin-top:2px;">' + detail + '</div>' if detail else ''}
        </td>
      </tr>"""


def calendar_section_html(events: list[dict] | None = None,
                          target_date: dt.date | None = None) -> str:
    """
    Returns a complete HTML block for 'Today's Macro Calendar'.
    Pass events=None to fetch automatically (uses target_date or today).
    Returns empty string if no events found.
    """
    if events is None:
        events = get_todays_events(target_date)
    if not events:
        return "<p style='font-family:Arial,sans-serif;color:#999;font-size:13px;'>No scheduled high-impact releases today.</p>"

    date_label = (target_date or dt.date.today()).strftime("%A %d %B %Y")
    rows = "".join(_row_html(e) for e in events)

    return f"""
    <h2 style="font-family:Arial,sans-serif;color:#fff;background:{COL_CAL_HDR};
        padding:8px 12px;font-size:16px;margin:24px 0 10px;border-radius:4px;">
      Today&#39;s Macro Calendar
    </h2>
    <div style="font-family:Arial,sans-serif;font-size:11px;color:#999;margin-bottom:8px;">
      Scheduled data releases for {html.escape(date_label)} · USD &amp; GBP high/medium impact
    </div>
    <table width="100%" cellspacing="0" cellpadding="0"
      style="border-collapse:collapse;background:{COL_CAL_BG};border-radius:4px;overflow:hidden;">
      <thead>
        <tr style="background:#eef2f7;">
          <th style="padding:6px 8px;font-size:11px;color:#666;text-align:left;font-weight:600;">Time (UK)</th>
          <th style="padding:6px 6px;font-size:11px;color:#666;text-align:center;font-weight:600;">CCY</th>
          <th style="padding:6px 6px;font-size:11px;color:#666;font-weight:600;"></th>
          <th style="padding:6px 8px;font-size:11px;color:#666;text-align:left;font-weight:600;">Release</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="font-family:Arial,sans-serif;font-size:10px;color:#bbb;margin:4px 0 0;">
      ● High impact &nbsp; ● Medium impact &nbsp;·&nbsp; Sources: Investing.com, EIA, Baker Hughes schedule
    </div>"""


# ---------------------------------------------------------------------------
# 5. Standalone run — prints events and saves a preview HTML
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Optional: --date YYYY-MM-DD
    target = dt.date.today()
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            target = dt.date.fromisoformat(sys.argv[idx + 1])

    print(f"\nFetching calendar for {target.strftime('%A %d %B %Y')} ...")
    events = get_todays_events(target)

    if not events:
        print("No high-impact events found for that date.")
    else:
        print(f"\n{'Time':<10} {'CCY':<5} {'Impact':<8} Title")
        print("-" * 70)
        for e in events:
            print(f"{e['time']:<10} {e['currency']:<5} {e['impact']:<8} {e['title']}")

    cal_html = calendar_section_html(events, target_date=target)
    out = Path(__file__).parent / "calendar_preview.html"
    out.write_text(
        f"<!DOCTYPE html><html><body style='max-width:640px;margin:auto;padding:20px;font-family:Arial,sans-serif;'>"
        f"{cal_html}</body></html>",
        encoding="utf-8",
    )
    print(f"\nPreview saved to: {out}")
