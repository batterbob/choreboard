# Family Tracker — Project Memory

A self-hosted family web app for tracking kids' daily chores and activity goals (reading, outdoor time) during a structured program. Kids check off chores and log time on their own; parents get a read-only status page and a password-protected admin dashboard. Notifications via Apprise (Pushover, Telegram, Discord, Slack, ntfy, Gotify, or any Apprise URL).

## Locked decisions — do not relitigate
- **URL = identity.** No kid login. `/alex` is Alex's page, `/jordan` is Jordan's.
- **SQLite only.** No Postgres, no Redis. File-based, mounted as a Docker volume.
- **No HTTPS.** Local network only. No SSL.
- **Plain HTML/CSS/vanilla JS.** No React/Vue, no build step. Avoid newer CSS features (no :has, no container queries) for broad browser compatibility.
- **Python 3.12 + Flask** backend; `python:3.12-slim` base image; single container.
- **Port 7823.** Read from env var; never hardcode. Not 80/443/8080.
- **Weekly window:** Monday 00:00 → Sunday 23:59, configured timezone. Not rolling 7 days.
- **Single-process** (no multi-worker gunicorn) so APScheduler doesn't fire reminders N times.
- **No app-level backup job.** The data volume (`./data/`) is the user's responsibility to back up at the infrastructure level.

## Critical correctness rules
- **Local time, never UTC.** Every `log_date` is the local date in `TZ`. Use `datetime.now(ZoneInfo(...))`. Never `datetime.utcnow()`. Midnight-rollover date bugs are the #1 risk.
- **Pace math:** guard divide-by-zero — `days_remaining` is 0 on Sunday.
- **Undo toast on chore checkboxes:** POST only after the 3s window closes; cancel on Undo.
- **Quick-log buttons:** debounce double-taps + 3s undo; large (≥64px) touch targets.
- **Weekly results** finalize on ANY page load if a past week is unfinalized — not just admin.
- **Notifications are fire-and-forget:** log failures, never block the API response (return 200).
- **Vacations/camp/program window** drive proration, pauses, and camp outdoor auto-credit. Streak must ignore paused weeks.
- **Five chore types**: daily, alternate_daily, weekly, as-needed, scheduled.
  - `alternate_daily` shows on even or odd ones-digit days (`alt_day_parity` 0/1); if missed it shows overdue the next day and blocks checklist completion.
  - Weekly is a standing per-kid assignment (`weekly_assignments` table) that recurs every Monday.
  - Delete = soft-delete.
- **Assignment is independent of type**: any recurring chore is assigned to specific kids via `weekly_assignments`, OR set to rotate (`is_rotating` + rotation). "Both" = two assignment rows. Daily is not implicitly for both kids — each kid's checklist = their assigned daily chores (`assigned_daily_chores`).
- **Scheduled chores**: recurring on a weekday (`due_weekday`) with a `reminder_lead_days` countdown + `due_label`. Completion is occurrence-based (tied to the due date, not the Mon–Sun week).

## How to work here
- Plan before coding on anything non-trivial; wait for approval, then execute.
- Commit after each working feature (`git commit`) so changes are revertible.
- Verify the risky math (timezone, weekly/pace, make-up Monday) with unit tests, not by eye.
- Run the test suite: `.venv\Scripts\python -m unittest discover -s tests`

## Preferences
- Outline multi-step plans first and wait for approval before executing.
- Show what will change before deleting/overwriting/renaming any file.
