"""Pushover notifications — fire-and-forget. A failure must never block an API
response or raise into the request path (spec: log and return 200)."""
import logging

import requests

import logic

log = logging.getLogger("chore.notify")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def send(conn, title, message):
    """Best-effort Pushover send. Returns True on success, never raises."""
    token = logic.get_setting(conn, "pushover_app_token", "") or ""
    user = logic.get_setting(conn, "pushover_user_key", "") or ""
    if not token or not user:
        log.warning("Pushover not configured; skipping: %s / %s", title, message)
        return False
    try:
        resp = requests.post(
            PUSHOVER_URL,
            data={"token": token, "user": user, "title": title, "message": message},
            timeout=5,
        )
        if resp.status_code != 200:
            log.error("Pushover returned %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - fire-and-forget by design
        log.error("Pushover send failed: %s", exc)
        return False


def send_once(conn, kid_id, ntype, title, message, d):
    """Send only if this (kid, date, type) hasn't been sent — dedup backstop.

    The UNIQUE constraint on notifications_sent makes the claim atomic, so two
    near-simultaneous requests can't both send.
    """
    ds = logic.d2s(d)
    try:
        conn.execute(
            "INSERT INTO notifications_sent (kid_id, notification_date, "
            "notification_type, sent_at) VALUES (?,?,?,?)",
            (kid_id, ds, ntype, logic.now_iso()))
        conn.commit()
    except Exception:
        return False  # already recorded -> already sent (or being sent)
    return send(conn, title, message)
