# Chore Tracker — Product Spec

## Overview

A self-hosted web app running in Docker on TrueNAS SCALE. Two audiences: kids (Andrew, 11, and Daniel, 12) each get their own iPad-friendly URL to check off daily tasks and log time; parents (Allen and Diane) get a password-protected admin dashboard showing weekly rollups and history.

---

## v1.1 Additions & Fixes (2026-06-19) — READ FIRST

This section was added after the initial spec and **extends or supersedes** anything below that conflicts with it. Build to this section where it overlaps with the original spec.

### A. Program window, vacations, and camp

The program does not run all summer at full intensity — there are travel and camp periods that must not punish the kids or fire reminders. This is configured via a **program window** and a list of **special periods**, both seeded on first run and editable in admin Settings.

**Program window (settings keys):**
- `program_start_date` = `2026-06-22` (Monday; school ended Jun 18, this skips the half-week before)
- `program_end_date` = `2026-08-30` (Sunday; school resumes Aug 31)

Outside this window: kid pages show a friendly "Summer Tracker isn't running right now" state, no goals are evaluated, no reminders or summaries fire.

**`special_periods` table** (seeded on first run; editable in admin Settings):

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| label | TEXT | e.g. "Alaska cruise" |
| type | TEXT | `paused` or `outdoor_credit` |
| start_date | DATE | inclusive, local time |
| end_date | DATE | inclusive, local time |
| outdoor_minutes_per_day | INTEGER | only used when type = `outdoor_credit`; null otherwise |

**Seed rows (first run):**

| label | type | start_date | end_date | outdoor_minutes_per_day |
|-------|------|------------|----------|--------------------------|
| Alaska cruise | paused | 2026-07-03 | 2026-07-14 | (null) |
| Congo camp | outdoor_credit | 2026-07-20 | 2026-07-31 | 60 |

**`paused` behavior** (any day inside a paused range):
- No daily reminder, no Sunday summary that week.
- Kid-page weekly banner shows **"On break ☀️ — enjoy your trip!"** instead of green/red.
- The day does not count toward pace or goals.
- A week is **excluded from bonus pass/fail and from streak math** if it has *no active (non-paused) days* — see proration below. The cruise must not break a streak.

**`outdoor_credit` behavior** (Congo camp):
- For each **weekday** (Mon–Fri) inside the range, auto-credit `outdoor_minutes_per_day` (60) toward that day's/week's outdoor total. Implement as a synthetic, clearly-flagged outdoor log entry (e.g., `source = 'camp_auto'`) so it shows in totals but is distinguishable and not double-counted on re-runs (idempotent: one auto-entry per kid per camp weekday max).
- 5 camp weekdays × 60 = 300 min = meets the weekly outdoor goal exactly.
- **The daily checklist and the reading goal still apply during camp** — they're home evenings. Only outdoor is auto-handled.
- Reminders still fire during camp unless an admin turns them off.

### B. Prorated goals on shoulder weeks

Weeks that are only partly vacation (e.g., leave Jul 3, fly home Jul 14) must scale the goal to the days actually home — not pause the whole week (which would hand out free days) and not demand a full week's goal.

- For a given week, `active_days` = days in the Mon–Sun window that are **within the program window** and **not inside a `paused` period**.
- `prorated_reading_target = round(reading_weekly_target_minutes × active_days / 7)`
- `prorated_outdoor_target = round(outdoor_weekly_target_minutes × active_days / 7)`
- Pace, banner, bonus, and history all use the **prorated** targets for that week.
- If `active_days == 0`, the week is fully paused: no goals, no bonus evaluation, excluded from streak.
- `outdoor_credit` (camp) days **are** active days and use the full (non-reduced) target; the auto-credit fills them.
- Display the proration to parents on the admin/history view (e.g., "Target prorated to 4/7 days: Reading 100, Outdoor 171") so the numbers aren't mysterious.

### C. Make-up Monday (bonus reinstatement)

Replaces the old "miss a goal → lose the bonus all next week." If a kid misses either weekly goal, the bonus is **withheld at the start of the next week but recoverable on Monday**:

