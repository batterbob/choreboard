"""Date, weekly, proration, camp, make-up, and scoreboard logic.

Every date is the LOCAL date in the configured timezone. Functions take an
explicit `today`/date argument (a ``datetime.date``) so they stay pure and
unit-testable; the web layer decides what "today" is.
"""
import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DATE_FMT = "%Y-%m-%d"


# --------------------------------------------------------------------------- #
# Time helpers — local time only, never UTC.
# --------------------------------------------------------------------------- #
def get_tz(env=None, conn=None):
    if conn is not None:
        tz_name = get_setting(conn, "timezone", None)
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                pass
    env = env if env is not None else os.environ
    return ZoneInfo(env.get("TZ", "America/New_York"))


def now(env=None):
    """Timezone-aware 'now' in local time."""
    return datetime.now(get_tz(env))


def now_iso(env=None):
    return now(env).isoformat(timespec="seconds")


def today(env=None):
    return now(env).date()


def d2s(d):
    return d.strftime(DATE_FMT)


def s2d(s):
    return datetime.strptime(s, DATE_FMT).date()


def week_start(d):
    """Monday of the week containing d (Monday.weekday() == 0)."""
    return d - timedelta(days=d.weekday())


def week_end(ws):
    return ws + timedelta(days=6)


def week_dates(ws):
    return [ws + timedelta(days=i) for i in range(7)]


# --------------------------------------------------------------------------- #
# Settings & kids
# --------------------------------------------------------------------------- #
def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def kid_by_slug(conn, slug):
    return conn.execute("SELECT * FROM kids WHERE url_slug=? AND active=1",
                        (slug,)).fetchone()


def active_kids(conn):
    return conn.execute("SELECT * FROM kids WHERE active=1 ORDER BY id").fetchall()


# --------------------------------------------------------------------------- #
# Program window & special periods (v1.1 A)
# --------------------------------------------------------------------------- #
def program_window(conn):
    start_s = get_setting(conn, "program_start_date", "")
    end_s = get_setting(conn, "program_end_date", "")
    start = s2d(start_s) if start_s else date.today()
    end = s2d(end_s) if end_s else date(2099, 12, 31)
    return start, end


def in_program_window(conn, d):
    start, end = program_window(conn)
    return start <= d <= end


def special_periods(conn, ptype=None):
    if ptype:
        return conn.execute("SELECT * FROM special_periods WHERE type=? "
                            "ORDER BY start_date", (ptype,)).fetchall()
    return conn.execute("SELECT * FROM special_periods ORDER BY start_date").fetchall()


def paused_period_on(conn, d):
    """Return the paused special_period covering date d, or None."""
    s = d2s(d)
    return conn.execute(
        "SELECT * FROM special_periods WHERE type='paused' "
        "AND start_date <= ? AND end_date >= ? LIMIT 1", (s, s)).fetchone()


def is_paused(conn, d):
    return paused_period_on(conn, d) is not None


def paused_chore_ids_on(conn, d):
    """Set of chore IDs explicitly paused by any special period active on date d."""
    s = d2s(d)
    rows = conn.execute(
        "SELECT spc.chore_id FROM special_period_paused_chores spc "
        "JOIN special_periods sp ON sp.id = spc.special_period_id "
        "WHERE sp.start_date <= ? AND sp.end_date >= ?", (s, s)).fetchall()
    return {r["chore_id"] for r in rows}


# --------------------------------------------------------------------------- #
# Activities (generic, v2) — registry + per-activity logs and targets
# --------------------------------------------------------------------------- #
def activities(conn, enabled_only=True):
    """Activity rows ordered for display. Pass enabled_only=False for admin lists."""
    sql = "SELECT * FROM activities"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY sort_order, id"
    return conn.execute(sql).fetchall()


def activity_by_key(conn, key):
    return conn.execute("SELECT * FROM activities WHERE key=?", (key,)).fetchone()


def activity_by_id(conn, activity_id):
    return conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()


def activity_target(conn, activity, kid_id):
    """Per-kid weekly target, falling back to the activity's default."""
    row = conn.execute(
        "SELECT target FROM activity_targets WHERE activity_id=? AND kid_id=?",
        (activity["id"], kid_id)).fetchone()
    return row["target"] if row else activity["default_target"]


def set_activity_target(conn, activity_id, kid_id, target):
    conn.execute(
        "INSERT INTO activity_targets (activity_id, kid_id, target) VALUES (?,?,?) "
        "ON CONFLICT(activity_id, kid_id) DO UPDATE SET target=excluded.target",
        (activity_id, kid_id, target))


