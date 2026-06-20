"""Chore Tracker — Flask entrypoint.

Single-process (no gunicorn workers) so background scheduling fires once.
Kid pages and APIs only for this milestone; /status and /admin come next.
"""
import os
import re
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, g, jsonify, render_template, request, abort,
                   session, redirect)

import db
import logic
import notify
import scheduler

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Ensure the data dir exists, then create/seed the DB once at startup.
os.makedirs(db.DATA_DIR, exist_ok=True)
with db.connect() as _c:
    db.init_db(_c, os.environ)
    # Stable secret so admin sessions survive restarts (seeded once on first run).
    app.secret_key = logic.get_setting(_c, "flask_secret") or os.urandom(32).hex()
app.permanent_session_lifetime = timedelta(days=7)


# --------------------------------------------------------------------------- #
# Per-request DB connection
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def effective_today():
    """Local 'today', with an optional ?today=/JSON override when CHORE_DEBUG=1.

    The override is for manually exercising in-program-window dates before the
    season starts; it is inert unless CHORE_DEBUG is enabled.
    """
    if os.environ.get("CHORE_DEBUG", "0") in ("1", "true", "True"):
        override = request.args.get("today")
        if not override and request.is_json:
            override = (request.get_json(silent=True) or {}).get("today")
        if not override and request.form:
            override = request.form.get("today")
        if override:
            try:
                return logic.s2d(override)
            except ValueError:
                pass
    return logic.today()


@app.after_request
def _no_store(resp):
    """iOS Safari caches aggressively; force fresh totals on kid/status pages."""
    p = request.path
    if (p == "/status" or p == "/healthz" or p.rstrip("/") in _KID_PATHS
            or p.startswith("/api/") or p.startswith("/admin")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def require_admin(f):
    """Guard for /admin* routes — redirect to the login page when not signed in."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper


_KID_PATHS = set()  # filled after we know the slugs (below)


# --------------------------------------------------------------------------- #
# Shared view-model builders (reused by the page render and the JSON APIs)
# --------------------------------------------------------------------------- #
def _fmt_hm(mins):
    h, m = divmod(int(mins), 60)
    if h and m:
        return "%d hr %d min" % (h, m)
    if h:
        return "%d hr" % h
    return "%d min" % m


def _fmt_time(iso):
    """ISO timestamp -> '8:15 AM' (local, as stored). None passes through."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return "%d:%02d %s" % (dt.hour % 12 or 12, dt.minute,
                           "AM" if dt.hour < 12 else "PM")


def _pct(value, target):
    return min(100, int(value / target * 100)) if target > 0 else 0


def log_section(conn, kid, kind, d):
    """View-model for a reading/outdoor log section, used by render + API."""
    table = "reading_logs" if kind == "reading" else "outdoor_logs"
    ws = logic.week_start(d)
    targets = logic.prorated_targets(conn, kid, ws)
    target = targets["reading"] if kind == "reading" else targets["outdoor"]
    weekly = (logic.weekly_reading if kind == "reading" else logic.weekly_outdoor)(
        conn, kid["id"], ws)
    today_total = logic.today_minutes(conn, table, kid["id"], d)
    entries = [{"id": r["id"], "minutes": r["minutes"]}
               for r in logic.today_entries(conn, table, kid["id"], d)]

    in_program = logic.in_program_window(conn, d) and not logic.is_paused(conn, d)
    pace_state, pace_needed = (logic.pace(conn, target, weekly, ws, d)
                               if in_program else ("inactive", None))
    return {
        "kind": kind,
        "weekly": weekly,
        "target": target,
        "today_total": today_total,
        "entries": entries,
        "pace_state": pace_state,
        "pace_needed": pace_needed,
        "met": weekly >= target and target > 0,
        "weekly_hm": _fmt_hm(weekly),
        "target_hm": _fmt_hm(target),
    }