- When a week finalizes as a miss, compute the **deficit**: `reading_deficit = max(0, target − actual)` and `outdoor_deficit = max(0, target − actual)` (using that week's prorated targets). Store on a new `makeup_owed` row: `kid_id`, `for_week_start`, `reading_deficit`, `outdoor_deficit`, `satisfied_at`.
- On Monday the kid page shows a **make-up banner**: *"Bonus locked. To turn it back on today: read N more min, get M more outdoor min, and finish your checklist."* (Hide a line if that deficit is 0.)
- The bonus reinstates the moment **all** of: Monday's deficit reading minutes logged **and** deficit outdoor minutes logged **and** Monday's daily checklist complete. Set `satisfied_at`, flip the banner to green, fire a Pushover ("Andrew earned his bonus back!").
- If not made up Monday, the bonus stays locked for the week (status quo), but the make-up offer expires end of Monday — it's a same-day second chance, not an open-ended one.
- Make-up minutes also count toward the new week's normal totals (no double work).

### D. Summer Scoreboard (positive reward)

A visible "earn-up" layer, not just stakes.

- Each week a kid earns the bonus = **one star**. Paused weeks are skipped (neither earn nor break).
- Kid page: a small star row near the top — *"⭐⭐⭐ 3 bonus weeks!"* — plus the current streak (*"🔥 2-week streak"*). This is the cheapest motivation lever; put it on the kid page, not just history.
- Admin/history: total stars per kid across the summer.
- **End-of-summer reward: TBD** — Allen and the boys will choose it together. Store it as a settings key `scoreboard_reward_text` (free text, shown on the kid page as the goal, e.g. "Reach 6 stars by Aug 30 → ___"). Leave the value blank in seed; the kid page hides the reward line until it's filled in.

### E. Implementation fixes (apply throughout)

1. **Pace divide-by-zero:** `days_remaining = 7 − days_elapsed` is 0 on Sunday. Guard before dividing — if `days_remaining <= 0`, show met/not-met only, never compute pace. Same guard with prorated active-day counts.
2. **Quick-log accidental double-tap:** the +min reading/outdoor buttons must debounce (ignore a second tap within ~600ms) **and** show a 3-second undo toast like the chore checkboxes (*"+60 min added — Undo"*). A mis-tap on a slow iPad must be reversible.
3. **Kid "fix it" control:** below each log, show today's entries with a one-tap **× remove** for the current day only (e.g., *"Today: +60 (✕)  +15 (✕)"*). Lets a kid undo a wrong entry without a parent. Removing deletes that entry and re-totals.
4. **Admin log edit:** admin can view, edit the minutes of, or delete any reading/outdoor entry for either kid (covers fixes beyond the kid's same-day window).
5. **Scheduler reliability:** run the app **single-process** (no multi-worker gunicorn) so APScheduler doesn't fire reminders/summaries N times; the `notifications_sent` dedup check is the backstop. Reminders/summaries must come from **APScheduler**, never depend on a page being loaded (a quiet morning must still send the 10am reminder).
6. **iOS Safari caching:** send `Cache-Control: no-store` on kid and status pages so a reload always shows fresh totals on the old iPad.
7. **SQLite WAL mode:** enable `PRAGMA journal_mode=WAL;` for the two-iPads-at-once case.
8. **Null prior-week:** before any week has finalized, "Last week's bonus" renders as hidden/—, never "Bonus not earned ✗."
9. **Nightly backup:** add a simple nightly copy of the SQLite file into `./data/backups/` (timestamped, keep last ~14). Cheap insurance against corruption losing the summer.
10. **Per-kid targets:** store reading/outdoor targets keyed per kid (default both to 175/300) so they can diverge later without a schema change.

---

## v1.2 Additions (2026-06-19) — Weekly chore type

Chores now have **three occurrence types** instead of two. The admin console can
add / edit (rename, change type, toggle active) / delete chores, with a
**Daily / Weekly / As-needed** selector.

- **daily** — shown every day; checked off per day (unchanged).
- **weekly** *(new)* — a **standing per-kid assignment** (assigned in admin, like
  as-needed, but it recurs automatically). It shows on that kid's page as an open
  to-do **all week until they mark it off**; once done it shows checked/locked for
  the rest of the week. Resets Monday with the weekly window. A weekly chore is
  "done this week" if a `chore_completions` row exists anywhere in the current
  Mon–Sun week (no schema change to completions). The kid page shows weekly chores
  in their own **"This week"** section with a *"by Sunday"* hint.
- **as_needed** — admin assigns ad-hoc per kid (unchanged).

Decisions:
- **Weekly is tracked separately** — it does **not** feed the "daily checklist
  done" Pushover or the screen-time bonus / make-up / streak math. Bonus stays
  tied to daily chores + reading/outdoor goals only.
- **Delete = soft-delete** (`active = 0`): vanishes from kid pages immediately,
  history/weekly results preserved.

New table `weekly_assignments` (standing chore↔kid links):

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| chore_id | INTEGER FK | weekly chore |
| kid_id | INTEGER FK | who it recurs for |
| created_at | DATETIME | |

Seed (first run): one weekly chore **"Clean your room"** assigned to both kids.

---

## v1.4 Additions (2026-06-19) — Assignment independent of type

"Who does a chore" is now separate from "how often." Any recurring chore can be
assigned to one kid, both, or set to rotate — regardless of type.

- **Daily is no longer implicitly both.** Daily chores are assigned per kid like
  weekly/scheduled (via `weekly_assignments`). Each kid's **checklist = their assigned
  daily chores**; the checklist-complete notification, make-up Monday, and dashboard
  counts are all per kid. On upgrade, a migration assigns existing daily chores to both
  kids so nothing changes until you edit.
- **Rotate works on any type** (daily / weekly / scheduled / as-needed) via the existing
  `is_rotating` + rotation mechanism. A rotating chore alternates which kid does it weekly
  and shows only for that kid.
- **Admin:** a "Who does it" column in the Chores table — per-kid assign toggles + a Rotate
  toggle on each row. The old per-kid weekly/scheduled assignment block is gone; the
  per-kid section keeps only day-to-day overrides (mark-done, ad-hoc as-needed).
- One assignment check drives it all: `chore_assigned_to(chore, kid, week)` — rotating →
  this week's rotation pick, else a standing `weekly_assignments` row.

---

## v1.3 Additions (2026-06-19) — Scheduled chore type

A **fourth** chore type, **scheduled**, for recurring tasks tied to a weekday with a
lead-up countdown on the kid page (e.g., "Put bins out — Monday night").

Chore fields (added to `chores`): `due_weekday` (0=Mon..6=Sun), `reminder_lead_days`
(how many days before the due day the countdown starts), `due_label` (free text shown
to the kid, e.g. "Monday night").

- **Assignment:** rotating (reuses the existing weekly Andrew↔Daniel swap) or a fixed
  per-kid standing assignment (reuses `weekly_assignments`).
- **Kid page:** a "Coming up" card (only for the assigned kid). Outside the lead window
  it's hidden; inside, it shows "in N days (label)" with a checkbox counting down; on the
  due day "due today! (label)"; past due and not done "overdue — do it now!" (red) until
  checked off; done shows checked for the day.
- **Completion is occurrence-based** (tied to the due date, not the Mon–Sun week), so
  checking it off any time in the lead-up counts. Overdue is suppressed for occurrences
  earlier than the chore's `created_at` (a brand-new chore isn't instantly overdue).
- **Pushover:** on the due weekday at/after 7pm, if not done, one reminder per occurrence
  (deduped via `notifications_sent`, type `scheduled_due:<chore_id>`).
- **Seed/migration:** "Put bins out at curb" becomes scheduled (Monday, lead 5, rotating);
  existing deployments auto-migrate the row.

---

## Deployment

- **Host:** TrueNAS SCALE, existing Docker setup
- **Container:** Single Docker container (Python Flask + SQLite)
- **Database:** SQLite file mounted as a TrueNAS volume so data persists across container restarts
- **Port:** Configurable via environment variable (default: 7823)
- **DNS:** Firewalla DNS entries point kid-friendly hostnames to the container port. Example: `andrew.camp` → TrueNAS IP:7823/andrew
- **Environment variables for initial setup:**
  - `ADMIN_PASSWORD` — hashed on first run
  - `PUSHOVER_APP_TOKEN`
  - `PUSHOVER_USER_KEY`
  - `PORT` (default 7823)
  - `TZ` (default `America/New_York`)

---

## URL Structure

| URL | Who | Description |
|-----|-----|-------------|
| `/andrew` | Andrew | Andrew's kid page |
| `/daniel` | Daniel | Daniel's kid page |
| `/status` | Parents | Read-only parent status page (no password) |
| `/admin` | Parents | Admin dashboard (current week) |
| `/admin/history` | Parents | Week-by-week history |
| `/api/*` | Internal | REST API endpoints |

---

## Pages

### 1. Kid Page (`/andrew`, `/daniel`)

**Design:** Touch-friendly, large tap targets (minimum 48px), readable font sizes. Optimized for Safari on older iPads (iOS 15+). No login required — the URL is the identity. All CSS and JS must be compatible with iOS 15 Safari (no ES2022+, no CSS features unavailable before iOS 16).

**Layout (top to bottom):**

1. Kid's name + today's date
2. Weekly status banner
3. Rotating chore info line
4. Daily checklist
5. As-needed chores (if assigned)
6. Reading log
7. Outdoor time log

---

#### Today's Date
Display the current day and date prominently at the top (e.g., "Friday, June 19") so kids can confirm they're looking at today.

---

#### Weekly Status Banner
Displayed just below the date. One of three states:

- ✅ **"You earned your bonus screen time next week!"** — both weekly goals fully met
- 🟢 **"On track for bonus screen time — keep it up!"** — on pace for both goals with days remaining
- 🔴 **"Bonus screen time at risk — keep going!"** — behind pace on one or both goals

"On track" is defined as: current total ÷ days elapsed >= daily pace needed to hit the weekly target. If today is Monday and they've logged nothing, show green (full week ahead). Flip to red when they fall behind pace.

---

#### Rotating Chore Info Line
Informational only — no checkbox. Displays which as-needed rotating chore is assigned to this kid for the current week (pulled from the admin's rotation setting). Example:

> *"Your rotation this week: Put bins out Monday evening"*

If no rotating chore is set for this kid this week, this section is hidden.

---

#### Daily Checklist
- List of active daily chores pulled from the database (not hardcoded)
- Each chore is a large, tappable checkbox row
- Checkboxes are date-scoped — checking off a chore records it for today only
- **Undo toast:** After tapping a checkbox, a toast appears for 3 seconds: *"[Chore name] marked done — Undo"*. Tapping Undo within that window unchecks it. After 3 seconds the checkbox locks and cannot be unchecked.
- On page reload, today's already-locked chores appear pre-checked and non-interactive
- When **all daily chores** are checked and locked, show a celebration message in place of the checklist: *"🎉 All done! Dad has been notified."* Trigger a Pushover notification (once per kid per day — see Notifications section).

---

#### As-Needed Chores
- Only visible if the admin has assigned one or more as-needed chores to this kid
- Section header: *"Dad assigned you this today:"*
- Same checkbox + undo-toast mechanic as daily chores
- On each completion, trigger a separate Pushover notification: *"[Name] completed: [chore name]"*

---

#### Reading Log
- Section header: *"Reading"*
- Shows weekly progress: *"This week: 105 / 175 min"* with a progress bar
- Shows today's logged total: *"Today: 25 min logged"*
- Shows pace indicator: *"Need ~14 min/day to finish by Sunday"* (computed as `(target - weekly_total) ÷ days_remaining_in_week`, shown only if goal not yet met)
- **Quick-log buttons:** Four large tap buttons — **+15 min**, **+25 min**, **+30 min**, **+60 min** — each immediately add to the total on tap with a brief confirmation flash
- **Custom entry:** A collapsible "Enter custom amount" option with a number input + Submit for values not covered by the quick buttons
- Multiple log entries per day are allowed and additive

---

#### Outdoor Time Log
- Identical mechanic to Reading Log
- Section header: *"Outdoor Time"*
- Progress display in hours and minutes: *"This week: 2 hr 30 min / 5 hr"*
- Pace indicator: *"Need ~45 min/day to finish by Sunday"*
- Same quick-log buttons: **+15 min**, **+30 min**, **+60 min**, **+90 min**
- Custom entry collapsible

---

### 2. Parent Status Page (`/status`)

**No password required.** A read-only page Allen and Diane can bookmark on their phones for a quick at-a-glance view without logging in. No controls or editable fields — purely informational.

**Layout:**

- Today's date at the top
- Two cards (one per kid), each showing:
  - Kid's name
  - ✅ / ❌ Daily checklist status (with completion timestamp if done)
  - Any pending or completed as-needed chores assigned today
  - Reading progress this week: minutes logged / target, with progress bar
  - Outdoor progress this week: hours logged / target, with progress bar
  - Whether last week's bonus was earned
- Page auto-refreshes every 60 seconds so it stays current without manual reload
- Fully responsive — designed for phone use

This page intentionally has no admin controls. For assigning chores, overriding completions, or changing settings, Allen and Diane use `/admin`.

---

### 3. Admin Dashboard (`/admin`)

**Authentication:** Password prompt on first visit; session cookie persists for 7 days. Page must be fully responsive — usable on a phone as well as a desktop browser.

**Default view: Current Week**

---

#### Today's Status (top of page — most prominent section)

Two large cards side by side (stacked on mobile), one per kid. Each card shows:

- Kid's name as the card header
- **Large green or red status indicator:** ✅ *"Checklist done today"* or ❌ *"Checklist not done yet"*
- Timestamp of when checklist was completed (if done)
- Any active as-needed chores: pending or completed

This section answers the one question Allen and Diane open the page to ask.

---

#### This Week's Progress Panel

Below the top cards, for each kid:
- Daily checklist: how many days this week they completed it (e.g., 3/5 days so far)
- Reading: minutes logged / weekly target, progress bar, pace indicator
- Outdoor: hours logged / weekly target, progress bar, pace indicator
- Last week's bonus: *"Bonus unlocked ✓"* or *"Bonus not earned ✗"*

---

#### Chore Management

Table of all chores with columns: Name, Type (Daily / As-Needed), Active (toggle on/off), Delete.

- "Add Chore" button opens an inline form: chore name, type selector (Daily or As-Needed)
- Deleting a chore soft-deletes it (preserves history); it no longer appears on kid pages
- Toggling Active on a daily chore immediately shows/hides it on kid pages
- **Admin override for daily chore completions:** Each daily chore row has a per-kid "Mark done for today" button (visible only if the kid hasn't already completed it). Completions marked this way are flagged as `parent_verified = true` in the database. This covers the case where a kid did the chore but forgot to check it off.

---

#### As-Needed Chore Assignment

Displayed as two per-kid sections (not a dropdown flow). For each kid:

- Kid's name as the section header
- One-tap **Assign** button next to each as-needed chore (e.g., *"[Assign] Take indoor trash to outdoor bins"*)
- Currently assigned (incomplete) chores shown with a "Mark Complete" override button
- Completed as-needed chores from today shown with timestamp

---

#### Rotating Chore Manager

Simple two-column table showing which kid has which rotating chore this week:

| Chore | This Week |
|-------|-----------|
| Take indoor trash to outdoor bins | Andrew |
| Put bins out at curb | Daniel |

- Flips automatically every Monday (Andrew → Daniel, Daniel → Andrew)
- "Override this week" button allows manual swap if needed
- This data drives the informational line displayed on each kid's page

---

#### Settings

- Reading weekly target (minutes) — default 175
- Outdoor weekly target (minutes) — default 300 (5 hours)
- Week start day — fixed to Monday (display only, not editable in v1)
- **Mid-morning reminder time** — configurable time (default 10:00 AM); set to "off" to disable
- Pushover App Token
- Pushover User Key
- Admin password change
- Save button; changes take effect immediately

---

### 3. History Page (`/admin/history`)

Linked from the admin dashboard. Lists completed weeks newest-first.

**Summary strip at top:**
- For each kid: *"Bonus earned X of Y weeks"* — makes patterns visible at a glance
- Streak indicator: *"Andrew: 3-week streak ✓"* or *"Daniel: streak broken last week"*

**Per-week rows (expandable):**
- Week date range (Mon–Sun)
- For each kid:
  - Days with full daily checklist completed (e.g., 5/7)
  - Total reading minutes vs. target
  - Total outdoor minutes vs. target
  - Bonus earned: yes/no
- Chore-level breakdown on expand

---

## Data Model (SQLite)

### `kids`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT | Display name, e.g. "Andrew" |
| url_slug | TEXT UNIQUE | URL path, e.g. "andrew" |
| active | BOOLEAN | Default true |
| created_at | DATETIME | |

### `chores`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT | e.g. "Make your bed" |
| type | TEXT | "daily" or "as_needed" |
| active | BOOLEAN | Soft delete flag |
| created_at | DATETIME | |

### `chore_completions`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| chore_id | INTEGER FK | |
| completion_date | DATE | YYYY-MM-DD, local time |
| completed_at | DATETIME | Full timestamp |
| parent_verified | BOOLEAN | True if marked complete by admin |

### `as_needed_assignments`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| chore_id | INTEGER FK | |
| assigned_at | DATETIME | When admin assigned it |
| completed_at | DATETIME | Null until kid checks it off |

### `rotating_chore_assignments`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| chore_id | INTEGER FK | As-needed chore marked as rotating |
| kid_id | INTEGER FK | Who has it this week |
| week_start_date | DATE | Monday of the week |
| is_override | BOOLEAN | True if manually set by admin |

### `reading_logs`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| log_date | DATE | YYYY-MM-DD, local time |
| minutes | INTEGER | Minutes logged in this entry |
| logged_at | DATETIME | |

### `outdoor_logs`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| log_date | DATE | YYYY-MM-DD, local time |
| minutes | INTEGER | Minutes logged in this entry |
| logged_at | DATETIME | |

### `settings`
| Column | Type | Notes |
|--------|------|-------|
| key | TEXT PK | |
| value | TEXT | |

**Default settings keys:**
- `reading_weekly_target_minutes` → `175`
- `outdoor_weekly_target_minutes` → `300`
- `pushover_app_token` → (empty, set via admin or env var)
- `pushover_user_key` → (empty, set via admin or env var)
- `admin_password_hash` → (set from ADMIN_PASSWORD env var on first run)
- `week_start_day` → `monday`
- `timezone` → `America/New_York`
- `reminder_time` → `10:00` (24h format; set to `off` to disable)

### `notifications_sent`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| notification_date | DATE | |
| notification_type | TEXT | See types below |
| sent_at | DATETIME | |

**Notification types:** `daily_complete`, `as_needed_complete`, `morning_reminder`, `weekly_summary`

### `weekly_results`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| kid_id | INTEGER FK | |
| week_start_date | DATE | Monday of the week |
| reading_minutes | INTEGER | Total for that week |
| outdoor_minutes | INTEGER | Total for that week |
| bonus_earned | BOOLEAN | |
| computed_at | DATETIME | |

---

## Business Logic

### Weekly window
- Weeks run Monday 00:00 → Sunday 23:59 in the configured timezone
- All weekly totals computed by summing logs where `log_date` falls within the current Mon–Sun window
- History page uses the same Mon–Sun bucketing for past weeks

### Daily checklist completion
- A kid has "completed their checklist today" when every active daily chore has a `chore_completion` row with today's date (including parent-verified completions)
- Pushover fires exactly once per kid per day — check `notifications_sent` before sending

### Pace calculation
- `days_elapsed` = day of week index (Mon=1 … Sun=7), minimum 1
- `days_remaining` = 7 - days_elapsed
- `daily_pace_needed` = (target - current_total) ÷ days_remaining
- If `daily_pace_needed <= 0`, goal is met — hide pace indicator and show goal-complete state

### Weekly bonus logic
- Computed on **any page load** (kid or admin) if the prior week has not yet been finalized in `weekly_results`
- A week is considered finalized once its Sunday has passed in the configured timezone
- Bonus earned when: reading_minutes >= target AND outdoor_minutes >= target (both required)

### Rotating chore auto-assignment
- On Monday 00:00 (first request of the week in the configured timezone), check whether `rotating_chore_assignments` has rows for the current week
- If not, generate them by swapping from last week's assignments (Andrew → Daniel, Daniel → Andrew)
- If no prior week exists (first run), default: Andrew gets "Take indoor trash to outdoor bins," Daniel gets "Put bins out at curb"
- Admin override writes `is_override = true` and persists through the week

---

## Pushover Notifications

- Library: Python `requests` (simple HTTP POST)
- Endpoint: `https://api.pushover.net/1/messages.json`
- Fire-and-forget: log failures to console, do not block the API response
- All notifications go to the same Pushover user key (both Allen and Diane)

| Trigger | Title | Message |
|---------|-------|---------|
| Daily checklist complete | "Chore Tracker ✓" | "Andrew finished his daily checklist!" |
| As-needed chore complete | "Chore Tracker" | "Daniel took out the trash." |
| Mid-morning reminder (checklist not done by reminder_time) | "Chore Tracker 🔔" | "Andrew hasn't finished his checklist yet." |
| Sunday evening summary (sent at 7pm) | "Chore Tracker — Week Summary" | "Andrew: Reading 140/175 min ✗, Outdoor 5/5 hr ✓. Daniel: Reading 175/175 min ✓, Outdoor 3.5/5 hr ✗." |

**Mid-morning reminder logic:**
- Fires once per kid per day at the configured reminder time
- Only fires if the kid has NOT completed their checklist by that time
- Suppressed on days when the kid has no active chores (edge case)
- Implemented as a lightweight background thread that wakes up and checks every minute (or on each incoming request — check if current time >= reminder_time and notification not yet sent today)

**Sunday summary logic:**
- Fires once per week at 7pm Sunday
- Always sends, even if both kids are on track — gives Allen and Diane a heads-up before Monday

---

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Backend | Python 3.12 + Flask | Lightweight, easy to Docker-ize |
| Database | SQLite | File mounted as Docker volume |
| Frontend | Plain HTML + CSS + vanilla JS | No framework — fast on old iPad |
| CSS | Single stylesheet, mobile-first, responsive | Min 48px tap targets; responsive for admin on phone |
| Notifications | Pushover HTTP API | `requests` library |
| Background tasks | Python `threading` or `APScheduler` | For reminder and summary notifications |
| Container | Docker (single container) | `python:3.12-slim` base image |

---

## Docker Setup

### `Dockerfile`
```
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 7823
CMD ["python", "app.py"]
```

### `docker-compose.yml`
```yaml
version: "3.9"
services:
  summer-tracker:
    build: .
    ports:
      - "7823:7823"
    volumes:
      - ./data:/app/data
    environment:
      - ADMIN_PASSWORD=changeme
      - PUSHOVER_APP_TOKEN=
      - PUSHOVER_USER_KEY=
      - PORT=7823
      - TZ=America/New_York
    restart: unless-stopped
```

### `requirements.txt`
```
flask
requests
apscheduler
```

---

## Seed Data

On first run, populate so the app is immediately usable:

**Daily chores:**
- Make your bed
- Empty the dishwasher
- Tidy common areas (living room & family room)

**As-needed chores (rotating):**
- Take indoor trash to outdoor bins
- Put bins out at curb (Monday evenings)

**As-needed chores (non-rotating):**
- Put clothes away

**Kids:**
- Andrew (slug: `andrew`)
- Daniel (slug: `daniel`)

**Default rotating assignment (week 1):**
- Andrew → Take indoor trash to outdoor bins
- Daniel → Put bins out at curb

---

## Out of Scope (v1)

- User accounts per kid (URL is the identity)
- Mobile push notifications beyond Pushover
- Screen time tracking in the app (parents manage that manually)
- Allowance or point/reward system
- Public internet access / HTTPS (local network only)

---

## Open Questions for Developer

1. **Timezone handling:** All date logic must use the `TZ` environment variable. SQLite `log_date` values must be stored in local date (not UTC) to avoid midnight-rollover bugs. Use Python's `datetime.now(tz)` consistently.
2. **Old iPad compatibility:** Target Safari on iOS 15+. Avoid CSS features unavailable before iOS 16. No ES2022+ JS. Test the undo toast and quick-log buttons on touch before finalizing.
3. **Admin session:** Flask session with a secret key generated randomly on first run and stored in the settings table. 7-day cookie.
4. **Port conflicts:** Do not hardcode port in app logic — always read from environment variable.
5. **Background scheduler:** APScheduler is recommended for mid-morning reminders and Sunday summary. Ensure it starts with the Flask app and handles timezone-aware scheduling. Use `BackgroundScheduler` with `timezone` set from the `TZ` env var.
6. **Undo toast implementation:** Use a JavaScript `setTimeout` to lock the checkbox after 3 seconds. On tap, immediately record a "pending" state client-side, send the completion to the API after the 3-second window closes (not immediately on tap). If Undo is tapped, cancel the timeout and do not send.
7. **Weekly results reliability:** Do not rely solely on admin page load to finalize prior week results. Finalize on any page load (kid or admin) if the prior week is unfinalized.