def weekly_activity_total(conn, activity_id, kid_id, ws):
    we = week_end(ws)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS m FROM activity_logs "
        "WHERE activity_id=? AND kid_id=? AND log_date >= ? AND log_date <= ?",
        (activity_id, kid_id, d2s(ws), d2s(we))).fetchone()
    return row["m"]


def today_activity_total(conn, activity_id, kid_id, d):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS m FROM activity_logs "
        "WHERE activity_id=? AND kid_id=? AND log_date=?",
        (activity_id, kid_id, d2s(d))).fetchone()
    return row["m"]


def today_activity_entries(conn, activity_id, kid_id, d):
    """Manual entries for the current day, for the kid's one-tap ✕ remove."""
    return conn.execute(
        "SELECT id, amount FROM activity_logs WHERE activity_id=? AND kid_id=? "
        "AND log_date=? AND source='manual' ORDER BY id",
        (activity_id, kid_id, d2s(d))).fetchall()


def activity_required_days_in_week(conn, ws, activity_id):
    """Active days in week ws where this activity is NOT paused by a special period."""
    count = 0
    for d in week_dates(ws):
        if not is_active_day(conn, d):
            continue
        s = d2s(d)
        hit = conn.execute(
            "SELECT 1 FROM special_period_paused_activities spa "
            "JOIN special_periods sp ON sp.id = spa.special_period_id "
            "WHERE spa.activity_id=? AND sp.start_date <= ? AND sp.end_date >= ? LIMIT 1",
            (activity_id, s, s)).fetchone()
        if hit is None:
            count += 1
    return count


def is_active_day(conn, d):
    """A day counts toward goals if it's in the program window and not paused."""
    return in_program_window(conn, d) and not is_paused(conn, d)


def active_days_in_week(conn, ws):
    return sum(1 for d in week_dates(ws) if is_active_day(conn, d))


# --------------------------------------------------------------------------- #
# Camp outdoor auto-credit (v1.1 A, outdoor_credit) — idempotent.
# --------------------------------------------------------------------------- #
def ensure_camp_credit(conn, upto):
    """Insert one synthetic credit log per kid per camp WEEKDAY, up to `upto`.

    Each 'outdoor_credit' period credits its `credit_activity_id` (defaults to the
    outdoor activity). Idempotent: a (kid, activity, log_date, source='credit_auto')
    row is inserted at most once, so re-running on every page load never double-counts.
    """
    kids = active_kids(conn)
    outdoor = activity_by_key(conn, "outdoor")
    default_aid = outdoor["id"] if outdoor else None
    for period in special_periods(conn, "outdoor_credit"):
        aid = period["credit_activity_id"] or default_aid
        if aid is None:
            continue
        amount = period["outdoor_minutes_per_day"] or 0
        d = s2d(period["start_date"])
        end = s2d(period["end_date"])
        while d <= end and d <= upto:
            # Weekdays only (Mon-Fri); program window; not paused.
            if d.weekday() < 5 and is_active_day(conn, d):
                ds = d2s(d)
                for kid in kids:
                    exists = conn.execute(
                        "SELECT 1 FROM activity_logs WHERE activity_id=? AND kid_id=? "
                        "AND log_date=? AND source='credit_auto' LIMIT 1",
                        (aid, kid["id"], ds)).fetchone()
                    if not exists:
                        conn.execute(
                            "INSERT INTO activity_logs (activity_id, kid_id, log_date, "
                            "amount, source, logged_at) VALUES (?,?,?,?, 'credit_auto', ?)",
                            (aid, kid["id"], ds, amount, now_iso()))
            d += timedelta(days=1)
    conn.commit()


# --------------------------------------------------------------------------- #
# Targets & weekly totals
# --------------------------------------------------------------------------- #
def prorated_targets(conn, kid, ws):
    """Targets scaled to the active days in this Mon-Sun window (v1.1 B).

    Returns active_days/full plus a `by_activity` map {activity_id: target}.
    Each activity's target is further reduced for days it's paused by a special
    period. Legacy `reading`/`outdoor` keys are kept for callers that predate the
    generic activities model.
    """
    active = active_days_in_week(conn, ws)
    full = active == 7
    by_activity = {}
    legacy = {"reading": 0, "outdoor": 0}
    for a in activities(conn, enabled_only=True):
        if active == 0:
            t = 0
        else:
            days = activity_required_days_in_week(conn, ws, a["id"])
            t = round(activity_target(conn, a, kid["id"]) * days / 7)
        by_activity[a["id"]] = t
        if a["key"] in legacy:
            legacy[a["key"]] = t
    return {"active_days": active, "full": full, "by_activity": by_activity, **legacy}


# Legacy compatibility wrappers — map the old reading/outdoor calls onto the
# generic activity helpers so existing callers keep working during the migration.
def weekly_reading(conn, kid_id, ws):
    a = activity_by_key(conn, "reading")
    return weekly_activity_total(conn, a["id"], kid_id, ws) if a else 0