def kid_view(conn, kid, d):
    """Assemble the full kid-page context."""
    logic.finalize_past_weeks(conn, d)            # finalize any past weeks first
    logic.ensure_camp_credit(conn, d)             # idempotent camp outdoor credit
    logic.ensure_rotation_for_week(conn, d)       # auto-advance rotating chores

    banner = logic.banner_state(conn, kid, d)
    ws = logic.week_start(d)

    daily = logic.assigned_daily_chores(conn, kid["id"], d)
    done_map = logic.completed_chore_ids(conn, kid["id"], d)
    daily_rows = [{"id": c["id"], "name": c["name"], "done": c["id"] in done_map,
                   "kind": c["type"],
                   "overdue": (c["type"] == "alternate_daily"
                               and logic.alt_daily_is_overdue(c, d))}
                  for c in daily]
    checklist_done, completed_at = logic.checklist_status(conn, kid["id"], d)

    weekly = logic.weekly_chores_for_kid(conn, kid["id"], d)
    scheduled = logic.scheduled_for_kid(conn, kid["id"], d)

    as_needed = [{"id": a["id"], "name": a["name"],
                  "done": a["completed_at"] is not None}
                 for a in logic.as_needed_for_kid(conn, kid["id"], d)]

    stars, streak = logic.scoreboard(conn, kid["id"])
    reward = logic.get_setting(conn, "scoreboard_reward_text", "") or ""

    debug = os.environ.get("CHORE_DEBUG", "0") in ("1", "true", "True")
    return {
        "kid": kid,
        "debug": debug,
        "render_today": logic.d2s(d),
        "today_long": d.strftime("%A, %B ") + str(d.day),
        "banner": banner,
        "rotation": logic.rotating_chore_for_kid(conn, kid["id"], ws),
        "daily": daily_rows,
        "checklist_done": checklist_done,
        "completed_at": completed_at,
        "weekly": weekly,
        "scheduled": scheduled,
        "as_needed": as_needed,
        "reading": log_section(conn, kid, "reading", d),
        "outdoor": log_section(conn, kid, "outdoor", d),
        "stars": stars,
        "streak": streak,
        "reward": reward,
        "makeup": logic.makeup_banner(conn, kid["id"], d),
        "last_week_bonus": logic.last_week_bonus(conn, kid["id"], d),
        "reading_quick": [15, 25, 30, 60],
        "outdoor_quick": [15, 30, 60, 90],
    }


def status_view(conn, d):
    """Read-only at-a-glance view of both kids for the parent /status page."""
    logic.finalize_past_weeks(conn, d)
    logic.ensure_camp_credit(conn, d)
    logic.ensure_rotation_for_week(conn, d)

    ws = logic.week_start(d)
    in_program = logic.in_program_window(conn, d)
    cards = []
    for kid in logic.active_kids(conn):
        targets = logic.prorated_targets(conn, kid, ws)
        r = logic.weekly_reading(conn, kid["id"], ws)
        o = logic.weekly_outdoor(conn, kid["id"], ws)
        done, completed_at = logic.checklist_status(conn, kid["id"], d)
        as_needed = [{"name": a["name"],
                      "done": a["completed_at"] is not None,
                      "time": _fmt_time(a["completed_at"])}
                     for a in logic.as_needed_for_kid(conn, kid["id"], d)]
        weekly_done = [{"name": w["name"], "done": w["done"]}
                       for w in logic.weekly_chores_for_kid(conn, kid["id"], d)]
        cards.append({
            "name": kid["name"],
            "checklist_done": done,
            "completed_time": _fmt_time(completed_at),
            "as_needed": as_needed,
            "weekly": weekly_done,
            "reading": {"weekly": r, "target": targets["reading"],
                        "pct": _pct(r, targets["reading"])},
            "outdoor": {"weekly_hm": _fmt_hm(o), "target_hm": _fmt_hm(targets["outdoor"]),
                        "pct": _pct(o, targets["outdoor"])},
            "on_break": logic.is_paused(conn, d),
            "last_week_bonus": logic.last_week_bonus(conn, kid["id"], d),
        })
    return {
        "today_long": d.strftime("%A, %B ") + str(d.day),
        "in_program": in_program,
        "cards": cards,
    }


def admin_view(conn, d):
    """Read-only dashboard data: today's status + this week's progress per kid."""
    logic.finalize_past_weeks(conn, d)
    logic.ensure_camp_credit(conn, d)
    logic.ensure_rotation_for_week(conn, d)

    ws = logic.week_start(d)
    cards = []
    for kid in logic.active_kids(conn):
        done, completed_at = logic.checklist_status(conn, kid["id"], d)
        comp_days, elapsed_days = logic.checklist_days_this_week(conn, kid["id"], d)
        targets = logic.prorated_targets(conn, kid, ws)
        done_ids = logic.completed_chore_ids(conn, kid["id"], d)
        assigned_daily = logic.assigned_daily_chores(conn, kid["id"], d)
        cards.append({
            "id": kid["id"],
            "name": kid["name"],
            "slug": kid["url_slug"],
            "checklist_done": done,
            "completed_time": _fmt_time(completed_at),
            "on_break": logic.is_paused(conn, d),
            "as_needed": [{"id": a["id"], "name": a["name"],
                           "done": a["completed_at"] is not None,
                           "time": _fmt_time(a["completed_at"])}
                          for a in logic.as_needed_for_kid(conn, kid["id"], d)],
            "checklist_days": comp_days,
            "checklist_elapsed": elapsed_days,
            "reading": log_section(conn, kid, "reading", d),
            "outdoor": log_section(conn, kid, "outdoor", d),
            "last_week_bonus": logic.last_week_bonus(conn, kid["id"], d),
            "prorated": targets["active_days"] < 7,
            "active_days": targets["active_days"],
            "reading_target": targets["reading"],
            "outdoor_target": targets["outdoor"],
            "daily_incomplete": [{"id": c["id"], "name": c["name"]}
                                 for c in assigned_daily if c["id"] not in done_ids],
        })

    # Chore list with per-chore assignment state for the table's assign toggles.
    assigned_map = {}
    for r in conn.execute("SELECT chore_id, kid_id FROM weekly_assignments").fetchall():
        assigned_map.setdefault(r["chore_id"], set()).add(r["kid_id"])
    chore_rows = []
    for r in conn.execute(
            "SELECT id, name, type, active, is_rotating, due_weekday, "
            "reminder_lead_days, due_label, alt_day_parity FROM chores WHERE deleted=0 "
            "ORDER BY type, id").fetchall():
        chore_rows.append(dict(r, assigned_ids=sorted(assigned_map.get(r["id"], set()))))

    as_needed_chores = conn.execute(
        "SELECT id, name FROM chores WHERE type='as_needed' AND active=1 AND deleted=0 "
        "ORDER BY id").fetchall()
    debug = os.environ.get("CHORE_DEBUG", "0") in ("1", "true", "True")
    return {
        "today_long": d.strftime("%A, %B ") + str(d.day),
        "in_program": logic.in_program_window(conn, d),
        "cards": cards,
        "all_chores": chore_rows,
        "kids_list": [{"id": k["id"], "name": k["name"]} for k in logic.active_kids(conn)],
        "as_needed_chores": [dict(r) for r in as_needed_chores],
        "rotation": logic.rotation_table(conn, d),
        "debug_today": logic.d2s(d) if debug else None,
    }


