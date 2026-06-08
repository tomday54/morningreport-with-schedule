"""
Morning Energy Report - Agent edition
--------------------------------------
A tool-using Claude agent assembles a daily energy briefing. The agent can:
  - web_search  (Anthropic server-side tool) to find the day's news
  - fetch_url   (client-side tool) to pull the text of a specific article
  - emit_report (client-side tool) to return the final structured briefing
It loops, calling tools as needed, until it emits the report. We then render an
HTML email and send it through the locally signed-in Outlook (COM).

Sections:
  OIL          -> 3 headlines + 1 physical-fundamentals item + 1 carbon-credits item
  POWER & GAS  -> 3 headlines + 1 physical-fundamentals item

Requires env var: ANTHROPIC_API_KEY

NOTE: The agent searches and reads PUBLIC web pages only. No local or internal
      files are ever sent to the API or the internet.
"""

import os
import sys
import re
import json
import html
import datetime as dt
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import anthropic
from macro_calendar import get_todays_events, calendar_section_html

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
LOG = HERE / "run.log"


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ----------------------------------------------------------------------------
# Client-side tool: fetch a public article and return cleaned text
# ----------------------------------------------------------------------------
def fetch_url(url: str, max_chars: int = 6000) -> str:
    try:
        r = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (MorningReport/1.0)"},
        )
        r.raise_for_status()
    except Exception as e:
        return f"[fetch error: {e}]"
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()
    return text[:max_chars]


# ----------------------------------------------------------------------------
# The agent loop
# ----------------------------------------------------------------------------
# A list of 3 headline items, each with its own short paragraph.
HEADLINES_OBJ = {
    "type": "array",
    "description": "The 3 most important headlines, each as its own item.",
    "items": {
        "type": "object",
        "properties": {
            "title":   {"type": "string", "description": "The headline."},
            "summary": {"type": "string",
                        "description": "A paragraph of 50-60 words explaining this "
                                       "story: what happened, the concrete data "
                                       "(price levels, % moves, volumes, spreads), "
                                       "WHY it moved, and what to watch next. Stay "
                                       "within 50-60 words."},
            "source":  {"type": "string"},
            "url":     {"type": "string"},
        },
        "required": ["title", "summary", "source", "url"],
    },
    "minItems": 1, "maxItems": 3,
}

# A single short-paragraph section (fundamentals / carbon).
PARA_OBJ = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A paragraph of 50-60 words citing concrete data "
                           "(inventories, storage levels, production, flows, % "
                           "moves), the drivers, and what to watch next. Stay "
                           "within 50-60 words.",
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":  {"type": "string"},
                    "source": {"type": "string"},
                    "url":    {"type": "string"},
                },
                "required": ["title", "source", "url"],
            },
        },
    },
    "required": ["summary", "sources"],
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "oil_headlines":          HEADLINES_OBJ,
        "oil_fundamentals":       PARA_OBJ,
        "carbon":                 PARA_OBJ,
        "power_gas_headlines":    HEADLINES_OBJ,
        "power_gas_fundamentals": PARA_OBJ,
    },
    "required": ["oil_headlines", "oil_fundamentals", "carbon",
                 "power_gas_headlines", "power_gas_fundamentals"],
}