def weekly_outdoor(conn, kid_id, ws):
    a = activity_by_key(conn, "outdoor")
    return weekly_activity_total(conn, a["id"], kid_id, ws) if a else 0


# --------------------------------------------------------------------------- #
# Daily checklist
# --------------------------------------------------------------------------- #
def active_daily_chores(conn):
    return conn.execute(
        "SELECT * FROM chores WHERE type IN ('daily', 'alternate_daily') "
        "AND active=1 ORDER BY id").fetchall()


# --------------------------------------------------------------------------- #
# Alternate-daily helpers — every-other-day chores keyed on ones digit of date
# --------------------------------------------------------------------------- #
def _alt_daily_most_recent_due(chore, d):
    """The most recent due date on or before d (today if due today, else yesterday)."""
    parity = chore["alt_day_parity"] or 0
    return d if d.day % 2 == parity else d - timedelta(days=1)


def _alt_daily_done(conn, kid_id, chore, d):
    """True if the most recent due occurrence has a completion on or after it."""
    due = _alt_daily_most_recent_due(chore, d)
    row = conn.execute(
        "SELECT 1 FROM chore_completions WHERE kid_id=? AND chore_id=? "
        "AND completion_date >= ? AND completion_date <= ? LIMIT 1",
        (kid_id, chore["id"], d2s(due), d2s(d))).fetchone()
    return row is not None


def alt_daily_is_overdue(chore, d):
    """True when today is an off-day and the chore appears because it was missed."""
    parity = chore["alt_day_parity"] or 0
    return d.day % 2 != parity


def _alt_daily_show_today(conn, kid_id, chore, d):
    """Show in checklist today if it's a due day, or yesterday was due and not done."""
    parity = chore["alt_day_parity"] or 0
    if d.day % 2 == parity:
        return True
    yesterday = d - timedelta(days=1)
    if yesterday.day % 2 == parity:
        return not _alt_daily_done(conn, kid_id, chore, d)
    return False


def completed_chore_ids(conn, kid_id, d):
    rows = conn.execute(
        "SELECT chore_id, completed_at FROM chore_completions "
        "WHERE kid_id=? AND completion_date=?", (kid_id, d2s(d))).fetchall()
    return {r["chore_id"]: r["completed_at"] for r in rows}


def chore_assigned_to(conn, chore, kid_id, ws):
    """Is this (recurring) chore for kid_id this week? Rotating -> this week's
    rotation pick; otherwise -> a standing assignment in weekly_assignments.
    (v1.4: assignment is independent of chore type.)"""
    if chore["is_rotating"]:
        row = conn.execute(
            "SELECT 1 FROM rotating_chore_assignments WHERE chore_id=? AND kid_id=? "
            "AND week_start_date=? LIMIT 1", (chore["id"], kid_id, d2s(ws))).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM weekly_assignments WHERE chore_id=? AND kid_id=? LIMIT 1",
            (chore["id"], kid_id)).fetchone()
    return row is not None


def assigned_daily_chores(conn, kid_id, d):
    """Active daily and alternate_daily chores assigned to this kid, visible today."""
    ws = week_start(d)
    paused_ids = paused_chore_ids_on(conn, d)
    chores = conn.execute(
        "SELECT * FROM chores WHERE type IN ('daily', 'alternate_daily') "
        "AND active=1 ORDER BY id").fetchall()
    result = []
    for c in chores:
        if c["id"] in paused_ids:
            continue
        if not chore_assigned_to(conn, c, kid_id, ws):
            continue
        if c["type"] == "daily" or _alt_daily_show_today(conn, kid_id, c, d):
            result.append(c)
    return result


def checklist_status(conn, kid_id, d):
    """(done, completed_at) — done when every daily chore ASSIGNED TO THIS KID is
    checked. Daily chores are per-kid now (v1.4); weekly/scheduled don't count and
    don't gate the checklist-complete notification or the bonus.
    """
    chores = assigned_daily_chores(conn, kid_id, d)
    if not chores:
        return (False, None)  # no chores -> never "complete"; suppresses reminders
    done_map = completed_chore_ids(conn, kid_id, d)
    if all(c["id"] in done_map for c in chores):
        return (True, max(done_map[c["id"]] for c in chores))
    return (False, None)


def checklist_days_this_week(conn, kid_id, d):
    """(completed, elapsed) — daily-checklist days finished vs active days so far.

    Counts only active (in-program, non-paused) days from Monday through today.
    """
    ws = week_start(d)
    completed = elapsed = 0
    for dd in week_dates(ws):
        if dd > d:
            break
        if not is_active_day(conn, dd):
            continue
        elapsed += 1
        done, _ = checklist_status(conn, kid_id, dd)
        if done:
            completed += 1
    return completed, elapsed


