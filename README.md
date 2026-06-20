# Summer Tracker

A self-hosted family web app. Two kids (Andrew, Daniel) check off daily chores
and log reading/outdoor time on iPads; parents (Allen, Diane) get a read-only
status page and a password-protected admin dashboard. Notifications via Pushover.

Runs as a single Docker container on TrueNAS SCALE. Plain Flask + SQLite +
vanilla JS — no build step, works on Safari iOS 15 (old iPad).

---

## Pages

| URL | Who | Notes |
|-----|-----|-------|
| `/andrew`, `/daniel` | Kids | The URL is the identity — no login. Chores, reading & outdoor logs, scoreboard. |
| `/status` | Parents | Read-only, no password, auto-refreshes every 60s. |
| `/admin` | Parents | Password-protected dashboard. |
| `/admin/settings` | Parents | Targets, reminder time, program window, vacations/camp, Pushover, password. |
| `/admin/logs` | Parents | Edit/delete this week's reading & outdoor entries. |
| `/admin/history` | Parents | Week-by-week breakdown + streaks. |

---

## Configuration (`.env`)

All secrets and runtime config live in a `.env` file in the project root. It is
**gitignored** — never commit it. Create it from this template:

```dotenv
ADMIN_PASSWORD=change-me-before-first-run
PUSHOVER_APP_TOKEN=your-pushover-app-token
PUSHOVER_USER_KEY=your-pushover-user-key
PORT=7823
TZ=America/New_York
CHORE_DEBUG=0

# Optional — Uptime Kuma push monitor (see "Monitoring" below). Blank = disabled.
UPTIME_KUMA_PUSH_URL=
```

- **`ADMIN_PASSWORD`** — set this *before the first run*. It is hashed into the
  database the first time the app starts and is **not** re-read afterward. To
  change it later, use **/admin/settings**, or delete `data/chore_tracker.db` to
  re-seed from scratch.
- **`PUSHOVER_*`** — from your Pushover account. If blank, notifications are
  skipped (logged, never fatal). Can also be set later in /admin/settings.
- **`PORT`** — 7823 by design. Not 80/443/8080.
- **`TZ`** — all date logic uses this local timezone (never UTC).
- **`CHORE_DEBUG`** — leave `0` in production. When `1`, you can append
  `?today=YYYY-MM-DD` to any page to preview other dates (useful before the
  season starts). See "Local development" below.

---

## Deploy on TrueNAS SCALE (Docker)

1. Copy the project folder onto the TrueNAS host (git clone or file copy).
2. Create the `.env` file (above) in the project root with real values.
3. Build and start:

   ```bash
   docker compose up -d --build
   ```

4. Confirm it came up cleanly — you should see `Scheduler started (minute interval)`:

   ```bash
   docker compose logs -f
   ```

5. Point your Firewalla DNS entries at the container, e.g.
   `andrew.camp → <truenas-ip>:7823/andrew`. The app just answers at the right
   routes; it does not handle DNS itself.

The SQLite database persists in `./data/` (mounted as a volume), so it survives
container restarts and rebuilds.

**Updating:** pull/copy new code, then `docker compose up -d --build` again. The
DB migrates itself in place (non-destructive); your data is kept.

---

## Monitoring with Uptime Kuma

Two complementary monitors:

**1. HTTP monitor (web server + database)** — recommended for everyone.

- Add a new monitor in Uptime Kuma:
  - **Monitor Type:** HTTP(s)
  - **URL:** `http://<truenas-ip>:7823/healthz`
  - **Method:** GET, **Interval:** 60s
  - **Accepted Status Codes:** 200 (optionally add a Keyword check for `ok`)

  `/healthz` returns `{"status":"ok"}` with HTTP 200 when the app and SQLite are
  healthy, and 503 if the database can't be reached. It needs no password.

**2. Push monitor (background scheduler)** — optional, catches a subtler failure.

The HTTP check above can pass while the background job that sends reminders has
silently died. To also confirm the scheduler is ticking:

- In Uptime Kuma, add a monitor with **Monitor Type: Push**, interval 120s. Copy
  its push URL (looks like `http://<kuma-host>/api/push/<token>`).
- Put it in `.env` and restart the container:

  ```dotenv
  UPTIME_KUMA_PUSH_URL=http://<kuma-host>/api/push/<token>
  ```

  The scheduler pings that URL every minute; if the tick stops, Uptime Kuma marks
  it down. (Use 120s interval so a single missed minute doesn't false-alarm.)

If `UPTIME_KUMA_PUSH_URL` is blank, the heartbeat is simply disabled.

---

## Local development (Windows / any OS without Docker)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

# secrets optional locally; blank Pushover keys = no notifications
$env:CHORE_DEBUG = "1"
.\.venv\Scripts\python app.py
```

Then open http://localhost:7823/admin (log in with `ADMIN_PASSWORD`, default
`changeme`). Because the season starts 2026-06-22, preview in-season pages with
the debug date param, e.g. http://localhost:7823/andrew?today=2026-06-22.

Stop with `Ctrl+C`. The local database lives in `data/` and is gitignored.

### Tests

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```

Covers the risky logic: local-time date math, prorated goals, camp auto-credit,
pace guards, make-up Monday, scoreboard/streaks, rotation swap, and the
reminder/summary scheduler decisions.

---

## How it works (a few notes)

- **Single process.** The app runs single-process so the background scheduler
  fires reminders/summaries exactly once. Do not put it behind a multi-worker
  server.
- **Scheduler.** A minute-interval job sends the mid-morning reminder (if a kid's
  checklist isn't done by `reminder_time`) and the Sunday 7pm week summary.
  Everything else (weekly results, camp credit, rotation) computes on page load.
- **Timezone.** Every date is the local date in `TZ`. The `tzdata` package is a
  dependency so this works on Windows and slim containers alike.
- **Seed data.** On first run with an empty DB: the two kids, the daily /
  weekly / as-needed chores, week-1 rotation, default settings, the program
  window (2026-06-22 → 2026-08-30), and the two special periods (Alaska cruise,
  Congo camp).

## Source of truth

- `2026-06-19-chore-tracker-spec.md` — full product spec (read the
  "v1.1 Additions" and "v1.2" sections first; they extend the original).
- `CLAUDE.md` — locked decisions and working conventions.

## Backups

Handled at the infrastructure level: **TrueNAS snapshots** of the dataset holding
`data/`. The app does not run its own backup job (the spec's optional v1.1 E9
nightly copy was intentionally left to TrueNAS snapshots instead).
