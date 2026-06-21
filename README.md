# Choreify

**A simple chore and habit tracker your kids run themselves.**

Every morning your kids open their own page on a tablet or phone, check off their chores, and log things like reading or outdoor time. If they hit their goals for the week, they earn a reward you choose — extra screen time, allowance, whatever works in your house. You get a notification when they're done and a dashboard showing how everyone's doing.

No accounts to create, no monthly fee, no data leaving your home. It runs on your own computer or home server.

> Built with plain Flask + SQLite. One small Docker container, no database server to manage.

---

## Is this for you?

Choreify is a good fit if:

- You want kids to track their own chores without nagging.
- You'd like to tie a reward to finishing the week.
- You're comfortable running a **Docker container** on a home server, NAS, or an always-on PC. *(If you've never used Docker, the [Deploy](#deploy-with-docker) section walks through it — but this isn't a hosted app you just sign up for. You run it yourself.)*

It works great for the summer, the school year, or all year round — you set the start and end dates (or leave it running indefinitely).

---

## What it looks like

**Each kid's page** — daily checklist, weekly chores, activity logging, and a scoreboard of stars and streaks:

![Kid page](docs/screenshots/kid-page.png)

**Your dashboard** — today's status and the week's progress for every kid at a glance:

![Admin dashboard](docs/screenshots/admin-dashboard.png)

**Status page** — a read-only view you can leave open on a kitchen tablet, no password needed:

![Status page](docs/screenshots/status-page.png)

**Settings** — kids, activities, goals, the reward, vacations, and notifications:

![Settings](docs/screenshots/settings-page.png)

---

## Who uses what

- **Kids** get their own page at a simple link (e.g. `yourserver/alex`). The link *is* their login — no password to remember. They check off chores and log their minutes themselves.
- **You** get a password-protected dashboard to see everyone's progress, mark chores done, assign extra ones, set goals, and handle vacations.
- **The whole family** can leave the status page up on a shared tablet — it auto-refreshes and needs no password.

---

## How the week works

- **Chores** come in a few flavors: every day, every other day, once a week, on a specific weekday with a countdown, or one-off "do this today" assignments. Any chore can also **rotate** — Choreify automatically swaps it between kids each week so you don't have to track whose turn it is.
- **Activities** are optional things kids log toward a weekly target — reading and outdoor time out of the box. Add your own (practice piano, glasses of water, pages read…), each measured in minutes or as a count, rename them, or turn them all off for a pure chores setup.
- **The bonus.** Each week a kid who hits their goals earns a star and the reward you set. If you turn activities off, the bonus is simply about finishing the daily checklist — you choose how many days a week count.
- **Points (optional).** Give each chore a point value and kids rack up points as they go — handy for a pocket-money system. Set a dollars-per-point rate and the page shows what they've earned this week.
- **Reminders.** Choreify can send you a phone notification mid-morning if a kid hasn't finished, plus a Sunday-evening wrap-up of the week.
- **Vacations.** Mark a trip or camp and Choreify pauses chores (or auto-credits outdoor time) so a week off doesn't break anyone's streak.

---

## Deploy with Docker

```bash
git clone https://github.com/batterbob/Choreify.git
cd choreboard
cp .env.example .env   # then edit .env with your own values
docker compose up -d --build
```

Check that it started:

```bash
docker compose logs -f
# look for: Scheduler started (minute interval)
```

Open `http://your-server-address:7823` in a browser. The first time, Choreify walks you through a short setup: app name, timezone, your kids, the activities you want to track, and the reward. Everything is changeable later in Settings.

Your data lives in `./data/` on the host (a SQLite file), so it survives restarts and updates. To update later: pull the latest code and run `docker compose up -d --build` again — the database migrates itself without losing anything.

---

## Configuration (`.env`)

A few settings live in a `.env` file in the project folder. It's gitignored — never commit it. Start from the template:

```dotenv
ADMIN_PASSWORD=change-me-before-first-run
PORT=7823
TZ=America/New_York
CHORE_DEBUG=0

# Optional: ping a monitor each minute so you know the app is alive
# HEALTHCHECK_URL=https://your-monitor-url/ping
```

- **`ADMIN_PASSWORD`** — set a real password before the first run. Change it later in Settings.
- **`TZ`** — your local timezone, e.g. `America/Chicago`, `Europe/London`. All the day-by-day logic runs in this zone.
- **`PORT`** — the port Choreify listens on (default 7823).
- **`CHORE_DEBUG`** — leave `0` normally.

---

## Notifications

Choreify can send a push notification when a kid finishes and a weekly summary on Sunday. Supported services include **Pushover, Telegram, Discord, Slack, ntfy, and Gotify** (anything [Apprise](https://github.com/caronc/apprise) supports). Pick one in **Settings → Notifications**, paste in your credentials, and hit *Send test notification* to confirm it works. There's step-by-step help next to each service.

---

## Monitoring (optional)

Choreify has a health endpoint at `/healthz` that returns `ok` when the app and database are healthy. For peace of mind that the background scheduler keeps running, set `HEALTHCHECK_URL` in your `.env` — the app pings it every minute, so a monitor like Uptime Kuma or Healthchecks.io can alert you if it ever goes quiet. Leave it blank to skip.

---

## Integrations API

Choreify exposes a read-only JSON status endpoint that any app on your local network can poll — no password, no setup.

| Endpoint | Returns |
|----------|---------|
| `GET /api/v1/status` | Status for all kids |
| `GET /api/v1/status/<slug>` | Status for one kid, e.g. `/api/v1/status/alex` |

**Example response** (`/api/v1/status/alex`):

```json
{
  "generated_at": "2026-06-21T14:32:00",
  "date": "2026-06-21",
  "program": { "active": true, "paused": false, "start": "2026-06-22", "end": "2026-08-30" },
  "kid": {
    "id": 1, "name": "Alex", "slug": "alex",
    "checklist": {
      "done": false, "completed_at": null, "total": 3, "finished": 2,
      "chores": [
        { "id": 1, "name": "Make your bed", "done": true },
        { "id": 2, "name": "Empty the dishwasher", "done": true },
        { "id": 3, "name": "Tidy common areas", "done": false }
      ]
    },
    "activities": [
      { "key": "reading", "label": "Reading", "unit": "minutes",
        "week_amount": 45, "target": 175, "pct": 26, "met": false },
      { "key": "outdoor", "label": "Outdoor Time", "unit": "minutes",
        "week_amount": 120, "target": 300, "pct": 40, "met": false }
    ],
    "banner": "on_track",
    "bonus_earned": false,
    "stars": 3,
    "streak": 2
  }
}
```

**`banner`** is one of: `on_track` 🟢 · `at_risk` 🔴 · `earned` ✅ · `on_break` ☀️ · `out_of_program` — maps directly to display colors.

The `activities` array reflects whatever activities you have enabled in Settings — add or rename them and the API updates automatically.

---

## Building integrations

### Quick test (curl)

```bash
curl http://<server>:7823/api/v1/status/alex | python -m json.tool
```

### Python

```python
import requests

data = requests.get("http://<server>:7823/api/v1/status").json()

if not data["program"]["active"]:
    print("Choreify is off-season")
else:
    for kid in data["kids"]:
        status = "✅ done" if kid["checklist"]["done"] else (
            f"{kid['checklist']['finished']}/{kid['checklist']['total']} chores")
        print(f"{kid['name']}: {status}  |  banner: {kid['banner']}")
        for act in kid["activities"]:
            print(f"  {act['label']}: {act['week_amount']}/{act['target']} ({act['pct']}%)")
```

### Home Assistant (REST sensor)

```yaml
sensor:
  - platform: rest
    name: choreboard_alex
    resource: http://<server>:7823/api/v1/status/alex
    value_template: "{{ value_json.kid.checklist.done }}"
    json_attributes_path: "$.kid"
    json_attributes: [checklist, activities, banner, bonus_earned, stars, streak]
    scan_interval: 60
```

Automation condition — fires when Alex finishes the daily checklist:
```yaml
condition:
  condition: template
  value_template: "{{ is_state('sensor.choreboard_alex', 'True') }}"
```

### Tidbyt / Tronbyt (Starlark)

```python
load("http.star", "http")
load("render.star", "render")

def main():
    r = http.get("http://<server>:7823/api/v1/status/alex", ttl_seconds=60)
    kid = r.json()["kid"]

    color = {
        "earned": "#FFD700", "on_track": "#00CC44",
        "at_risk": "#FF3333", "on_break": "#6699CC",
    }.get(kid["banner"], "#888888")

    label = "✓ Done" if kid["checklist"]["done"] else (
        "%d/%d chores" % (kid["checklist"]["finished"], kid["checklist"]["total"]))

    rows = [render.Text(content=kid["name"], color=color), render.Text(content=label)]
    for act in kid["activities"]:
        rows.append(render.Text(content="%s %d%%" % (act["label"], act["pct"])))

    return render.Root(child=render.Column(children=rows))
```

### Guard checks before displaying

```
program.active == false   →  show "off-season" or hide widget
program.paused == true    →  show "on break" (banner will also say "on_break")
kid.checklist.total == 0  →  no chores assigned today
```

---

## Good to know

- **One process on purpose.** Choreify runs as a single process so the background reminders fire exactly once. Don't put it behind a multi-worker server.
- **Local network only.** There's no HTTPS built in — run it on your home network.
- **Your data is yours.** Export everything to a JSON file from Settings any time, and restore from that file just as easily.

---

## License

MIT — see [LICENSE](LICENSE). Use it, change it, share it.
