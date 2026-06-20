# Summer Tracker — As-Built Spec & Handoff

**Status:** built, tested (49 unit tests), deployed on TrueNAS. This document is the
current source of truth and supersedes the original `2026-06-19-chore-tracker-spec.md`
and its v1.1–v1.4 addenda (kept for history). Date: 2026-06-19.

---

## 1. Overview

A self-hosted family web app. Two kids (**Andrew**, **Daniel**) check off chores and
log reading/outdoor time on iPads; parents (**Allen**, **Diane**) get a read-only status
page and a password-protected admin dashboard. Notifications via **Pushover**.

- Single **Docker** container on **TrueNAS SCALE**, port **7823**.
- **Python 3.12 + Flask + SQLite**, plain HTML/CSS/vanilla JS (no build step).
- Runs on **Safari iOS 15** (old iPad): no ES2022+, no post-iOS15 CSS.
- All dates are **local** (`America/New_York`), never UTC.
- **Program window:** 2026-06-22 (Mon) → 2026-08-30 (Sun). Outside it, kid pages show
  "Summer Tracker isn't running right now" and nothing is evaluated.

---

## 2. Pages

| URL | Who | Auth | Notes |
|-----|-----|------|-------|
| `/andrew`, `/daniel` | Kids | none (URL = identity) | The everyday page |
| `/status` | Parents | none | Read-only, auto-refresh 60s |
| `/admin` | Parents | password (7-day cookie) | Dashboard + chore management |
| `/admin/settings` | Parents | password | Targets, schedule, vacations, Pushover, password |
| `/admin/logs` | Parents | password | Edit/delete this week's reading/outdoor entries |
| `/admin/history` | Parents | password | Week-by-week breakdown + streaks |
| `/healthz` | monitoring | none | `{"status":"ok"}` / 503 |

### Kid page (top to bottom)
1. Name + today's date
2. Scoreboard: ⭐ stars (bonus weeks) + 🔥 streak; optional reward line
3. Weekly bonus banner: **earned** ✅ / **on track** 🟢 / **at risk** 🔴 / **on break** ☀️
4. Make-up banner (Mondays only, if a bonus is recoverable)
5. Rotation info line (only for a rotating **as-needed** chore)
6. **Daily checklist** — the kid's assigned daily chores, undo-toast checkboxes
7. **This week** — assigned weekly chores ("by Sunday")
8. **Coming up** — scheduled chores with countdown / due / overdue
9. **As-needed** — chores the admin assigned today
10. Reading log + Outdoor log (progress bar, pace, big quick-add buttons, custom entry, same-day ✕ remove)

Kid interactions: tapping a checkbox or a +min button shows a **3-second undo toast** and
only POSTs after the window closes (cancel = no POST). Quick-log buttons debounce double-taps.
`Cache-Control: no-store` on kid/status/admin/api so the old iPad always shows fresh totals.

---

## 3. Chore model

A chore has a **type** (how often / how it behaves) and an **assignment** (who does it).
These are **independent** — any chore can go to one kid, both, or rotate.

### Types
- **Daily** — shown every day; "done" = checked that day. A kid's *checklist* = their
  assigned daily chores; finishing it fires the daily-complete Pushover.
- **Every other day** (`alternate_daily`) — shown on alternating days based on
  `alt_day_parity` (0 = even ones-digit of day-of-month, 1 = odd). If not done on the
  "on" day it shows as overdue the next day (red border + "do it today!") until completed.
  Blocks checklist completion just like daily chores. Parity configured via "Even days /
  Odd days" picker in the chore edit form.
- **Weekly** — shown all week until checked; "done" = a completion anywhere in the Mon–Sun
  week. Shown in "This week".
- **Scheduled** — recurring on a **weekday** with a lead-up **countdown** (`due_weekday`,
  `reminder_lead_days`, `due_label`). Shown in "Coming up": hidden until within the lead
  window, then "in N days (label)", "due today!", or "overdue" until done. Completion is
  **occurrence-based** (tied to the due date, so checking off during the lead-up counts);
  overdue is suppressed for occurrences before the chore was created.
- **As-needed** — admin assigns an instance ad-hoc; kid checks it off once.

### Assignment (independent of type)
- **Fixed:** assigned to specific kids (table `weekly_assignments`, chore↔kid). "Both" =
  two rows.
- **Rotating:** `is_rotating=1` + weekly rotation (`rotating_chore_assignments`). Alternates
  Andrew↔Daniel each week; shows only for that week's kid. Works on **any** type.
- One check governs visibility: `chore_assigned_to(chore, kid, week)` → rotating? this
  week's pick : a `weekly_assignments` row.