SYSTEM_PROMPT = """You are a senior energy-markets analyst preparing a morning \
briefing for a commodities trading desk. Work as an agent: use web_search to \
find today's most market-moving developments, then fetch_url to read the actual \
source pages and extract concrete figures. Do at least 4-5 searches across \
different topics before deciding. Call emit_report exactly once when confident.

SOURCE HIERARCHY — always prefer in this order:
  1. Primary data: EIA.gov, ICE, CME, OPEC.org, IEA, Bank of England, ONS
  2. Wire services: Reuters, Bloomberg, S&P Global Platts, Argus Media
  3. Quality press: Financial Times, Wall Street Journal
  4. Avoid: opinion blogs, social media, press releases without hard data

CROSS-CHECK NUMBERS: If two sources give different prices for the same instrument \
fetch_url both and use the one from the higher-ranked source above. Never average \
them. If you cannot verify a number from a fetched page, do not include it — \
write around it instead.

PAYWALLS: If fetch_url returns less than 200 words or a login page, discard that \
URL and web_search for an alternative source covering the same story.

ACCURACY & TIMING — CRITICAL:
  - NEVER use memory or prior knowledge for any price, inventory or storage figure.
  - Always fetch_url the source page and read the number directly off it.
  - Report the PRIOR TRADING DAY'S CLOSE/SETTLEMENT only — never intraday.
  - The large number at the top of price pages is the LIVE quote — ignore it.
  - Use "Previous close", "Settlement" or "Prior session" fields instead.
  - If only a live price is shown: derive the prior close from the stated day's \
change and flag it clearly, e.g. "Brent settled ~$93.80 (-1.2%) on Tuesday \
based on reported daily change".
  - Always state the DAY of the session: "Brent settled -1.2% at $93.80 on Tuesday".
  - On Monday mornings always use Friday's settlement and say so explicitly.

MACRO CALENDAR: You will be given today's scheduled data releases. If a major \
release has already occurred (NFP, EIA inventories, CPI, BoE rate decision etc.) \
it MUST feature prominently — search for the actual print, compare it to \
consensus, and explain the market reaction. Do not just note it was scheduled; \
report what the number was and what it did to prices.

WRITING STYLE:
  - Analyst prose, not headlines. Each summary must be a tight PARAGRAPH of \
50-60 WORDS covering: what happened, the exact figures, why it moved, and what \
to watch next — never a single line, never just a headline. Keep strictly to 50-60 words.
  - Lead every summary with the key data point: "Brent settled -2.8% at $95.03 as..."
  - Never invent URLs, figures or quotes. If you did not find it, do not write it.

Sections:
  - oil_headlines: 3 most market-moving crude/products stories, each its own paragraph.
  - oil_fundamentals: physical supply/demand — inventories, OPEC+ output, refinery \
runs, flows, cargoes. If EIA published today cite the exact inventory build/draw figure.
  - carbon: EU ETS / UKA / voluntary market — price level, direction and key driver.
  - power_gas_headlines: 3 most important power & gas stories, each its own paragraph.
  - power_gas_fundamentals: gas storage as % of seasonal norm, LNG sendout, \
generation mix, outages, weather-driven demand.

Prefer developments from the last 24 hours. Never cite sources older than 36 hours."""


def build_tools():
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": CONFIG.get("max_web_searches", 10),
        },
        {
            "name": "fetch_url",
            "description": "Fetch and return the cleaned text of a public web "
                           "article by URL. Use to verify or better summarise a "
                           "specific story.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {
            "name": "emit_report",
            "description": "Submit the final structured morning briefing. Call "
                           "exactly once when finished.",
            "input_schema": REPORT_SCHEMA,
        },
    ]


def run_agent(prices=None, cal_events=None):
    client = anthropic.Anthropic()
    tools = build_tools()
    today = dt.datetime.now().strftime("%A, %d %B %Y")
    vpages = CONFIG.get("verify_pages", {})
    vlist = "\n".join(f"  - {k}: {u}" for k, u in vpages.items())

    # Build calendar context string for the prompt
    cal_context = ""
    if cal_events:
        lines = "\n".join(
            f"  {e.get('time','?'):>6}  {e['currency']}  {e['impact']:<6}  {e['title']}"
            for e in cal_events
        )
        cal_context = (
            f"\nTODAY'S MACRO CALENDAR (use these to frame market context — "
            f"do NOT rely on memory or any source published more than 1 day ago):\n{lines}\n"
        )

    # Anchor the whole briefing to the same session the price strip reports.
    anchor = ""
    if prices:
        adate = prices[0].get("date_full", prices[0].get("date", ""))
        plist = "; ".join(
            f'{p["name"]} {p["cur"]}{p["close"]:.2f} ({p["pct"]:+.2f}%)' for p in prices)
        anchor = (
            f"PRICE ANCHOR — the report's price strip shows the CLOSE for {adate}. "
            f"Anchor the ENTIRE briefing to THAT trading session. Strip closes already "
            f"computed: {plist}. Report how each market FINISHED on {adate} (close vs the "
            f"prior close); do NOT describe a later or intraday move, and keep any prices "
            f"you cite consistent with these strip values.\n\n")

    user_task = (
        f"Today is {today}. Build the morning energy briefing.\n\n"
        f"{anchor}"
        f"{cal_context}"
        f"Focus: {CONFIG.get('focus_notes', '')}\n\n"
        f"Before writing, fetch_url these reference pages and read each market's "
        f"PRIOR TRADING DAY end-of-day close/settlement, that session's % change and "
        f"direction, and the latest news off each one — do not rely on memory:\n{vlist}\n\n"
        f"Then web_search for any additional market-moving stories, read key "
        f"articles, and call emit_report with the final selection. Every number you "
        f"cite must come from a page you fetched in this run. "
        f"Do NOT use any data source published more than 1 day ago."
    )
    messages = [{"role": "user", "content": user_task}]

    for turn in range(CONFIG.get("max_agent_turns", 12)):
        resp = client.messages.create(
            model=CONFIG["anthropic_model"],
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        # surface what the agent did this turn
        searches = sum(1 for b in resp.content if getattr(b, "type", "") == "server_tool_use")
        if searches:
            log(f"  turn {turn+1}: agent ran web_search")

        if resp.stop_reason != "tool_use":
            # No client tool requested. Nudge once toward emit_report.
            log(f"  turn {turn+1}: stop_reason={resp.stop_reason}; nudging for emit_report")
            messages.append({"role": "user", "content":
                "Please finalise now by calling emit_report with your selection."})
            continue

        tool_results = []
        final = None
        for block in resp.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            if block.name == "fetch_url":
                url = block.input.get("url", "")
                log(f"  turn {turn+1}: fetch_url {url[:70]}")
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": fetch_url(url),
                })
            elif block.name == "emit_report":
                final = block.input
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": "Report received.",
                })
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        if final is not None:
            log("  agent emitted report.")
            return final

    raise RuntimeError("Agent did not emit a report within the turn budget.")


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------
# Colour scheme
COL_OIL = "#E8730C"      # orange  — OIL section heading
COL_PG = "#13386B"       # dark blue — POWER & GAS section heading
COL_ACCENT = "#0B6B3A"   # dark green — links, source links, sub-labels
COL_TITLE = "#13386B"    # dark blue — main report title

