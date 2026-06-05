# Morning Energy Report (Agent edition)

Automated daily email: a tool-using Claude agent researches the web, reads key
articles, and compiles curated oil, carbon, and power & gas headlines, sent
through your signed-in Outlook every morning.

## What it does
A Claude agent loops over three tools until it has the briefing:
1. **web_search** (Anthropic server-side) — finds the day's developments.
2. **fetch_url** (local) — pulls full article text to verify/summarise.
3. **emit_report** — returns a structured briefing where each section is a
   SHORT WRITTEN SUMMARY citing key data and trends (not just a link list),
   with source articles attached:
   - **OIL** — market summary + physical fundamentals + carbon credits
   - **POWER & GAS** — market summary + physical fundamentals

Then it renders an HTML email and sends it via Outlook.

> The agent reads only PUBLIC web pages. No local/internal files are ever sent
> to the API or the internet.

## Files
| File | Purpose |
|------|---------|
| `morning_report.py` | The agent + email pipeline |
| `config.json` | Recipients, model, agent/search limits, focus notes |
| `run_report.bat` | Launcher used by Task Scheduler |
| `run.log` | Append-only run log |
| `last_report.html` | Last generated email (handy for `--dry-run`) |
| `last_report.json` | Last agent selection (structured) |

## One-time setup
1. **API key** (user env var):
   ```powershell
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
   Open a *new* terminal afterwards so it takes effect.
2. **Recipients**: edit `config.json` -> `recipients`.

## Manual runs
```powershell
# Build + preview only (writes last_report.html, no email):
%LOCALAPPDATA%\Programs\Python\Python312\python.exe morning_report.py --dry-run

# Full run (sends the email):
run_report.bat
```

## Schedule (06:00 daily)
Registered as Windows Task Scheduler job "MorningEnergyReport".
To change the time:
```powershell
schtasks /Change /TN "MorningEnergyReport" /ST 06:00
```
The machine must be on (not necessarily logged in if "run whether user is
logged on or not" is set, but Outlook COM needs an interactive session, so an
unlocked/logged-in session at send time is most reliable).

## Tuning
- `focus_notes` in `config.json` steers what the agent looks for.
- `max_web_searches` / `max_agent_turns` cap cost and loop length.
- Change the curation rules in `SYSTEM_PROMPT` inside `morning_report.py`.