- Daily chores are **not** implicitly both; the seed/migration assigns existing daily chores
  to both kids so behavior is preserved until you change it.

### Admin chore management (`/admin`)
- **Chores table:** Name · Type · **Who does it** · Active · Edit · Delete.
  - *Type:* single dropdown that auto-submits on change (`/admin/chore/set-type`).
    Preserves existing scheduled/alt-parity config when switching to those types; sets
    defaults otherwise. Use Edit to fine-tune scheduled day, lead, and label.
  - *Who does it:* single dropdown per row (`/admin/chore/set-who`), auto-submits on
    change. Options: `— Unassigned`, `Andrew & Daniel`, `Andrew only`, `Daniel only`,
    `Rotate → Andrew starts`, `Rotate → Daniel starts`. When already rotating, shows
    a disabled `Rotates (Kid)` placeholder plus the other kid's rotate option and all
    stop options. As-needed chores show a note instead ("Assigned in overrides below").
  - *Edit:* inline rename + change type; scheduled fields and every-other-day parity
    picker shown/grayed by selected type.
  - *Delete:* soft-delete (hidden from kids & admin, history kept).
- **Assignments & overrides** — two separate cards, each showing Andrew and Daniel side
  by side (divided by a vertical line):
  - *Mark a daily chore done today* — buttons for each incomplete daily chore per kid.
  - *As-needed assignments* — pending/done status per kid, plus Assign buttons for all
    as-needed chores.
- **Rotating chores:** chore→kid table with per-chore **Swap** button + **Swap all**.

### Seed chores (first run)
- Daily (both kids): Make your bed; Empty the dishwasher; Tidy common areas
- Weekly (both kids): Clean your room
- As-needed rotating: Take indoor trash to outdoor bins
- As-needed (assign per day): Put clothes away
- **Scheduled rotating:** Put bins out at curb — Monday, 5-day countdown, "Monday night"

---

## 4. Business logic

- **Week:** Monday 00:00 → Sunday 23:59 local.
- **Goals/bonus:** bonus earned when weekly reading ≥ target **and** outdoor ≥ target
  (checklist does **not** affect the bonus). Targets are **per kid** (default 175 reading /
  300 outdoor min).
- **Pace / banner:** on-track if you've met the share required by the start of today;
  Monday-with-nothing is green. Divide-by-zero guarded (no pace on the last active day).
- **Proration (shoulder weeks):** a week's targets scale to its **active days** (in program
  window, not paused): `target × active/7`. A fully-paused week (0 active days) is excluded
  from bonus and streak. Camp days count as active (full target; the auto-credit fills them).
- **Special periods:**
  - *Paused* (e.g. Alaska cruise 7/3–7/14): days don't count; banner "on break"; no reminders/
    summary; can't break a streak.
  - *Outdoor credit* (e.g. Congo camp 7/20–7/31): auto-adds N min outdoor each weekday
    (synthetic `source='camp_auto'` log, idempotent). Checklist + reading still apply.
- **Rotation:** auto-advances each Monday (backfilling missed weeks), swapping the two kids;
  admin can override-swap a week.
- **Weekly results** finalize on **any** page load once a week's Sunday has passed.
- **Make-up Monday:** if a kid misses a weekly goal, the bonus is withheld but recoverable
  the next Monday — log the deficit reading + outdoor minutes **and** finish the checklist
  that Monday → bonus reinstated + Pushover. Offer expires end of Monday.
- **Scoreboard:** one ⭐ per bonus week; streak of consecutive non-paused bonus weeks
  (paused weeks skipped, never break it). Reward text shown on the kid page if set.

---

## 5. Notifications (Pushover)

Fire-and-forget (a failure never blocks a response); deduped via `notifications_sent`.

| Event | When |
|-------|------|
| Checklist complete | Kid finishes their assigned daily chores (once/kid/day) |
| As-needed complete | Kid checks off an assigned as-needed chore |
| Mid-morning reminder | At `reminder_time` (default 10:00) if a kid's checklist isn't done; per kid; "off" disables |
| Scheduled due | On a scheduled chore's due weekday ≥ 17:00 if not done (once per occurrence) |
| Make-up earned back | When a Monday make-up is satisfied |
| Sunday summary | Sunday 19:00 — per-kid reading/outdoor vs target |

The mid-morning reminder, scheduled-due, and Sunday summary come from a **single
minute-interval APScheduler job** running in the one Flask process. It also sends the
Uptime Kuma heartbeat. Off-season / paused days suppress reminders + summary.

---

## 6. Data model (SQLite, WAL)

