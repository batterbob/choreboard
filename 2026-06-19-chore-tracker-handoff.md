# Chore Tracker — Claude Code Handoff

## What You're Building

A self-hosted family web app called **Chore Tracker**. Two kids (Andrew, 11, and Daniel, 12) use it on iPads to check off daily chores and log reading and outdoor time. Parents (Allen and Diane) use a status page and admin dashboard to track progress and manage chores. Notifications go via Pushover.

Full spec: `2026-06-19-chore-tracker-spec.md` (in the same directory as this file). Read it before starting. Everything below is context to help you make good decisions — the spec is the source of truth.

---

## Environment

- **Host:** TrueNAS SCALE with Docker already set up
- **Deployment:** Single Docker container via `docker-compose.yml`
- **Port:** 7823 (non-standard by design — do not use 80, 443, 8080)
- **Database:** SQLite, stored on a mounted TrueNAS volume (`./data/`)
- **Timezone:** `America/New_York` via `TZ` env var — all date logic must use local time, not UTC
- **DNS:** Firewalla will create DNS entries pointing to the container (e.g., `andrew.camp` → server IP:7823/andrew). The app doesn't handle DNS — just respond correctly at the right routes.

---

## Pages to Build (in priority order)

Build and test each before moving to the next.

1. **`/andrew` and `/daniel` — Kid pages** (highest priority; this is what the kids use every day)
2. **`/status` — Read-only parent status page** (no password; Allen and Diane bookmark this on their phones)
3. **`/admin` — Admin dashboard** (password-protected; chore management, assignment, settings)
4. **`/admin/history` — History page** (lowest priority; can be a stub in v1 if needed)

---

## Key Decisions Already Made

Do not relitigate these:

- **URL = identity.** No login for kids. `/andrew` is Andrew's page, `/daniel` is Daniel's. This is intentional.
- **SQLite only.** No Postgres, no Redis. File-based is fine for a household of 4.
- **No HTTPS.** Local network only. Don't add SSL complexity.
- **Plain HTML/CSS/vanilla JS.** No React, no Vue, no build step. The app must work on Safari iOS 15 on an old iPad. No ES2022+, no CSS features unavailable before iOS 16.
- **APScheduler for background tasks.** Used for mid-morning reminders and Sunday evening summary notifications.
- **Port 7823.** Not configurable to 80 or 8080.
- **Weekly reset on Monday.** Not rolling 7 days.
- **Pushover only.** No email, no SMS.

---

## Critical Implementation Notes

**Timezone / date bugs are the most likely source of problems.** Every `log_date` must be stored as the local date in the configured timezone — not UTC. A reading entry logged at 11pm Eastern must record as that day's date, not the next day in UTC. Use `datetime.now(ZoneInfo(os.environ['TZ']))` consistently. Never call `datetime.utcnow()`.

**Undo toast on checkboxes.** When a kid taps a chore checkbox, show a 3-second undo toast. Do NOT send the completion to the API immediately — wait until the 3-second window closes, then POST. If the kid taps Undo, cancel the timeout and do not POST. This prevents accidental taps on old iPads from locking in a wrong completion.

**Quick-log buttons must be touch-friendly.** The +15/+25/+30/+60 min buttons on the kid page are the most-used UI element. Make them large (min 64px tall), well-spaced, and test that they don't accidentally trigger two taps on a slow iPad.

**Weekly results must be computed on any page load**, not just the admin page. If a prior week is unfinalized (Sunday has passed but `weekly_results` has no row for that week), compute and store it immediately on any request. Don't rely on someone opening the admin page Monday morning.

**Pushover is fire-and-forget.** Never let a failed Pushover call block an API response. Log the error, return 200 to the client.

**Admin page must be responsive.** Allen and Diane will check it on their phones. The kid pages are mobile-first; the admin page must also be usable on a phone screen, not just a desktop.

---

## Seed Data (first run only)

Populate on first run if the database is empty:

**Kids:** Andrew (slug: `andrew`), Daniel (slug: `daniel`)

**Daily chores:**
- Make your bed
- Empty the dishwasher
- Tidy common areas (living room & family room)

**As-needed chores:**
- Take indoor trash to outdoor bins *(rotating)*
- Put bins out at curb *(rotating, Monday evenings)*
- Put clothes away *(non-rotating — both kids own their own)*

**Default rotating assignment (week 1):**
- Andrew → Take indoor trash to outdoor bins
- Daniel → Put bins out at curb

**Default settings:**
- `reading_weekly_target_minutes` = 175
- `outdoor_weekly_target_minutes` = 300
- `reminder_time` = 10:00
- `timezone` = America/New_York

---

## Definition of Done (v1)

- [ ] Kid pages work on Safari iOS 15: chores check off with undo toast, reading/outdoor quick-log buttons work, weekly progress and pace display correctly
- [ ] `/status` page shows both kids' current status, auto-refreshes every 60 seconds, no password required
- [ ] Admin dashboard: today's status cards at top, weekly progress, chore management (add/remove/toggle), as-needed assignment with per-kid one-tap buttons, daily chore admin override, rotating chore display
- [ ] Admin history page: week-by-week breakdown, streak summary at top
- [ ] Pushover fires correctly for: checklist complete, as-needed chore complete, mid-morning reminder (if checklist incomplete), Sunday 7pm summary
- [ ] Weekly results computed reliably; rotating chores auto-swap Monday
- [ ] Docker container builds and runs with `docker-compose up -d`; SQLite data persists across restarts
- [ ] All dates use local timezone; no UTC date bugs

---

## Out of Scope (do not build)

- HTTPS / SSL
- User accounts or login for kids
- Screen time tracking
- Allowance or rewards system
- Public internet access
- Any JS framework or build toolchain