def checklist_days_in_week(conn, kid_id, ws):
    """(completed, active) over the FULL Mon-Sun week — for the history page."""
    completed = active = 0
    for dd in week_dates(ws):
        if not is_active_day(conn, dd):
            continue
        active += 1
        done, _ = checklist_status(conn, kid_id, dd)
        if done:
            completed += 1
    return completed, active


def weekly_points(conn, kid_id, ws):
    """Total chore points a kid earned in the Mon-Sun week starting at ws.

    Sums `chores.points` over every completion that week — both checklist-style
    completions (chore_completions) and finished as-needed assignments.
    """
    a, b = d2s(ws), d2s(week_end(ws))
    checklist = conn.execute(
        "SELECT COALESCE(SUM(c.points), 0) AS pts FROM chore_completions cc "
        "JOIN chores c ON c.id = cc.chore_id "
        "WHERE cc.kid_id=? AND cc.completion_date>=? AND cc.completion_date<=?",
        (kid_id, a, b)).fetchone()["pts"]
    asneeded = conn.execute(
        "SELECT COALESCE(SUM(c.points), 0) AS pts FROM as_needed_assignments an "
        "JOIN chores c ON c.id = an.chore_id "
        "WHERE an.kid_id=? AND an.completed_at IS NOT NULL "
        "AND substr(an.completed_at,1,10)>=? AND substr(an.completed_at,1,10)<=?",
        (kid_id, a, b)).fetchone()["pts"]
    return (checklist or 0) + (asneeded or 0)


# --------------------------------------------------------------------------- #
# Weekly chores (v1.2) — standing per-kid assignments, done once per week.
# --------------------------------------------------------------------------- #
def weekly_done(conn, kid_id, chore_id, d):
    """A weekly chore is done if any completion falls in the current Mon-Sun week."""
    ws = week_start(d)
    row = conn.execute(
        "SELECT 1 FROM chore_completions WHERE kid_id=? AND chore_id=? "
        "AND completion_date >= ? AND completion_date <= ? LIMIT 1",
        (kid_id, chore_id, d2s(ws), d2s(week_end(ws)))).fetchone()
    return row is not None


def weekly_chores_for_kid(conn, kid_id, d):
    """Active weekly chores for this kid (fixed or rotating), with done flag."""
    ws = week_start(d)
    paused_ids = paused_chore_ids_on(conn, d)
    chores = conn.execute(
        "SELECT * FROM chores WHERE type='weekly' AND active=1 ORDER BY id").fetchall()
    return [{"id": c["id"], "name": c["name"], "notes": c["notes"] or "",
             "done": weekly_done(conn, kid_id, c["id"], d)}
            for c in chores
            if c["id"] not in paused_ids and chore_assigned_to(conn, c, kid_id, ws)]


# --------------------------------------------------------------------------- #
# Scheduled chores (v1.3) — recurring on a weekday, with a countdown + overdue.
# --------------------------------------------------------------------------- #
def _completion_between(conn, kid_id, chore_id, lo_exclusive, hi_inclusive):
    row = conn.execute(
        "SELECT 1 FROM chore_completions WHERE kid_id=? AND chore_id=? "
        "AND completion_date > ? AND completion_date <= ? LIMIT 1",
        (kid_id, chore_id, d2s(lo_exclusive), d2s(hi_inclusive))).fetchone()
    return row is not None


def scheduled_state(conn, kid_id, chore, d):
    """Display state for a scheduled chore for one kid on date d.

    Returns a dict: state in {countdown, due_today, overdue, done, idle},
    days_until, and the due_label. Completion is occurrence-based (tied to the
    due date), so checking it off any time in the lead-up counts.
    """
    wd = chore["due_weekday"]
    lead = chore["reminder_lead_days"] or 0
    next_due = d + timedelta(days=(wd - d.weekday()) % 7)   # today if today is due
    prev_due = next_due - timedelta(days=7)
    label = chore["due_label"] or ""

    # A missed previous occurrence (its due day has passed, never done) is overdue
    # — but only if that occurrence fell on/after the chore was created, so a
    # brand-new scheduled chore doesn't immediately read as overdue.
    created = (chore["created_at"] or "")[:10]
    prev_after_creation = (not created) or (d2s(prev_due) >= created)
    if (prev_due < d and prev_after_creation
            and not _completion_between(conn, kid_id, chore["id"],
                                        prev_due - timedelta(days=7), d)):
        return {"state": "overdue", "days_until": 0, "due_label": label}

    days_until = (next_due - d).days
    done_now = _completion_between(conn, kid_id, chore["id"],
                                   next_due - timedelta(days=7), d)
    if done_now:
        done_today = _completion_between(conn, kid_id, chore["id"],
                                         d - timedelta(days=1), d)
        return {"state": "done" if done_today else "idle",
                "days_until": days_until, "due_label": label}
    if days_until == 0:
        return {"state": "due_today", "days_until": 0, "due_label": label}
    if days_until <= lead:
        return {"state": "countdown", "days_until": days_until, "due_label": label}
    return {"state": "idle", "days_until": days_until, "due_label": label}