def _int_or_none(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _valid_date(s):
    try:
        logic.s2d(s)
        return True
    except (ValueError, TypeError):
        return False


def _valid_time(s):
    return bool(re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", s or ""))


def settings_view(conn):
    kids = [{"id": k["id"], "name": k["name"],
             "reading_target": k["reading_target_minutes"],
             "outdoor_target": k["outdoor_target_minutes"]}
            for k in logic.active_kids(conn)]
    g = lambda key, default="": logic.get_setting(conn, key, default)
    return {
        "kids": kids,
        "reminder_time": g("reminder_time", "10:00"),
        "program_start": g("program_start_date", ""),
        "program_end": g("program_end_date", ""),
        "reward": g("scoreboard_reward_text", ""),
        "pushover_app": g("pushover_app_token", ""),
        "pushover_user": g("pushover_user_key", ""),
        "specials": [dict(r) for r in logic.special_periods(conn)],
        "saved": request.args.get("saved"),
        "pwerror": request.args.get("pwerror"),
    }


def history_view(conn, d):
    """Week-by-week breakdown (newest first) + per-kid streak/stars summary."""
    logic.finalize_past_weeks(conn, d)
    kids = logic.active_kids(conn)
    daily = logic.active_daily_chores(conn)

    summary = []
    for kid in kids:
        stars, streak = logic.scoreboard(conn, kid["id"])
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM weekly_results WHERE kid_id=? AND is_paused_week=0",
            (kid["id"],)).fetchone()["n"]
        summary.append({"name": kid["name"], "stars": stars, "total": total,
                        "streak": streak})

    weeks = []
    for wr in conn.execute("SELECT DISTINCT week_start_date FROM weekly_results "
                           "ORDER BY week_start_date DESC").fetchall():
        ws = logic.s2d(wr["week_start_date"])
        we = logic.week_end(ws)
        kcards = []
        for kid in kids:
            res = conn.execute(
                "SELECT * FROM weekly_results WHERE kid_id=? AND week_start_date=?",
                (kid["id"], wr["week_start_date"])).fetchone()
            if res is None:
                continue
            comp, active = logic.checklist_days_in_week(conn, kid["id"], ws)
            breakdown = []
            for ch in daily:
                cnt = conn.execute(
                    "SELECT COUNT(*) AS n FROM chore_completions WHERE kid_id=? "
                    "AND chore_id=? AND completion_date>=? AND completion_date<=?",
                    (kid["id"], ch["id"], logic.d2s(ws), logic.d2s(we))).fetchone()["n"]
                breakdown.append({"name": ch["name"], "count": cnt})
            kcards.append({
                "name": kid["name"],
                "paused": res["is_paused_week"] == 1,
                "bonus": res["bonus_earned"],
                "reading": res["reading_minutes"], "reading_target": res["reading_target"],
                "reading_met": res["reading_minutes"] >= res["reading_target"],
                "outdoor_hm": _fmt_hm(res["outdoor_minutes"]),
                "outdoor_target_hm": _fmt_hm(res["outdoor_target"]),
                "outdoor_met": res["outdoor_minutes"] >= res["outdoor_target"],
                "active_days": res["active_days"],
                "checklist_days": comp, "checklist_active": active,
                "breakdown": breakdown,
            })
        weeks.append({
            "label": "%s – %s" % (ws.strftime("%b ") + str(ws.day),
                                  we.strftime("%b ") + str(we.day)),
            "kids": kcards,
            "prorated": any((not k["paused"]) and k["active_days"] < 7 for k in kcards),
        })
    return {"summary": summary, "weeks": weeks}


def logs_view(conn, d):
    """Reading/outdoor entries for the current week, per kid, for admin editing."""
    ws = logic.week_start(d)
    we = logic.week_end(ws)
    kids = []
    for kid in logic.active_kids(conn):
        rows = {}
        for kind, table in (("reading", "reading_logs"), ("outdoor", "outdoor_logs")):
            rows[kind] = [dict(r) for r in conn.execute(
                "SELECT id, log_date, minutes, source FROM %s "
                "WHERE kid_id=? AND log_date >= ? AND log_date <= ? "
                "ORDER BY log_date, id" % table,
                (kid["id"], logic.d2s(ws), logic.d2s(we))).fetchall()]
        kids.append({"name": kid["name"], "reading": rows["reading"],
                     "outdoor": rows["outdoor"]})
    debug = os.environ.get("CHORE_DEBUG", "0") in ("1", "true", "True")
    return {
        "week_label": "%s – %s" % (ws.strftime("%b ") + str(ws.day),
                                   we.strftime("%b ") + str(we.day)),
        "kids": kids,
        "debug_today": logic.d2s(d) if debug else None,
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return ("Chore Tracker. Kid pages: /andrew, /daniel. "
            "Parent pages coming soon."), 200


@app.route("/healthz")
def healthz():
    """Lightweight health check for Uptime Kuma — verifies the DB is reachable.

    Public (no auth) and exposes nothing sensitive. 200 = healthy, 503 = down.
    """
    try:
        get_db().execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001 - any DB failure means unhealthy
        return jsonify({"status": "error"}), 503
    return jsonify({"status": "ok"}), 200


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    conn = get_db()
    error = None
    if request.method == "POST":
        stored = logic.get_setting(conn, "admin_password_hash", "")
        if db.verify_password(stored, request.form.get("password", "")):
            session.permanent = True
            session["admin"] = True
            return redirect("/admin")
        error = "Incorrect password."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")


@app.route("/admin")
@require_admin
def admin_dashboard():
    conn = get_db()
    d = effective_today()
    return render_template("admin.html", **admin_view(conn, d))


def _admin_redirect():
    """Redirect back to the dashboard, preserving the debug ?today if present."""
    today = request.form.get("today")
    return redirect("/admin?today=" + today if today else "/admin")


def _schedule_fields(ctype):
    """Parse the scheduled-chore fields from the form; returns
    (due_weekday, lead_days, due_label). Rotation is set via the table toggle."""
    if ctype != "scheduled":
        return (None, None, None)
    return (
        _int_or_none(request.form.get("due_weekday")),
        _int_or_none(request.form.get("reminder_lead_days")) or 0,
        (request.form.get("due_label") or "").strip(),
    )


def _alt_daily_fields(ctype):
    """Parse alt_day_parity (0=even, 1=odd) for alternate_daily chores."""
    if ctype != "alternate_daily":
        return None
    val = request.form.get("alt_day_parity", "0")
    return 1 if val == "1" else 0


@app.route("/admin/chore/add", methods=["POST"])
@require_admin
def admin_chore_add():
    conn = get_db()
    name = (request.form.get("name") or "").strip()
    ctype = request.form.get("type")
    if name and ctype in ("daily", "weekly", "as_needed", "scheduled", "alternate_daily"):
        wd, lead, label = _schedule_fields(ctype)
        parity = _alt_daily_fields(ctype)
        conn.execute(
            "INSERT INTO chores (name, type, is_rotating, active, deleted, "
            "created_at, due_weekday, reminder_lead_days, due_label, alt_day_parity) "
            "VALUES (?,?,0,1,0,?,?,?,?,?)",
            (name, ctype, logic.now_iso(), wd, lead, label, parity))
        conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/edit", methods=["POST"])
@require_admin
def admin_chore_edit():
    """Rename a chore and/or change its type."""
    conn = get_db()
    name = (request.form.get("name") or "").strip()
    ctype = request.form.get("type")
    chore_id = request.form.get("chore_id")
    if name and ctype in ("daily", "weekly", "as_needed", "scheduled", "alternate_daily"):
        # is_rotating is owned by the chores-table Rotate toggle, never the form.
        cur = conn.execute("SELECT is_rotating FROM chores WHERE id=?",
                           (chore_id,)).fetchone()
        rot = cur["is_rotating"] if cur else 0
        wd, lead, label = _schedule_fields(ctype)
        parity = _alt_daily_fields(ctype)
        conn.execute(
            "UPDATE chores SET name=?, type=?, is_rotating=?, due_weekday=?, "
            "reminder_lead_days=?, due_label=?, alt_day_parity=? WHERE id=? AND deleted=0",
            (name, ctype, rot, wd, lead, label, parity, chore_id))
        conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/toggle", methods=["POST"])
@require_admin
def admin_chore_toggle():
    conn = get_db()
    conn.execute("UPDATE chores SET active = 1 - active WHERE id=? AND deleted=0",
                 (request.form.get("chore_id"),))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/delete", methods=["POST"])
@require_admin
def admin_chore_delete():
    conn = get_db()
    conn.execute("UPDATE chores SET deleted=1, active=0 WHERE id=?",
                 (request.form.get("chore_id"),))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/checklist/override", methods=["POST"])
@require_admin
def admin_checklist_override():
    """Mark a daily chore done for a kid today, flagged parent_verified."""
    conn = get_db()
    d = effective_today()
    kid_id = request.form.get("kid_id")
    chore_id = request.form.get("chore_id")
    chore = conn.execute(
        "SELECT 1 FROM chores WHERE id=? AND type IN ('daily', 'alternate_daily') AND active=1",
        (chore_id,)).fetchone()
    if chore:
        conn.execute(
            "INSERT OR IGNORE INTO chore_completions (kid_id, chore_id, "
            "completion_date, completed_at, parent_verified) VALUES (?,?,?,?,1)",
            (kid_id, chore_id, logic.d2s(d), logic.now_iso()))
        conn.commit()
    return _admin_redirect()


@app.route("/admin/assign", methods=["POST"])
@require_admin
def admin_assign():
    """Assign an as-needed chore to a kid (skip if already pending)."""
    conn = get_db()
    kid_id = request.form.get("kid_id")
    chore_id = request.form.get("chore_id")
    chore = conn.execute("SELECT 1 FROM chores WHERE id=? AND type='as_needed' AND active=1",
                         (chore_id,)).fetchone()
    pending = conn.execute(
        "SELECT 1 FROM as_needed_assignments WHERE kid_id=? AND chore_id=? "
        "AND completed_at IS NULL", (kid_id, chore_id)).fetchone()
    if chore and not pending:
        conn.execute(
            "INSERT INTO as_needed_assignments (kid_id, chore_id, assigned_at, "
            "completed_at) VALUES (?,?,?,NULL)", (kid_id, chore_id, logic.now_iso()))
        conn.commit()
    return _admin_redirect()


@app.route("/admin/assign/complete", methods=["POST"])
@require_admin
def admin_assign_complete():
    conn = get_db()
    conn.execute(
        "UPDATE as_needed_assignments SET completed_at=? WHERE id=? AND completed_at IS NULL",
        (logic.now_iso(), request.form.get("assignment_id")))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/assign", methods=["POST"])
@require_admin
def admin_chore_assign():
    """Assign a recurring chore (daily/weekly/scheduled, non-rotating) to a kid."""
    conn = get_db()
    kid_id = request.form.get("kid_id")
    chore_id = request.form.get("chore_id")
    chore = conn.execute(
        "SELECT 1 FROM chores WHERE id=? "
        "AND type IN ('daily','weekly','scheduled','alternate_daily') "
        "AND is_rotating=0 AND active=1", (chore_id,)).fetchone()
    if chore:
        conn.execute(
            "INSERT OR IGNORE INTO weekly_assignments (chore_id, kid_id, created_at) "
            "VALUES (?,?,?)", (chore_id, kid_id, logic.now_iso()))
        conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/unassign", methods=["POST"])
@require_admin
def admin_chore_unassign():
    conn = get_db()
    conn.execute("DELETE FROM weekly_assignments WHERE chore_id=? AND kid_id=?",
                 (request.form.get("chore_id"), request.form.get("kid_id")))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/set-type", methods=["POST"])
@require_admin
def admin_chore_set_type():
    """Change just the type of a chore; preserves name and existing config fields."""
    conn = get_db()
    chore_id = request.form.get("chore_id")
    ctype = request.form.get("type")
    if ctype not in ("daily", "weekly", "as_needed", "scheduled", "alternate_daily"):
        return _admin_redirect()
    row = conn.execute(
        "SELECT due_weekday, reminder_lead_days, due_label, alt_day_parity "
        "FROM chores WHERE id=? AND deleted=0", (chore_id,)).fetchone()
    if row is None:
        return _admin_redirect()
    if ctype == "scheduled":
        wd = row["due_weekday"] if row["due_weekday"] is not None else 0
        lead = row["reminder_lead_days"] if row["reminder_lead_days"] is not None else 5
        label = row["due_label"] or ""
    else:
        wd, lead, label = None, None, None
    parity = (row["alt_day_parity"] if row["alt_day_parity"] is not None else 0) \
             if ctype == "alternate_daily" else None
    conn.execute(
        "UPDATE chores SET type=?, due_weekday=?, reminder_lead_days=?, due_label=?, "
        "alt_day_parity=? WHERE id=? AND deleted=0",
        (ctype, wd, lead, label, parity, chore_id))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/set-who", methods=["POST"])
@require_admin
def admin_chore_set_who():
    """Single-select assignment: none / both / kid_<id> / rotate_<id>."""
    conn = get_db()
    chore_id = request.form.get("chore_id")
    mode = request.form.get("mode", "none")
    row = conn.execute("SELECT is_rotating FROM chores WHERE id=? AND deleted=0",
                       (chore_id,)).fetchone()
    if row is None or mode == "rotating":
        return _admin_redirect()
    kids = logic.active_kids(conn)
    valid_kid_ids = {k["id"] for k in kids}
    # Clear current state
    if row["is_rotating"]:
        conn.execute("UPDATE chores SET is_rotating=0 WHERE id=?", (chore_id,))
        conn.execute("DELETE FROM rotating_chore_assignments WHERE chore_id=?", (chore_id,))
    conn.execute("DELETE FROM weekly_assignments WHERE chore_id=?", (chore_id,))
    if mode == "both":
        for k in kids:
            conn.execute(
                "INSERT OR IGNORE INTO weekly_assignments (chore_id, kid_id, created_at) "
                "VALUES (?,?,?)", (chore_id, k["id"], logic.now_iso()))
    elif mode.startswith("kid_"):
        kid_id = _int_or_none(mode[4:])
        if kid_id in valid_kid_ids:
            conn.execute(
                "INSERT OR IGNORE INTO weekly_assignments (chore_id, kid_id, created_at) "
                "VALUES (?,?,?)", (chore_id, kid_id, logic.now_iso()))
    elif mode.startswith("rotate_"):
        start_kid_id = _int_or_none(mode[7:])
        if start_kid_id not in valid_kid_ids and kids:
            start_kid_id = kids[0]["id"]
        conn.execute("UPDATE chores SET is_rotating=1 WHERE id=?", (chore_id,))
        ws = logic.d2s(logic.week_start(effective_today()))
        conn.execute(
            "INSERT INTO rotating_chore_assignments (chore_id, kid_id, week_start_date, is_override) "
            "VALUES (?,?,?,0)", (chore_id, start_kid_id, ws))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/chore/rotate", methods=["POST"])
@require_admin
def admin_chore_rotate():
    """Toggle rotation on any chore. ON: fixed assignments drop, this week's pick
    is seeded (then it auto-swaps). OFF: rotation assignments drop (chore becomes
    unassigned until you assign it)."""
    conn = get_db()
    chore_id = request.form.get("chore_id")
    row = conn.execute("SELECT is_rotating FROM chores WHERE id=? AND deleted=0",
                       (chore_id,)).fetchone()
    if row is None:
        return _admin_redirect()
    if row["is_rotating"]:
        conn.execute("UPDATE chores SET is_rotating=0 WHERE id=?", (chore_id,))
        conn.execute("DELETE FROM rotating_chore_assignments WHERE chore_id=?", (chore_id,))
    else:
        conn.execute("UPDATE chores SET is_rotating=1 WHERE id=?", (chore_id,))
        conn.execute("DELETE FROM weekly_assignments WHERE chore_id=?", (chore_id,))
        ws = logic.d2s(logic.week_start(effective_today()))
        exists = conn.execute("SELECT 1 FROM rotating_chore_assignments "
                              "WHERE chore_id=? AND week_start_date=?",
                              (chore_id, ws)).fetchone()
        kids = logic.active_kids(conn)
        if not exists and kids:
            start_id = _int_or_none(request.form.get("start_kid_id"))
            valid_ids = {k["id"] for k in kids}
            kid_id = start_id if start_id in valid_ids else kids[0]["id"]
            conn.execute("INSERT INTO rotating_chore_assignments (chore_id, kid_id, "
                         "week_start_date, is_override) VALUES (?,?,?,0)",
                         (chore_id, kid_id, ws))
    conn.commit()
    return _admin_redirect()


@app.route("/admin/rotation/swap", methods=["POST"])
@require_admin
def admin_rotation_swap():
    conn = get_db()
    logic.swap_rotation_this_week(conn, effective_today())
    return _admin_redirect()


@app.route("/admin/rotation/swap-one", methods=["POST"])
@require_admin
def admin_rotation_swap_one():
    conn = get_db()
    chore_id = _int_or_none(request.form.get("chore_id"))
    if chore_id:
        logic.swap_rotation_for_chore(conn, chore_id, effective_today())
    return _admin_redirect()


# ---- Settings page ------------------------------------------------------- #
@app.route("/admin/settings")
@require_admin
def admin_settings():
    return render_template("settings.html", **settings_view(get_db()))


@app.route("/admin/settings/targets", methods=["POST"])
@require_admin
def admin_settings_targets():
    conn = get_db()
    for k in logic.active_kids(conn):
        try:
            r = int(request.form.get("reading_%d" % k["id"]))
            o = int(request.form.get("outdoor_%d" % k["id"]))
        except (TypeError, ValueError):
            continue
        if r > 0 and o > 0:
            conn.execute("UPDATE kids SET reading_target_minutes=?, "
                         "outdoor_target_minutes=? WHERE id=?", (r, o, k["id"]))
    conn.commit()
    return redirect("/admin/settings?saved=1")


@app.route("/admin/settings/general", methods=["POST"])
@require_admin
def admin_settings_general():
    conn = get_db()
    reminder = (request.form.get("reminder_time") or "").strip()
    if reminder == "off" or _valid_time(reminder):
        logic.set_setting(conn, "reminder_time", reminder)
    for field, key in (("program_start", "program_start_date"),
                       ("program_end", "program_end_date")):
        val = (request.form.get(field) or "").strip()
        if _valid_date(val):
            logic.set_setting(conn, key, val)
    logic.set_setting(conn, "scoreboard_reward_text",
                      (request.form.get("reward") or "").strip())
    conn.commit()
    return redirect("/admin/settings?saved=1")


@app.route("/admin/settings/pushover", methods=["POST"])
@require_admin
def admin_settings_pushover():
    conn = get_db()
    logic.set_setting(conn, "pushover_app_token",
                      (request.form.get("pushover_app") or "").strip())
    logic.set_setting(conn, "pushover_user_key",
                      (request.form.get("pushover_user") or "").strip())
    conn.commit()
    return redirect("/admin/settings?saved=1")


@app.route("/admin/settings/password", methods=["POST"])
@require_admin
def admin_settings_password():
    conn = get_db()
    stored = logic.get_setting(conn, "admin_password_hash", "")
    current = request.form.get("current", "")
    new = request.form.get("new", "")
    confirm = request.form.get("confirm", "")
    if db.verify_password(stored, current) and new and new == confirm:
        logic.set_setting(conn, "admin_password_hash", db.hash_password(new))
        conn.commit()
        return redirect("/admin/settings?saved=1")
    return redirect("/admin/settings?pwerror=1")


@app.route("/admin/special/add", methods=["POST"])
@require_admin
def admin_special_add():
    conn = get_db()
    label = (request.form.get("label") or "").strip()
    ptype = request.form.get("type")
    start = (request.form.get("start_date") or "").strip()
    end = (request.form.get("end_date") or "").strip()
    omd = request.form.get("outdoor_minutes_per_day")
    if label and ptype in ("paused", "outdoor_credit") and _valid_date(start) and _valid_date(end):
        try:
            omd = int(omd) if ptype == "outdoor_credit" else None
        except (TypeError, ValueError):
            omd = None
        conn.execute(
            "INSERT INTO special_periods (label, type, start_date, end_date, "
            "outdoor_minutes_per_day) VALUES (?,?,?,?,?)",
            (label, ptype, start, end, omd))
        conn.commit()
    return redirect("/admin/settings?saved=1")


@app.route("/admin/special/edit", methods=["POST"])
@require_admin
def admin_special_edit():
    conn = get_db()
    label = (request.form.get("label") or "").strip()
    ptype = request.form.get("type")
    start = (request.form.get("start_date") or "").strip()
    end = (request.form.get("end_date") or "").strip()
    omd = request.form.get("outdoor_minutes_per_day")
    if label and ptype in ("paused", "outdoor_credit") and _valid_date(start) and _valid_date(end):
        try:
            omd = int(omd) if ptype == "outdoor_credit" else None
        except (TypeError, ValueError):
            omd = None
        conn.execute(
            "UPDATE special_periods SET label=?, type=?, start_date=?, end_date=?, "
            "outdoor_minutes_per_day=? WHERE id=?",
            (label, ptype, start, end, omd, request.form.get("id")))
        conn.commit()
    return redirect("/admin/settings?saved=1")


@app.route("/admin/special/delete", methods=["POST"])
@require_admin
def admin_special_delete():
    conn = get_db()
    conn.execute("DELETE FROM special_periods WHERE id=?", (request.form.get("id"),))
    conn.commit()
    return redirect("/admin/settings?saved=1")


# ---- History page -------------------------------------------------------- #
@app.route("/admin/history")
@require_admin
def admin_history():
    conn = get_db()
    return render_template("history.html", **history_view(conn, effective_today()))


# ---- Log edit page ------------------------------------------------------- #
@app.route("/admin/logs")
@require_admin
def admin_logs():
    conn = get_db()
    return render_template("logs.html", **logs_view(conn, effective_today()))


def _logs_redirect():
    today = request.form.get("today")
    return redirect("/admin/logs?today=" + today if today else "/admin/logs")


@app.route("/admin/log/edit", methods=["POST"])
@require_admin
def admin_log_edit():
    conn = get_db()
    kind = request.form.get("kind")
    if kind not in ("reading", "outdoor"):
        abort(400)
    table = "reading_logs" if kind == "reading" else "outdoor_logs"
    try:
        minutes = int(request.form.get("minutes"))
    except (TypeError, ValueError):
        minutes = 0
    if minutes > 0:
        conn.execute("UPDATE %s SET minutes=? WHERE id=? AND source='manual'" % table,
                     (minutes, request.form.get("log_id")))
        conn.commit()
    return _logs_redirect()


@app.route("/admin/log/delete", methods=["POST"])
@require_admin
def admin_log_delete():
    conn = get_db()
    kind = request.form.get("kind")
    if kind not in ("reading", "outdoor"):
        abort(400)
    table = "reading_logs" if kind == "reading" else "outdoor_logs"
    conn.execute("DELETE FROM %s WHERE id=? AND source='manual'" % table,
                 (request.form.get("log_id"),))
    conn.commit()
    return _logs_redirect()


@app.route("/status")
def status_page():
    conn = get_db()
    d = effective_today()
    return render_template("status.html", **status_view(conn, d))


@app.route("/<slug>")
def kid_page(slug):
    conn = get_db()
    kid = logic.kid_by_slug(conn, slug)
    if kid is None:
        abort(404)
    d = effective_today()
    return render_template("kid.html", **kid_view(conn, kid, d))


def _require_kid(conn, payload):
    kid = logic.kid_by_slug(conn, (payload or {}).get("slug", ""))
    if kid is None:
        abort(404)
    return kid


def _maybe_reinstate_bonus(conn, kid, d):
    """After any kid action on Monday, see if the make-up bonus is now earned."""
    row = logic.check_makeup_reinstatement(conn, kid["id"], d)
    if row is not None:
        notify.send(conn, "Chore Tracker",
                    "%s earned his bonus back!" % kid["name"])
        return True
    return False


@app.route("/api/chore/complete", methods=["POST"])
def api_chore_complete():
    conn = get_db()
    data = request.get_json(silent=True) or {}
    kid = _require_kid(conn, data)
    d = effective_today()
    kind = data.get("kind", "daily")

    if kind == "as_needed":
        assignment_id = data.get("id")
        row = conn.execute(
            "SELECT a.*, c.name FROM as_needed_assignments a "
            "JOIN chores c ON c.id=a.chore_id "
            "WHERE a.id=? AND a.kid_id=?", (assignment_id, kid["id"])).fetchone()
        if row is None:
            abort(404)
        if row["completed_at"] is None:
            conn.execute("UPDATE as_needed_assignments SET completed_at=? WHERE id=?",
                         (logic.now_iso(), row["id"]))
            conn.commit()
            notify.send(conn, "Chore Tracker",
                        "%s completed: %s" % (kid["name"], row["name"]))
        return jsonify({"ok": True})

    # Daily / alternate_daily / weekly / scheduled chore — all record a
    # chore_completion row for today. Only daily/alternate_daily fire the
    # checklist notification.
    ctype = kind if kind in ("weekly", "scheduled", "alternate_daily") else "daily"
    chore_id = data.get("id")
    chore = conn.execute("SELECT * FROM chores WHERE id=? AND type=? AND active=1",
                         (chore_id, ctype)).fetchone()
    if chore is None:
        abort(404)
    conn.execute(
        "INSERT OR IGNORE INTO chore_completions (kid_id, chore_id, "
        "completion_date, completed_at, parent_verified) VALUES (?,?,?,?,0)",
        (kid["id"], chore_id, logic.d2s(d), logic.now_iso()))
    conn.commit()

    done = False
    if ctype in ("daily", "alternate_daily"):
        done, _ = logic.checklist_status(conn, kid["id"], d)
        if done:
            notify.send_once(conn, kid["id"], "daily_complete", "Chore Tracker ✓",
                             "%s finished his daily checklist!" % kid["name"], d)
    reinstated = _maybe_reinstate_bonus(conn, kid, d)
    return jsonify({"ok": True, "checklist_done": done, "bonus_reinstated": reinstated})


@app.route("/api/log", methods=["POST"])
def api_log_add():
    conn = get_db()
    data = request.get_json(silent=True) or {}
    kid = _require_kid(conn, data)
    d = effective_today()
    kind = data.get("kind")
    if kind not in ("reading", "outdoor"):
        abort(400)
    try:
        minutes = int(data.get("minutes"))
    except (TypeError, ValueError):
        abort(400)
    if minutes <= 0 or minutes > 600:
        abort(400)

    table = "reading_logs" if kind == "reading" else "outdoor_logs"
    conn.execute(
        "INSERT INTO %s (kid_id, log_date, minutes, source, logged_at) "
        "VALUES (?,?,?, 'manual', ?)" % table,
        (kid["id"], logic.d2s(d), minutes, logic.now_iso()))
    conn.commit()

    reinstated = _maybe_reinstate_bonus(conn, kid, d)
    section = log_section(conn, kid, kind, d)
    section["bonus_reinstated"] = reinstated
    return jsonify(section)


@app.route("/api/log", methods=["DELETE"])
def api_log_remove():
    conn = get_db()
    data = request.get_json(silent=True) or {}
    kid = _require_kid(conn, data)
    d = effective_today()
    kind = data.get("kind")
    if kind not in ("reading", "outdoor"):
        abort(400)
    table = "reading_logs" if kind == "reading" else "outdoor_logs"
    # Same-day, manual entries only — kids can't delete camp auto-credit or history.
    conn.execute(
        "DELETE FROM %s WHERE id=? AND kid_id=? AND log_date=? AND source='manual'"
        % table, (data.get("id"), kid["id"], logic.d2s(d)))
    conn.commit()
    return jsonify(log_section(conn, kid, kind, d))


# Populate kid paths for the no-store filter now that the DB is seeded.
with db.connect() as _c:
    _KID_PATHS = {"/" + r["url_slug"] for r in logic.active_kids(_c)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7823"))
    # Start the background scheduler here (single process) so reminders/summaries
    # fire exactly once. use_reloader=False keeps it to one process; threaded=True
    # handles the two-iPads-at-once case.
    scheduler.start(os.environ)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
