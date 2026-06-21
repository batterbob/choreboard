"""Notifications via Apprise — fire-and-forget. A failure must never block an API
response or raise into the request path: log and return False."""
import logging

import logic

log = logging.getLogger("chore.notify")


def _build_urls(conn):
    """Construct Apprise URL(s) from the configured service and its credentials."""
    service = logic.get_setting(conn, "notify_service", "none") or "none"
    g = lambda k: (logic.get_setting(conn, k, "") or "").strip()
    if service == "pushover":
        app, user = g("notify_pushover_app_token"), g("notify_pushover_user_key")
        return ["pover://%s@%s" % (user, app)] if app and user else []
    if service == "telegram":
        token, chat = g("notify_telegram_token"), g("notify_telegram_chatid")
        return ["tgram://%s/%s" % (token, chat)] if token and chat else []
    if service == "discord":
        url = g("notify_discord_webhook")
        return [url] if url else []
    if service == "slack":
        url = g("notify_slack_webhook")
        return [url] if url else []
    if service == "ntfy":
        topic, host = g("notify_ntfy_topic"), g("notify_ntfy_host").rstrip("/")
        if not topic:
            return []
        if host:
            bare = host.replace("https://", "").replace("http://", "")
            scheme = "ntfys" if host.startswith("https") else "ntfy"
            return ["%s://%s/%s" % (scheme, bare, topic)]
        return ["ntfy://%s" % topic]   # public ntfy.sh
    if service == "gotify":
        host, token = g("notify_gotify_url").rstrip("/"), g("notify_gotify_token")
        if host and token:
            bare = host.replace("https://", "").replace("http://", "")
            return ["gotifys://%s/%s" % (bare, token)]
        return []
    if service == "apprise":
        raw = g("notify_urls")
        return [u for u in (line.strip() for line in raw.splitlines()) if u]
    return []   # "none" or unknown


def send(conn, title, message):
    """Best-effort Apprise notification. Returns True on success, never raises."""
    urls = _build_urls(conn)
    if not urls:
        log.warning("No notification service configured; skipping: %s", title)
        return False
    try:
        import apprise
        ap = apprise.Apprise()
        for url in urls:
            ap.add(url)
        result = ap.notify(title=title, body=message)
        if not result:
            log.error("Apprise notify returned False for: %s", title)
        return bool(result)
    except Exception as exc:  # noqa: BLE001 - fire-and-forget by design
        log.error("Notification send failed: %s", exc)
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