# Instruments shown in the at-a-glance price strip (exact prior-day closes via yfinance)
PRICE_TICKERS = [
    ("Brent", "BZ=F", "$"),
    ("WTI", "CL=F", "$"),
    ("TTF Gas", "TTF=F", "€"),
    ("US NatGas", "NG=F", "$"),
]


def fetch_prices():
    """Exact prior trading-day closes via yfinance. Returns [] if unavailable."""
    try:
        import yfinance as yf
    except Exception as e:
        log(f"  yfinance unavailable ({e}); skipping price strip")
        return []
    today = dt.date.today()
    out = []
    for name, tk, cur in PRICE_TICKERS:
        try:
            h = yf.Ticker(tk).history(period="10d")
            closes = [(idx.date(), float(row["Close"])) for idx, row in h.iterrows()]
            prior = [c for c in closes if c[0] < today]   # completed sessions only
            series = prior if len(prior) >= 2 else closes
            if len(series) < 2:
                continue
            (_, c_prev), (d_last, c_last) = series[-2], series[-1]
            pct = (c_last - c_prev) / c_prev * 100 if c_prev else 0.0
            out.append({"name": name, "cur": cur, "close": c_last,
                        "pct": pct, "date": d_last.strftime("%a %d %b"),
                        "date_full": d_last.strftime("%A %d %B %Y")})
            log(f"  price {name}: {cur}{c_last:.2f} ({pct:+.2f}%) close {d_last}")
        except Exception as e:
            log(f"  price {name} error: {e}")
    return out


def price_strip_html(prices):
    if not prices:
        return ""
    cells = []
    for p in prices:
        up = p["pct"] >= 0
        col = "#1e8449" if up else "#c0392b"
        arrow = "&#9650;" if up else "&#9660;"
        cells.append(
            '<td style="padding:10px 6px;background:#f7f9fc;border:1px solid #eef;text-align:center;">'
            f'<div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;">{html.escape(p["name"])}</div>'
            f'<div style="font-size:17px;font-weight:700;color:#111;">{p["cur"]}{p["close"]:,.2f}</div>'
            f'<div style="font-size:12px;font-weight:700;color:{col};">{arrow} {abs(p["pct"]):.2f}%</div>'
            '</td>')
    asof = html.escape(prices[0]["date"])
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;margin-bottom:6px;"><tr>'
        + "".join(cells) +
        '</tr></table>'
        f'<div style="font-size:11px;color:#aaa;margin:0 0 18px;">Prior-session close '
        f'({asof}) · source: Yahoo Finance / exchange close. Carbon &amp; power '
        f'covered in the sections below.</div>'
    )


def build_subject(prices):
    base = CONFIG.get("subject_prefix", "Morning Energy Report")
    if not prices:
        return f"{base} — {dt.datetime.now():%d %b %Y}"
    bits = []
    for p in prices[:4]:
        arrow = "▲" if p["pct"] >= 0 else "▼"
        bits.append(f'{p["name"]} {arrow}{p["cur"]}{p["close"]:.2f}')
    return f"{base} — " + " · ".join(bits)


