"""SQLite schema, connection helper, and first-run seed for Chore Tracker.

All date columns store the LOCAL date (YYYY-MM-DD) in the configured timezone,
never UTC. See logic.py for the date helpers everything routes through.
"""
import os
import secrets
import sqlite3
from datetime import datetime
from hashlib import scrypt
from hmac import compare_digest

# Default data dir is ./data (a TrueNAS volume in prod). Overridable for tests.
DATA_DIR = os.environ.get("CHORE_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.environ.get("CHORE_DB_PATH", os.path.join(DATA_DIR, "chore_tracker.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS kids (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url_slug TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    -- Per-kid targets (v1.1 E10): default both to the global seed values so they
    -- can diverge later without a schema change.
    reading_target_minutes INTEGER NOT NULL DEFAULT 175,
    outdoor_target_minutes INTEGER NOT NULL DEFAULT 300,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chores (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,            -- 'daily', 'weekly', 'as_needed', or 'scheduled'
    is_rotating INTEGER NOT NULL DEFAULT 0,  -- chores that rotate kids weekly
    active INTEGER NOT NULL DEFAULT 1,        -- show/hide on kid pages
    deleted INTEGER NOT NULL DEFAULT 0,       -- soft-delete: hidden from admin too
    -- 'scheduled' chores only: due weekday (0=Mon..6=Sun), how many days early the
    -- countdown starts, and a label shown to the kid (e.g. "Monday night").
    due_weekday INTEGER,
    reminder_lead_days INTEGER,
    due_label TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chore_completions (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    chore_id INTEGER NOT NULL,
    completion_date TEXT NOT NULL,   -- YYYY-MM-DD local
    completed_at TEXT NOT NULL,
    parent_verified INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kid_id, chore_id, completion_date)
);

CREATE TABLE IF NOT EXISTS as_needed_assignments (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    chore_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL,
    completed_at TEXT             -- NULL until the kid checks it off
);

-- v1.2: weekly chores are standing per-kid assignments that recur each week.
CREATE TABLE IF NOT EXISTS weekly_assignments (
    id INTEGER PRIMARY KEY,
    chore_id INTEGER NOT NULL,
    kid_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (chore_id, kid_id)
);

CREATE TABLE IF NOT EXISTS rotating_chore_assignments (
    id INTEGER PRIMARY KEY,
    chore_id INTEGER NOT NULL,
    kid_id INTEGER NOT NULL,
    week_start_date TEXT NOT NULL,   -- Monday of the week
    is_override INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chore_id, week_start_date)
);

CREATE TABLE IF NOT EXISTS reading_logs (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,          -- YYYY-MM-DD local
    minutes INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' or 'camp_auto'
    logged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outdoor_logs (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,          -- YYYY-MM-DD local
    minutes INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' or 'camp_auto'
    logged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS notifications_sent (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER,
    notification_date TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE (kid_id, notification_date, notification_type)
);

CREATE TABLE IF NOT EXISTS weekly_results (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    week_start_date TEXT NOT NULL,
    reading_minutes INTEGER NOT NULL DEFAULT 0,
    outdoor_minutes INTEGER NOT NULL DEFAULT 0,
    reading_target INTEGER NOT NULL DEFAULT 0,   -- prorated target used that week
    outdoor_target INTEGER NOT NULL DEFAULT 0,
    active_days INTEGER NOT NULL DEFAULT 7,
    is_paused_week INTEGER NOT NULL DEFAULT 0,   -- active_days == 0 -> excluded from streak
    bonus_earned INTEGER,                        -- NULL for paused weeks
    computed_at TEXT NOT NULL,
    UNIQUE (kid_id, week_start_date)
);

-- v1.1 A: vacation / camp periods.
CREATE TABLE IF NOT EXISTS special_periods (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    type TEXT NOT NULL,              -- 'paused' or 'outdoor_credit'
    start_date TEXT NOT NULL,        -- inclusive, local
    end_date TEXT NOT NULL,          -- inclusive, local
    outdoor_minutes_per_day INTEGER  -- only for type='outdoor_credit'
);

-- v1.1 C: make-up Monday bonus reinstatement.
CREATE TABLE IF NOT EXISTS makeup_owed (
    id INTEGER PRIMARY KEY,
    kid_id INTEGER NOT NULL,
    for_week_start TEXT NOT NULL,    -- Monday of the week the make-up applies to
    reading_deficit INTEGER NOT NULL DEFAULT 0,
    outdoor_deficit INTEGER NOT NULL DEFAULT 0,
    satisfied_at TEXT,               -- set when the bonus is earned back
    UNIQUE (kid_id, for_week_start)
);
"""


def connect(db_path=None):
    """Open a connection with row access by name and WAL enabled (v1.1 E7)."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _hash_password(password):
    """Salted scrypt hash, stored as 'salt$hex'. Stdlib only, no extra deps."""
    salt = secrets.token_hex(16)
    digest = scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"),
                    n=16384, r=8, p=1)
    return salt + "$" + digest.hex()


hash_password = _hash_password  # public alias for the admin password-change flow


def verify_password(stored, password):
    """Constant-time check of `password` against a 'salt$hex' scrypt hash."""
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    calc = scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"),
                  n=16384, r=8, p=1).hex()
    return compare_digest(calc, digest)


def _ensure_column(conn, table, column, decl):
    """Add a column if an older DB predates it (non-destructive migration)."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    if column not in cols:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))


def init_db(conn, env=None):
    """Create tables and seed first-run data if the DB is empty."""
    env = env if env is not None else os.environ
    conn.executescript(SCHEMA)
    # Migrations for DBs created before a column existed.
    _ensure_column(conn, "chores", "deleted", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chores", "due_weekday", "INTEGER")
    _ensure_column(conn, "chores", "reminder_lead_days", "INTEGER")
    _ensure_column(conn, "chores", "due_label", "TEXT")
    _ensure_column(conn, "chores", "alt_day_parity", "INTEGER")
    _migrate_bins_to_scheduled(conn)
    conn.commit()
    if conn.execute("SELECT COUNT(*) AS n FROM kids").fetchone()["n"] == 0:
        _seed(conn, env)
    else:
        _migrate_daily_assignments(conn)   # existing DB: preserve daily-for-both
    conn.commit()


def _migrate_daily_assignments(conn):
    """v1.4: daily chores became per-kid. Preserve current behavior by assigning
    every existing daily chore to all active kids — once."""
    done = conn.execute(
        "SELECT value FROM settings WHERE key='daily_assignment_migrated'").fetchone()
    if done:
        return
    now = datetime.now().isoformat(timespec="seconds")
    kids = [r["id"] for r in conn.execute("SELECT id FROM kids WHERE active=1").fetchall()]
    for ch in conn.execute(
            "SELECT id FROM chores WHERE type='daily' AND is_rotating=0").fetchall():
        for kid in kids:
            conn.execute(
                "INSERT OR IGNORE INTO weekly_assignments (chore_id, kid_id, created_at) "
                "VALUES (?,?,?)", (ch["id"], kid, now))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES "
                 "('daily_assignment_migrated', '1')")


def _migrate_bins_to_scheduled(conn):
    """One-time: turn an existing 'Put bins out at curb' into a scheduled chore.

    Idempotent (guarded on due_weekday IS NULL) and a no-op on fresh DBs, where the
    seed already creates bins as scheduled.
    """
    conn.execute(
        "UPDATE chores SET type='scheduled', is_rotating=1, due_weekday=0, "
        "reminder_lead_days=5, due_label='Monday night' "
        "WHERE name='Put bins out at curb' AND due_weekday IS NULL")


def _seed(conn, env):
    from logic import now_iso  # local import to avoid a cycle at module load

    ts = now_iso(env)
    reading_target = 175
    outdoor_target = 300

    # Kids -------------------------------------------------------------------
    conn.executemany(
        "INSERT INTO kids (name, url_slug, active, reading_target_minutes, "
        "outdoor_target_minutes, created_at) VALUES (?,?,1,?,?,?)",
        [
            ("Andrew", "andrew", reading_target, outdoor_target, ts),
            ("Daniel", "daniel", reading_target, outdoor_target, ts),
        ],
    )

    # Chores -----------------------------------------------------------------
    daily = [
        "Make your bed",
        "Empty the dishwasher",
        "Tidy common areas (living room & family room)",
    ]
    for name in daily:
        conn.execute(
            "INSERT INTO chores (name, type, is_rotating, active, created_at) "
            "VALUES (?, 'daily', 0, 1, ?)", (name, ts))

    # Rotating as-needed chore.
    conn.execute(
        "INSERT INTO chores (name, type, is_rotating, active, created_at) "
        "VALUES ('Take indoor trash to outdoor bins', 'as_needed', 1, 1, ?)", (ts,))

    # Scheduled rotating chore: due Monday night, 5-day countdown.
    conn.execute(
        "INSERT INTO chores (name, type, is_rotating, active, created_at, "
        "due_weekday, reminder_lead_days, due_label) "
        "VALUES ('Put bins out at curb', 'scheduled', 1, 1, ?, 0, 5, 'Monday night')",
        (ts,))

    conn.execute(
        "INSERT INTO chores (name, type, is_rotating, active, created_at) "
        "VALUES ('Put clothes away', 'as_needed', 0, 1, ?)", (ts,))

    # Weekly chore (v1.2): standing per-kid assignment, recurs every week.
    conn.execute(
        "INSERT INTO chores (name, type, is_rotating, active, created_at) "
        "VALUES ('Clean your room', 'weekly', 0, 1, ?)", (ts,))

    # Week-1 rotating assignment: Andrew -> trash, Daniel -> bins.
    # week_start is the Monday the program begins (2026-06-22).
    andrew = conn.execute("SELECT id FROM kids WHERE url_slug='andrew'").fetchone()["id"]
    daniel = conn.execute("SELECT id FROM kids WHERE url_slug='daniel'").fetchone()["id"]
    trash = conn.execute("SELECT id FROM chores WHERE name=?",
                         ("Take indoor trash to outdoor bins",)).fetchone()["id"]
    bins = conn.execute("SELECT id FROM chores WHERE name=?",
                        ("Put bins out at curb",)).fetchone()["id"]
    week1 = "2026-06-22"
    conn.execute(
        "INSERT INTO rotating_chore_assignments (chore_id, kid_id, week_start_date, "
        "is_override) VALUES (?,?,?,0)", (trash, andrew, week1))
    conn.execute(
        "INSERT INTO rotating_chore_assignments (chore_id, kid_id, week_start_date, "
        "is_override) VALUES (?,?,?,0)", (bins, daniel, week1))

    # Weekly assignment: "Clean your room" recurs for both kids.
    clean_room = conn.execute("SELECT id FROM chores WHERE name=?",
                              ("Clean your room",)).fetchone()["id"]
    conn.executemany(
        "INSERT INTO weekly_assignments (chore_id, kid_id, created_at) VALUES (?,?,?)",
        [(clean_room, andrew, ts), (clean_room, daniel, ts)])

    # Daily chores go to BOTH kids by default (v1.4: assignment is explicit now).
    for row in conn.execute("SELECT id FROM chores WHERE type='daily'").fetchall():
        conn.executemany(
            "INSERT OR IGNORE INTO weekly_assignments (chore_id, kid_id, created_at) "
            "VALUES (?,?,?)", [(row["id"], andrew, ts), (row["id"], daniel, ts)])

    # Settings ---------------------------------------------------------------
    settings = {
        "reading_weekly_target_minutes": str(reading_target),
        "outdoor_weekly_target_minutes": str(outdoor_target),
        "pushover_app_token": env.get("PUSHOVER_APP_TOKEN", ""),
        "pushover_user_key": env.get("PUSHOVER_USER_KEY", ""),
        "admin_password_hash": _hash_password(env.get("ADMIN_PASSWORD", "changeme")),
        "week_start_day": "monday",
        "timezone": env.get("TZ", "America/New_York"),
        "reminder_time": "10:00",
        "program_start_date": "2026-06-22",  # Monday; skips the school half-week
        "program_end_date": "2026-08-30",    # Sunday; school resumes Aug 31
        "scoreboard_reward_text": "",        # blank until Allen & the boys choose it
        "flask_secret": secrets.token_hex(32),
    }
    conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                     list(settings.items()))

    # Special periods (v1.1 A) ----------------------------------------------
    conn.executemany(
        "INSERT INTO special_periods (label, type, start_date, end_date, "
        "outdoor_minutes_per_day) VALUES (?,?,?,?,?)",
        [
            ("Alaska cruise", "paused", "2026-07-03", "2026-07-14", None),
            ("Congo camp", "outdoor_credit", "2026-07-20", "2026-07-31", 60),
        ],
    )