- **kids** — id, name, url_slug, active, **reading_target_minutes**, **outdoor_target_minutes**, created_at
- **chores** — id, name, type (`daily|alternate_daily|weekly|scheduled|as_needed`), is_rotating, active, **deleted**,
  **due_weekday**, **reminder_lead_days**, **due_label**, **alt_day_parity** (0=even, 1=odd), created_at
- **weekly_assignments** — chore_id, kid_id (fixed assignment for daily/weekly/scheduled)
- **rotating_chore_assignments** — chore_id, kid_id, week_start_date, is_override
- **chore_completions** — kid_id, chore_id, completion_date, completed_at, parent_verified
- **as_needed_assignments** — kid_id, chore_id, assigned_at, completed_at
- **reading_logs / outdoor_logs** — kid_id, log_date, minutes, **source** (`manual|camp_auto`), logged_at
- **weekly_results** — kid_id, week_start_date, reading/outdoor minutes + targets, active_days,
  is_paused_week, bonus_earned, computed_at
- **makeup_owed** — kid_id, for_week_start, reading_deficit, outdoor_deficit, satisfied_at
- **special_periods** — label, type (`paused|outdoor_credit`), start_date, end_date, outdoor_minutes_per_day
- **notifications_sent** — kid_id, notification_date, notification_type, sent_at (UNIQUE dedup;
  household summary uses kid_id=0)
- **settings** — key/value (targets, reminder_time, program window, Pushover tokens, admin
  password hash, flask secret, scoreboard reward, migration flags)

Migrations run on startup (non-destructive `ADD COLUMN` + one-time data fixes): `deleted`,
scheduled columns, bins→scheduled, daily→both-kids.

---

## 7. Tech stack & repo

- Backend: Python 3.12 + Flask, SQLite (WAL). Scheduler: APScheduler. Pushover via `requests`.
  Password hashing: scrypt (stdlib).
- Frontend: server-rendered Jinja templates, one CSS file, minimal vanilla JS.

```
app.py            Flask routes, view-models, API
logic.py          dates, assignment, checklist, weekly/pace/proration, camp,
                  rotation, make-up, scoreboard, scheduled-state
db.py             schema, connection (WAL), seed, migrations, password hashing
scheduler.py      APScheduler job: reminders, scheduled-due, summary, Kuma heartbeat
notify.py         Pushover (fire-and-forget + dedup)
templates/        kid.html, status.html, admin.html, settings.html, logs.html,
                  history.html, admin_login.html, _log_section.html
static/           style.css, kid.js
tests/            test_logic.py (49 tests)
Dockerfile, docker-compose.yml, requirements.txt, .env (gitignored)
```

GitHub: `https://github.com/batterbob/summer-tracker` (private).

---

## 8. Deployment (TrueNAS SCALE)

```bash
cd /mnt/tank/summer-tracker
git pull                       # first time: git clone ... .
nano .env                      # see below; created on the host, never in git
docker compose up -d --build
docker compose logs -f         # expect "Scheduler started (minute interval)"
```

`.env`:
```dotenv
ADMIN_PASSWORD=...        # hashed into the DB on first run; change later in Settings
PUSHOVER_APP_TOKEN=...
PUSHOVER_USER_KEY=...
PORT=7823
TZ=America/New_York
CHORE_DEBUG=0             # 1 enables ?today=YYYY-MM-DD previews
UPTIME_KUMA_PUSH_URL=     # optional, see monitoring
```

- Data persists in `./data/` (mounted volume). Updates: `git pull && docker compose up -d --build`
  (DB self-migrates).
- DNS via Firewalla maps friendly names → TrueNAS IP; kids open NGINX-Proxy-Manager-fronted
  `andrew.camp` / `daniel.camp` (proxy rewrites `/` → `/andrew` etc.).

---

## 9. Monitoring (Uptime Kuma)

- **HTTP monitor** → `http://<truenas-ip>:7823/healthz`, interval 60s, expect 200. Covers
  the web app + DB.
- **Push monitor** (optional) → set `UPTIME_KUMA_PUSH_URL` to the Kuma push URL (use the
  **base** URL; the app adds `status=up&msg=OK`). The scheduler pings it each minute, so this
  also proves the background job is alive. Set the monitor's interval to **120s**.

---

## 10. Backups

TrueNAS dataset **snapshots** of the folder holding `data/`. The app has no built-in backup job.

---

## 11. Locked decisions

URL = identity (no kid login) · SQLite only · no HTTPS (LAN) · plain HTML/CSS/JS, iOS-15
floor · single process (so the scheduler fires once) · port 7823 · Monday–Sunday week ·
Pushover only · local time, never UTC · backups via TrueNAS snapshots.

## 12. Out of scope

User accounts, screen-time tracking, allowance/points, public internet/HTTPS, email/SMS,
app-level nightly backup.