def _src_links(srcs):
    if not srcs:
        return ""
    links = " &nbsp;·&nbsp; ".join(
        f'<a href="{html.escape(s.get("url",""))}" '
        f'style="color:{COL_ACCENT};text-decoration:none;">'
        f'{html.escape(s.get("source") or s.get("title") or "source")}</a>'
        for s in srcs
    )
    return (f'<div style="color:#999;font-size:11px;margin-top:5px;">'
            f'Sources: {links}</div>')


def _as_item_list(v):
    """Normalise an agent section into a list of dict items (defensive)."""
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []


def _as_single(v):
    """Normalise an agent section into a single dict (defensive)."""
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                return x
    return {}


def headlines_html(label, items):
    """Render a labelled group of headline items, each its own paragraph."""
    items = _as_item_list(items)
    parts = []
    for it in items:
        title = html.escape(it.get("title", ""))
        url = html.escape(it.get("url", ""))
        summary = html.escape(it.get("summary", "")).replace("\n", "<br>")
        src = _src_links([it]) if it.get("url") else ""
        parts.append(f"""
          <div style="margin:0 0 18px;">
            <a href="{url}" style="color:{COL_ACCENT};text-decoration:none;
               font-weight:700;font-size:16px;">{title}</a>
            <div style="color:#333;font-size:15px;line-height:1.65;margin-top:5px;">{summary}</div>
            {src}
          </div>""")
    return f"""
      <div style="margin:4px 0 14px;">
        <div style="color:{COL_ACCENT};font-size:11px;text-transform:uppercase;
            letter-spacing:.5px;font-weight:700;margin-bottom:6px;">{label}</div>
        {''.join(parts)}
      </div>"""


def para_html(label, sec):
    """Render a single-paragraph sub-section (fundamentals / carbon)."""
    sec = _as_single(sec)
    summary = html.escape(sec.get("summary", "")).replace("\n", "<br>")
    return f"""
      <div style="margin:4px 0 14px;">
        <div style="color:{COL_ACCENT};font-size:11px;text-transform:uppercase;
            letter-spacing:.5px;font-weight:700;margin-bottom:4px;">{label}</div>
        <div style="color:#333;font-size:15px;line-height:1.65;">{summary}</div>
        {_src_links(sec.get("sources", []))}
      </div>"""


def section(title, accent, inner):
    return f"""
    <h2 style="font-family:Arial,sans-serif;color:#fff;background:{accent};
        padding:8px 12px;font-size:16px;margin:24px 0 10px;border-radius:4px;">{title}</h2>
    <div style="font-family:Arial,sans-serif;">{inner}</div>"""


def build_html(data, prices=None, cal_events=None):
    today = dt.datetime.now().strftime("%A, %d %B %Y")
    oil = (headlines_html("Top 3 Headlines", data.get("oil_headlines"))
           + para_html("Physical Fundamentals", data.get("oil_fundamentals"))
           + para_html("Carbon Credits", data.get("carbon")))
    pg = (headlines_html("Top 3 Headlines", data.get("power_gas_headlines"))
          + para_html("Physical Fundamentals", data.get("power_gas_fundamentals")))
    cal = calendar_section_html(cal_events)
    return f"""<!DOCTYPE html><html><body style="background:#f4f4f4;margin:0;padding:20px;">
    <div style="max-width:640px;margin:auto;background:#fff;padding:24px;border-radius:6px;">
      <h1 style="font-family:Arial,sans-serif;color:{COL_TITLE};font-size:22px;margin:0;border-bottom:3px solid {COL_ACCENT};padding-bottom:6px;">Morning Energy Report</h1>
      <div style="font-family:Arial,sans-serif;color:#888;font-size:13px;margin:4px 0 14px;">{today}</div>
      {price_strip_html(prices) if prices else ""}
      {cal}
      {section("OIL", COL_OIL, oil)}
      {section("POWER &amp; GAS", COL_PG, pg)}
      <p style="font-family:Arial,sans-serif;color:#aaa;font-size:11px;margin-top:24px;border-top:1px solid #eee;padding-top:10px;">
        Compiled by an automated research agent (Claude + web search). Verify before trading.</p>
    </div></body></html>"""


