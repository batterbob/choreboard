# Chore Tracker — Project Memory

A self-hosted family web app: two kids (Andrew 11, Daniel 12) check off daily chores
and log reading/outdoor time on iPads; parents (Allen, Diane) view status and manage
chores from an admin dashboard. Notifications via Pushover.

## Source of truth
- **As-built spec:** `2026-06-19-summer-tracker-as-built.md` — **current** consolidated
  spec/handoff (chore model, pages, data model, deploy, monitoring). Read this first.
- **Original spec:** `2026-06-19-chore-tracker-spec.md` — historical, with v1.1–v1.4
  addenda. Superseded by the as-built doc where they differ.
- **Handoff:** `2026-06-19-chore-tracker-handoff.md` — build order, environment, and
  decisions. Follow its page priority order.

## Locked decisions — do not relitigate
- **URL = identity.** No kid login. `/andrew` is Andrew's page, `/daniel` is Daniel's.
- **SQLite only.** No Postgres, no Redis. File-based, mounted as a Docker volume.
- **No HTTPS.** Local network only. No SSL.
- **Plain HTML/CSS/vanilla JS.** No React/Vue, no build step. Must run on **Safari
  iOS 15** (old iPad): no ES2022+, no CSS features unavailable before iOS 16.
- **Python 3.12 + Flask** backend; `python:3.12-slim` base image; single container.
- **Port 7823.** Read from env var; never hardcode. Not 80/443/8080.
- **Weekly window:** Monday 00:00 → Sunday 23:59, configured timezone. Not rolling 7 days.
- **Pushover only.** No email, no SMS.
- **Single-process** (no multi-worker gunicorn) so APScheduler doesn't fire reminders N times.
- **Backups via TrueNAS snapshots**, not an app-level job. The spec's optional v1.1 E9
  nightly SQLite copy is intentionally not built — don't add it.

## Critical correctness rules
- **Local time, never UTC.** Every `log_date` is the local date in `TZ`
  (`America/New_York`). Use `datetime.now(ZoneInfo(os.environ['TZ']))`. Never
  `datetime.utcnow()`. Midnight-rollover date bugs are the #1 risk.
- **Pace math:** guard divide-by-zero — `days_remaining` is 0 on Sunday.
- **Undo toast on chore checkboxes:** POST only after the 3s window closes; cancel on Undo.
- **Quick-log buttons:** debounce double-taps + 3s undo; large (≥64px) touch targets.
- **Weekly results** finalize on ANY page load if a past week is unfinalized — not just admin.
- **Pushover is fire-and-forget:** log failures, never block the API response (return 200).
- **Vacations/camp/program window** drive proration, pauses, and camp outdoor auto-credit —
  see spec v1.1 section A/B. Streak must ignore paused weeks.
- **Five chore types** (spec v1.2/v1.3 + v1.5): daily, **alternate_daily**, **weekly**, as-needed, **scheduled**.
  `alternate_daily` shows on even or odd ones-digit days (`alt_day_parity` 0/1); if missed it shows as
  overdue the next day and blocks checklist completion. Weekly is a *standing per-kid assignment*
  (table `weekly_assignments`) that recurs every Monday and shows on the kid page until checked off
  that week. Weekly is **separate** from the daily-checklist notification and the bonus/make-up/streak
  math. Delete = soft-delete.
- **Assignment is independent of type** (spec v1.4): any recurring chore (daily/weekly/scheduled/
  alternate_daily) is assigned to specific kids via `weekly_assignments`, OR set to **rotate**
  (`is_rotating` + rotation) — works on *any* type including daily and as-needed. "Both" = two
  assignment rows. Daily is **no longer implicitly both**; each kid's checklist = their assigned
  daily chores (`assigned_daily_chores`). `chore_assigned_to(conn, chore, kid_id, ws)` is the one
  assignment check (rotating → this week's pick; else weekly_assignments). Admin sets assignment via
  a single **dropdown** in the "Who does it" column (`/admin/chore/set-who`); type is also a dropdown
  (`/admin/chore/set-type`). Migration assigns existing daily chores to both kids once.
- **Scheduled chores** (spec v1.3): recurring on a weekday (`due_weekday`) with a `reminder_lead_days`
  countdown + `due_label`, shown in the kid "Coming up" card. Can rotate (reuses rotation) or be
  assigned per kid (weekly_assignments). Completion is **occurrence-based** (tied to the due date,
  not the Mon–Sun week) so checking off during the lead-up counts; past-due+not-done = overdue until
  done; overdue is suppressed for occurrences before the chore's `created_at`. A Pushover fires on the
  due evening (≥17:00) if not done. Seeded/auto-migrated: "Put bins out at curb" = scheduled, Mon, lead 5.

## How to work here
- Build in the handoff's priority order: kid pages → `/status` → `/admin` → `/admin/history`.
  Get each working end-to-end (in Docker) before the next.
- Plan before coding on anything non-trivial; wait for my approval, then execute.
- Commit after each working page (`git commit`) so changes are revertible.
- Verify the risky math (timezone, weekly/pace, make-up Monday) with quick tests, not by eye.
- Run with `docker-compose up -d` and confirm SQLite data persists across restarts.

## My preferences
- Outline multi-step plans first and wait for approval before executing.
- Show what will change before deleting/overwriting/renaming any file; wait for confirmation.
- Name new files `YYYY-MM-DD-descriptive` (system files keep required names).
- List all files created/modified at the end of a task.