def scheduled_for_kid(conn, kid_id, d):
    """Active scheduled chores assigned to this kid that should show today."""
    ws = week_start(d)
    paused_ids = paused_chore_ids_on(conn, d)
    out = []
    chores = conn.execute(
        "SELECT * FROM chores WHERE type='scheduled' AND active=1 ORDER BY id"
    ).fetchall()
    for ch in chores:
        if ch["id"] in paused_ids:
            continue
        if not chore_assigned_to(conn, ch, kid_id, ws):
            continue
        st = scheduled_state(conn, kid_id, ch, d)
        if st["state"] in ("countdown", "due_today", "overdue", "done"):
            out.append({"id": ch["id"], "name": ch["name"], "notes": ch["notes"] or "",
                        "done": st["state"] == "done", **st})
    return out


# --------------------------------------------------------------------------- #
# Pace (with divide-by-zero guard, v1.1 E1)
# --------------------------------------------------------------------------- #
def active_days_remaining(conn, ws, d):
    """Active days strictly after today through the end of the week."""
    return sum(1 for dd in week_dates(ws) if dd > d and is_active_day(conn, dd))


def pace(conn, target, current, ws, d):
    """Return (state, per_day_needed). state in {met, behind, no_days_left}."""
    if current >= target:
        return ("met", 0)
    remaining = active_days_remaining(conn, ws, d)
    if remaining <= 0:
        return ("no_days_left", None)  # Sunday / last active day: met-or-not only
    return ("behind", math.ceil((target - current) / remaining))


# --------------------------------------------------------------------------- #
# Weekly banner state
# --------------------------------------------------------------------------- #
def _on_pace(conn, target, current, ws, d):
    """On pace if you've met the share required by the START of today.

    Active days elapsed *excluding* today; so Monday (or the first active day)
    with nothing logged is still green — a full week is ahead (spec).
    """
    if current >= target:
        return True
    elapsed_excl_today = sum(1 for dd in week_dates(ws)
                             if dd < d and is_active_day(conn, dd))
    active_total = active_days_in_week(conn, ws)
    if active_total == 0:
        return True
    required = target * elapsed_excl_today / active_total
    return current >= required


def banner_state(conn, kid, d):
    """Compute the kid-page weekly banner. Returns a dict for the template."""
    if not in_program_window(conn, d):
        return {"state": "out_of_program"}
    if is_paused(conn, d):
        p = paused_period_on(conn, d)
        return {"state": "on_break", "label": p["label"]}

    ws = week_start(d)
    targets = prorated_targets(conn, kid, ws)
    acts = activities(conn, enabled_only=True)

    if not acts:
        # Chores-only mode: bonus tracks daily-checklist completion.
        required = required_checklist_days(conn, targets["active_days"])
        done, elapsed = checklist_days_this_week(conn, kid["id"], d)
        if done >= required:
            return {"state": "earned"}
        days_left = max(0, targets["active_days"] - elapsed)
        return {"state": "on_track" if done + days_left >= required else "at_risk"}

    all_met = on_pace = True
    for a in acts:
        cur = weekly_activity_total(conn, a["id"], kid["id"], ws)
        tgt = targets["by_activity"].get(a["id"], 0)
        if cur < tgt:
            all_met = False
        if not _on_pace(conn, tgt, cur, ws, d):
            on_pace = False
    if all_met:
        return {"state": "earned"}
    return {"state": "on_track" if on_pace else "at_risk"}


# --------------------------------------------------------------------------- #
# Rotating chore info line
# --------------------------------------------------------------------------- #
def rotating_chore_for_kid(conn, kid_id, ws):
    row = conn.execute(
        "SELECT c.name FROM rotating_chore_assignments r "
        "JOIN chores c ON c.id = r.chore_id "
        "WHERE r.kid_id=? AND r.week_start_date=? AND c.active=1 "
        "AND c.type = 'as_needed' LIMIT 1",   # other types show in their own sections
        (kid_id, d2s(ws))).fetchone()
    return row["name"] if row else None