# ----------------------------------------------------------------------------
# Outlook send
# ----------------------------------------------------------------------------
OUTLOOK_PATHS = [
    r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
    r"C:\Program Files (x86)\Microsoft Office\root\Office16\OUTLOOK.EXE",
    r"C:\Program Files\Microsoft Office\Office16\OUTLOOK.EXE",
]


def _ensure_outlook_running():
    """Classic Outlook must be running for COM automation to work."""
    import subprocess, time
    import win32com.client
    try:
        win32com.client.GetActiveObject("Outlook.Application")
        return  # already running
    except Exception:
        pass
    for path in OUTLOOK_PATHS:
        if os.path.exists(path):
            subprocess.Popen([path])
            break
    # wait for Outlook to initialise
    for _ in range(20):
        time.sleep(2)
        try:
            win32com.client.GetActiveObject("Outlook.Application")
            return
        except Exception:
            continue


def send_smtp(html_body, subject):
    """Cloud send path: SMTP (e.g. Gmail app password). Credentials from env."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    sender = os.environ["GMAIL_ADDRESS"].strip()
    # App passwords are shown with spaces; strip all whitespace incl. non-breaking.
    password = "".join(os.environ["GMAIL_APP_PASSWORD"].split()).replace("\xa0", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(CONFIG["recipients"])
    if CONFIG.get("cc"):
        msg["Cc"] = ", ".join(CONFIG["cc"])
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    recipients = CONFIG["recipients"] + CONFIG.get("cc", [])
    with smtplib.SMTP(host, port) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def send_email(html_body, subject):
    # Cloud / headless path: if an SMTP app password is present, use SMTP.
    if os.environ.get("GMAIL_APP_PASSWORD"):
        send_smtp(html_body, subject)
        return
    import win32com.client
    _ensure_outlook_running()
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # olMailItem
    mail.To = "; ".join(CONFIG["recipients"])
    if CONFIG.get("cc"):
        mail.CC = "; ".join(CONFIG["cc"])
    mail.Subject = subject
    mail.HTMLBody = html_body
    mail.Send()


# ----------------------------------------------------------------------------
def main():
    log("=== Morning report (agent) run start ===")
    override = "--to" in sys.argv
    # Force a send (ignore the once-per-day guard) when --force is passed OR when a
    # human clicks "Run workflow" on GitHub (event = workflow_dispatch).
    force = "--force" in sys.argv or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    dry = "--dry-run" in sys.argv
    marker = HERE / "last_sent.txt"
    today_str = dt.date.today().isoformat()
    already = marker.exists() and marker.read_text(encoding="utf-8").strip() == today_str

    # Once-per-day guard: skip the whole run if we've already sent today.
    # (Lets the 06:00 trigger, the at-logon catch-up, and retries coexist safely.)
    if already and not override and not force and not dry:
        log(f"  already sent today ({today_str}); nothing to do.")
        log("=== run complete ===")
        return

    # Strip any stray whitespace/newline a pasted secret may carry.
    if os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"].strip()

    # Cloud: recipients can come from the RECIPIENTS env var (GitHub secret).
    # Split on commas, semicolons, OR any whitespace/newlines (robust to paste format).
    if os.environ.get("RECIPIENTS"):
        CONFIG["recipients"] = [e for e in re.split(r"[,;\s]+", os.environ["RECIPIENTS"]) if e]

    # Optional one-off recipient override:  --to a@x.com,b@y.com
    if override:
        idx = sys.argv.index("--to")
        if idx + 1 < len(sys.argv):
            CONFIG["recipients"] = [e.strip() for e in sys.argv[idx + 1].split(",") if e.strip()]
            log(f"  recipient override: {CONFIG['recipients']}")
    prices = fetch_prices()
    cal_events = get_todays_events()
    log(f"  calendar: {len(cal_events)} events for today")
    data = run_agent(prices, cal_events=cal_events)
    log("  sections: " + ", ".join(
        f"{k}({len(v) if isinstance(v, list) else 1})" for k, v in data.items()))

    html_body = build_html(data, prices, cal_events=cal_events)
    subject = build_subject(prices)
    (HERE / "last_report.html").write_text(html_body, encoding="utf-8")
    (HERE / "last_report.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    if dry:
        log("  dry run: wrote last_report.html / .json, skipping send.")
    else:
        send_email(html_body, subject)
        log("  email sent.")
        if not override:  # only the standard daily send updates the once-per-day marker
            marker.write_text(today_str, encoding="utf-8")
    log("=== run complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"!! FATAL: {e!r}")
        raise
