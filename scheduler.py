"""Background scheduler — the only part of the app that acts on its own clock.

Two time-of-day Pushover notifications that must fire whether or not anyone has
a page open:
  * mid-morning reminder (default 10:00) if a kid's checklist isn't done
  * Sunday 7pm household week summary

A single minute-interval job checks both; `notifications_sent` (UNIQUE on
kid_id+date+type) makes every send once-per-day idempotent, so polling is safe.
Runs in the single Flask process — never start more than one.
"""
import logging
import os
from datetime import time

import requests

import db
import logic
import notify

log = logging.getLogger("chore.scheduler")

# Household-level notifications (the weekly summary) use this sentinel kid_id so
# the notifications_sent UNIQUE constraint dedups them (SQLite lets NULLs repeat).
HOUSEHOLD = 0


def _parse_hhmm(s):
    try:
        h, m = (s or "").split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _hr(mins):
    """Minutes -> compact hours string: 300 -> '5', 210 -> '3.5'."""
    return ("%.1f" % (mins / 60)).rstrip("0").rstrip(".")


def build_summary(conn, d):
    """Sunday-summary message for the week ending on d."""
    ws = logic.week_start(d)
    parts = []
    for kid in logic.active_kids(conn):
        targets = logic.prorated_targets(conn, kid, ws)
        r = logic.weekly_reading(conn, kid["id"], ws)
        o = logic.weekly_outdoor(conn, kid["id"], ws)
        parts.append("%s: Reading %d/%d min %s, Outdoor %s/%s hr %s" % (
            kid["name"], r, targets["reading"], "✓" if r >= targets["reading"] else "✗",
            _hr(o), _hr(targets["outdoor"]), "✓" if o >= targets["outdoor"] else "✗"))
    return " ".join(parts)


def maybe_morning_reminder(conn, now, d):
    """Fire the mid-morning reminder for any kid who hasn't finished today."""
    if logic.is_paused(conn, d):
        return                                  # no reminders on paused vacation days
    rt = logic.get_setting(conn, "reminder_time", "10:00")
    if rt == "off":
        return
    t = _parse_hhmm(rt)
    if t is None or now.time() < t:
        return
    for kid in logic.active_kids(conn):
        if not logic.assigned_daily_chores(conn, kid["id"], d):
            continue                            # this kid has no daily chores -> skip
        done, _ = logic.checklist_status(conn, kid["id"], d)
        if done:
            continue
        notify.send_once(conn, kid["id"], "morning_reminder", "Chore Tracker 🔔",
                         "%s hasn't finished his checklist yet." % kid["name"], d)


def maybe_sunday_summary(conn, now, d):
    """Fire the household week summary on Sunday at/after 7pm (once)."""
    if d.weekday() != 6 or now.hour < 19:       # Sunday == 6
        return
    if logic.is_paused(conn, d):                # paused week -> no summary
        return
    notify.send_once(conn, HOUSEHOLD, "weekly_summary", "Chore Tracker — Week Summary",
                     build_summary(conn, d), d)


def maybe_scheduled_due(conn, now, d):
    """On a scheduled chore's due evening, Pushover once if it isn't done yet."""
    if now.hour < 17:                            # give them the day; nudge in the evening
        return
    ws = logic.week_start(d)
    for ch in conn.execute(
            "SELECT * FROM chores WHERE type='scheduled' AND active=1").fetchall():
        if ch["due_weekday"] != d.weekday():
            continue
        for kid in logic.active_kids(conn):
            if not logic.chore_assigned_to(conn, ch, kid["id"], ws):
                continue
            st = logic.scheduled_state(conn, kid["id"], ch, d)
            if st["state"] in ("due_today", "overdue"):
                label = " (%s)" % ch["due_label"] if ch["due_label"] else ""
                notify.send_once(
                    conn, kid["id"], "scheduled_due:%d" % ch["id"], "Chore Tracker 🔔",
                    "Reminder: %s — %s%s is due tonight." % (kid["name"], ch["name"], label),
                    d)


def heartbeat(env=None):
    """Ping an Uptime Kuma push monitor if UPTIME_KUMA_PUSH_URL is set.

    Because this runs from the scheduler tick, a received heartbeat proves the
    background job is alive — not just that the web server answers. Fire-and-
    forget: a failure is logged, never raised.
    """
    env = env if env is not None else os.environ
    url = (env.get("UPTIME_KUMA_PUSH_URL") or "").strip()
    if not url:
        return False
    # If the configured URL already has a query string (Kuma's push URL ships
    # with ?status=up&msg=OK&ping=), use it as-is. Appending our own would
    # duplicate `status`, which Kuma parses as a list and reads as DOWN.
    if "?" not in url:
        url = url + "?status=up&msg=OK"
    try:
        requests.get(url, timeout=5)
        return True
    except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
        log.warning("Uptime Kuma heartbeat failed: %s", exc)
        return False


def run_tick(env=None):
    """One scheduler tick: fire anything due, then heartbeat. Own connection."""
    conn = db.connect()
    try:
        now = logic.now(env)
        d = now.date()
        if logic.in_program_window(conn, d):    # off-season: no reminders/summaries
            maybe_morning_reminder(conn, now, d)
            maybe_scheduled_due(conn, now, d)
            maybe_sunday_summary(conn, now, d)
    finally:
        conn.close()
    heartbeat(env)                              # always — proves the tick ran


def _safe_tick(env):
    try:
        run_tick(env)
    except Exception as exc:                     # never let a tick crash the scheduler
        log.error("scheduler tick failed: %s", exc)


def start(env=None):
    """Start the minute-interval background scheduler. Call once, in the server."""
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(timezone=logic.get_tz(env))
    sched.add_job(lambda: _safe_tick(env), "interval", minutes=1,
                  id="tick", max_instances=1, coalesce=True)
    sched.start()
    log.info("Scheduler started (minute interval).")
    return sched