def _next_kid(kids, kid_id):
    """Next kid in the ordered list after kid_id; wraps around for N kids."""
    for i, k in enumerate(kids):
        if k["id"] == kid_id:
            return kids[(i + 1) % len(kids)]["id"]
    return kids[0]["id"] if kids else kid_id


def ensure_rotation_for_week(conn, d):
    """Auto-advance rotating-chore assignments up to the current week.

    Backfills every week from the last week that has assignments through this
    one, swapping the two kids each week in alternating order. Idempotent: weeks
    that already have rows are skipped. Seed creates week 1, so normal operation
    just adds the current week on the first Monday load.
    """
    ws = week_start(d)
    rot_chores = [r["id"] for r in conn.execute(
        "SELECT id FROM chores WHERE is_rotating=1 AND active=1").fetchall()]
    kids = active_kids(conn)
    if not rot_chores or len(kids) < 2:
        return

    row = conn.execute(
        "SELECT MAX(week_start_date) AS m FROM rotating_chore_assignments "
        "WHERE week_start_date <= ?", (d2s(ws),)).fetchone()
    if row["m"] is None:
        # No prior assignments at all (shouldn't happen post-seed): seed default.
        for i, ch in enumerate(rot_chores):
            conn.execute(
                "INSERT OR IGNORE INTO rotating_chore_assignments "
                "(chore_id, kid_id, week_start_date, is_override) VALUES (?,?,?,0)",
                (ch, kids[i % len(kids)]["id"], d2s(ws)))
        conn.commit()
        return

    wk = s2d(row["m"]) + timedelta(days=7)
    while wk <= ws:
        prev = wk - timedelta(days=7)
        for ch in rot_chores:
            prow = conn.execute(
                "SELECT kid_id FROM rotating_chore_assignments "
                "WHERE chore_id=? AND week_start_date=?", (ch, d2s(prev))).fetchone()
            kid_id = _next_kid(kids, prow["kid_id"]) if prow else kids[0]["id"]
            conn.execute(
                "INSERT OR IGNORE INTO rotating_chore_assignments "
                "(chore_id, kid_id, week_start_date, is_override) VALUES (?,?,?,0)",
                (ch, kid_id, d2s(wk)))
        wk += timedelta(days=7)
    conn.commit()


def rotation_table(conn, d):
    """[{chore_id, chore, kid}] for the current week — drives the admin manager + kid line."""
    ws = week_start(d)
    rows = conn.execute(
        "SELECT c.id AS chore_id, c.name AS chore, k.name AS kid "
        "FROM rotating_chore_assignments r "
        "JOIN chores c ON c.id = r.chore_id JOIN kids k ON k.id = r.kid_id "
        "WHERE r.week_start_date=? AND c.active=1 ORDER BY c.id", (d2s(ws),)).fetchall()
    return [{"chore_id": r["chore_id"], "chore": r["chore"], "kid": r["kid"]}
            for r in rows]


def swap_rotation_this_week(conn, d):
    """Manual override: flip every rotating chore to the other kid for this week."""
    ws = week_start(d)
    kids = active_kids(conn)
    if len(kids) < 2:
        return
    for r in conn.execute(
            "SELECT id, kid_id FROM rotating_chore_assignments "
            "WHERE week_start_date=?", (d2s(ws),)).fetchall():
        conn.execute(
            "UPDATE rotating_chore_assignments SET kid_id=?, is_override=1 WHERE id=?",
            (_next_kid(kids, r["kid_id"]), r["id"]))
    conn.commit()


def swap_rotation_for_chore(conn, chore_id, d):
    """Manual override: flip a single rotating chore to the other kid for this week."""
    ws = week_start(d)
    kids = active_kids(conn)
    if len(kids) < 2:
        return
    row = conn.execute(
        "SELECT id, kid_id FROM rotating_chore_assignments "
        "WHERE chore_id=? AND week_start_date=?", (chore_id, d2s(ws))).fetchone()
    if row:
        conn.execute(
            "UPDATE rotating_chore_assignments SET kid_id=?, is_override=1 WHERE id=?",
            (_next_kid(kids, row["kid_id"]), row["id"]))
        conn.commit()


# --------------------------------------------------------------------------- #
# As-needed assignments for the kid today
# --------------------------------------------------------------------------- #
def as_needed_for_kid(conn, kid_id, d):
    """Assignments that are still pending or were completed today."""
    ds = d2s(d)
    return conn.execute(
        "SELECT a.id, a.chore_id, a.completed_at, c.name "
        "FROM as_needed_assignments a JOIN chores c ON c.id = a.chore_id "
        "WHERE a.kid_id=? AND c.active=1 "
        "AND (a.completed_at IS NULL OR substr(a.completed_at,1,10)=?) "
        "ORDER BY a.id", (kid_id, ds)).fetchall()


# --------------------------------------------------------------------------- #
# Weekly finalize (v1.1 B/C/D) — runs on ANY page load.
# --------------------------------------------------------------------------- #
def finalize_past_weeks(conn, d, env=None):
    """Finalize every completed, not-yet-stored week up to (not incl) this one.

    A week is finalized once its Sunday has passed in local time. Paused weeks
    (active_days == 0) are recorded but excluded from bonus/streak. Misses create
    a make-up_owed row for the following Monday (v1.1 C).
    """
    ensure_camp_credit(conn, d)  # camp totals must exist before we tally weeks
    start, end = program_window(conn)
    ws = week_start(start)
    cur_ws = week_start(d)
    kids = active_kids(conn)
    while ws < cur_ws and ws <= end:
        if week_end(ws) < d:  # fully in the past
            for kid in kids:
                _finalize_week_for_kid(conn, kid, ws)
        ws += timedelta(days=7)
    conn.commit()


def required_checklist_days(conn, active_days):
    """How many checklist days a chores-only week needs for the bonus.

    Setting `checklist_min_days` blank/0 means "every active day". A configured
    value is capped at active_days so a short (prorated) week stays achievable.
    """
    raw = (get_setting(conn, "checklist_min_days", "") or "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 0
    return active_days if n <= 0 else min(n, active_days)


def _bonus_earned(conn, kid, ws, targets, ck_done=0, ck_active=0):
    """True when the kid has met the week's bonus condition.

    With activities enabled, that's hitting every enabled activity's target. With
    no activities (chores-only mode), it's finishing the daily checklist on at
    least `required_checklist_days` of the week's active days.
    """
    acts = activities(conn, enabled_only=True)
    if not acts:
        return ck_done >= required_checklist_days(conn, ck_active)
    for a in acts:
        cur = weekly_activity_total(conn, a["id"], kid["id"], ws)
        if cur < targets["by_activity"].get(a["id"], 0):
            return False
    return True


def _finalize_week_for_kid(conn, kid, ws):
    exists = conn.execute(
        "SELECT 1 FROM weekly_results WHERE kid_id=? AND week_start_date=?",
        (kid["id"], d2s(ws))).fetchone()
    if exists:
        return

    targets = prorated_targets(conn, kid, ws)
    acts = activities(conn, enabled_only=True)
    sums = {a["id"]: weekly_activity_total(conn, a["id"], kid["id"], ws) for a in acts}

    # Legacy reading/outdoor columns are still written (rollback safety) when those
    # activities exist; the weekly_result_activities child rows are authoritative.
    r_act, o_act = activity_by_key(conn, "reading"), activity_by_key(conn, "outdoor")
    r_sum = sums.get(r_act["id"], 0) if r_act else 0
    o_sum = sums.get(o_act["id"], 0) if o_act else 0
    r_tgt = targets["by_activity"].get(r_act["id"], 0) if r_act else 0
    o_tgt = targets["by_activity"].get(o_act["id"], 0) if o_act else 0

    paused = targets["active_days"] == 0
    if paused:
        bonus_val = None
    else:
        ck_done, ck_active = checklist_days_in_week(conn, kid["id"], ws)
        bonus_val = 1 if _bonus_earned(conn, kid, ws, targets, ck_done, ck_active) else 0

    conn.execute(
        "INSERT OR IGNORE INTO weekly_results (kid_id, week_start_date, "
        "reading_minutes, outdoor_minutes, reading_target, outdoor_target, "
        "active_days, is_paused_week, bonus_earned, computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (kid["id"], d2s(ws), r_sum, o_sum, r_tgt, o_tgt,
         targets["active_days"], 1 if paused else 0, bonus_val, now_iso()))
    wr_id = conn.execute(
        "SELECT id FROM weekly_results WHERE kid_id=? AND week_start_date=?",
        (kid["id"], d2s(ws))).fetchone()["id"]
    for a in acts:
        conn.execute(
            "INSERT OR IGNORE INTO weekly_result_activities "
            "(weekly_result_id, activity_id, amount, target) VALUES (?,?,?,?)",
            (wr_id, a["id"], sums[a["id"]], 0 if paused else targets["by_activity"].get(a["id"], 0)))

    if bonus_val == 0:
        # Deficit is recoverable next Monday (v1.1 C).
        next_monday = ws + timedelta(days=7)
        conn.execute(
            "INSERT OR IGNORE INTO makeup_owed (kid_id, for_week_start, "
            "reading_deficit, outdoor_deficit, satisfied_at) VALUES (?,?,?,?,NULL)",
            (kid["id"], d2s(next_monday), max(0, r_tgt - r_sum), max(0, o_tgt - o_sum)))
        m_id = conn.execute(
            "SELECT id FROM makeup_owed WHERE kid_id=? AND for_week_start=?",
            (kid["id"], d2s(next_monday))).fetchone()["id"]
        for a in acts:
            deficit = max(0, targets["by_activity"].get(a["id"], 0) - sums[a["id"]])
            if deficit:
                conn.execute(
                    "INSERT OR IGNORE INTO makeup_deficits (makeup_id, activity_id, deficit) "
                    "VALUES (?,?,?)", (m_id, a["id"], deficit))


# --------------------------------------------------------------------------- #
# Make-up Monday (v1.1 C)
# --------------------------------------------------------------------------- #
def open_makeup(conn, kid_id, d):
    """Unsatisfied make-up row for the current week, or None."""
    return conn.execute(
        "SELECT * FROM makeup_owed WHERE kid_id=? AND for_week_start=? "
        "AND satisfied_at IS NULL", (kid_id, d2s(week_start(d)))).fetchone()


def check_makeup_reinstatement(conn, kid_id, d):
    """If today (Monday) satisfies the make-up, mark it and return the row.

    Returns the satisfied row when newly reinstated (so the caller can fire a
    Pushover), else None. Only valid on Monday — a same-day second chance.
    """
    if d.weekday() != 0:  # Monday only
        return None
    row = open_makeup(conn, kid_id, d)
    if row is None:
        return None
    done, _ = checklist_status(conn, kid_id, d)
    if not done:
        return None
    for md in conn.execute(
            "SELECT activity_id, deficit FROM makeup_deficits WHERE makeup_id=?",
            (row["id"],)).fetchall():
        if today_activity_total(conn, md["activity_id"], kid_id, d) < md["deficit"]:
            return None
    conn.execute("UPDATE makeup_owed SET satisfied_at=? WHERE id=?",
                 (now_iso(), row["id"]))
    conn.commit()
    return row


def makeup_banner(conn, kid_id, d):
    """Render data for the Monday make-up banner, or None if not applicable."""
    if d.weekday() != 0:
        return None
    row = open_makeup(conn, kid_id, d)
    if row is None:
        return None
    items = []
    for md in conn.execute(
            "SELECT activity_id, deficit FROM makeup_deficits WHERE makeup_id=?",
            (row["id"],)).fetchall():
        a = activity_by_id(conn, md["activity_id"])
        if a is None:
            continue
        left = max(0, md["deficit"] - today_activity_total(conn, md["activity_id"], kid_id, d))
        if left > 0:
            items.append({"label": a["label"], "unit": a["unit"], "left": left})
    return {"items": items}


# --------------------------------------------------------------------------- #
# Summer scoreboard (v1.1 D)
# --------------------------------------------------------------------------- #
def kid_bonus_history(conn, kid_id, n=5):
    """Last n finalized non-paused weeks for the kid's history section, each with a
    per-activity breakdown drawn from weekly_result_activities."""
    rows = conn.execute(
        "SELECT id, week_start_date, bonus_earned FROM weekly_results "
        "WHERE kid_id=? AND is_paused_week=0 "
        "ORDER BY week_start_date DESC LIMIT ?",
        (kid_id, n)).fetchall()
    out = []
    for r in rows:
        acts = conn.execute(
            "SELECT wra.amount, wra.target, a.label, a.unit "
            "FROM weekly_result_activities wra JOIN activities a ON a.id = wra.activity_id "
            "WHERE wra.weekly_result_id=? ORDER BY a.sort_order, a.id", (r["id"],)).fetchall()
        out.append({"week_start_date": r["week_start_date"],
                    "bonus_earned": r["bonus_earned"],
                    "activities": [dict(x) for x in acts]})
    return out


def scoreboard(conn, kid_id):
    """(stars, streak). Paused weeks are skipped — neither earn nor break."""
    rows = conn.execute(
        "SELECT bonus_earned FROM weekly_results "
        "WHERE kid_id=? AND is_paused_week=0 ORDER BY week_start_date",
        (kid_id,)).fetchall()
    stars = sum(1 for r in rows if r["bonus_earned"] == 1)
    streak = 0
    for r in reversed(rows):
        if r["bonus_earned"] == 1:
            streak += 1
        else:
            break
    return stars, streak


def last_week_bonus(conn, kid_id, d):
    """True/False for the most recent finalized non-paused week, or None."""
    row = conn.execute(
        "SELECT bonus_earned FROM weekly_results "
        "WHERE kid_id=? AND is_paused_week=0 AND week_start_date < ? "
        "ORDER BY week_start_date DESC LIMIT 1",
        (kid_id, d2s(week_start(d)))).fetchone()
    if row is None:
        return None  # before any week has finalized -> render as "—", never ✗
    return row["bonus_earned"] == 1
