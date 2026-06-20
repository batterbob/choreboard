# Summer Tracker — Deployment & Setup Guide

Step-by-step to get the app running on TrueNAS, monitored in Uptime Kuma, and
reachable by friendly names through Firewalla.

By the end you'll have:
- the container running on TrueNAS at port **7823**,
- the kids opening their pages from a home-screen icon on their iPads,
- Uptime Kuma watching both the web app and the background scheduler,
- Pushover notifications working.

---

## Before you start — gather these

1. **TrueNAS LAN IP** — e.g. `192.168.1.50`. (TrueNAS UI → top-right, or your
   router.) Write it down; you'll use it a lot below. We'll call it `TRUENAS_IP`.
2. **Pushover credentials** — your App Token and User Key (pushover.net).
3. **SSH access to TrueNAS** (recommended) — or the built-in TrueNAS Shell.
4. The project files (this folder).

---

## Part 1 — Deploy on TrueNAS SCALE

TrueNAS SCALE runs Docker. We'll put the code in a dataset (so the database
survives upgrades) and start it with Docker Compose.

### 1.1 Create a dataset for the app

TrueNAS UI → **Datasets** → pick your pool → **Add Dataset**.
- Name: `summer-tracker`
- Leave defaults. Note the resulting path, e.g. `/mnt/tank/summer-tracker`.
  We'll call it `APP_DIR`.

### 1.2 Get the files onto TrueNAS

Easiest: enable SMB sharing on the dataset and copy the project folder's contents
into it from your PC, **or** clone with git over SSH:

```bash
# SSH into TrueNAS, then:
cd /mnt/tank/summer-tracker
# copy the files here (git clone <your repo> . , or scp/SMB copy)
ls    # you should see app.py, docker-compose.yml, Dockerfile, etc.
```

### 1.3 Create the `.env` file (secrets)

In `APP_DIR`, create a file named `.env` with your real values:

```dotenv
ADMIN_PASSWORD=pick-a-good-password
PUSHOVER_APP_TOKEN=your-app-token
PUSHOVER_USER_KEY=your-user-key
PORT=7823
TZ=America/New_York
CHORE_DEBUG=0
UPTIME_KUMA_PUSH_URL=
```

> **Important:** `ADMIN_PASSWORD` is locked into the database the *first* time the
> app starts. Set the one you want now. (To change it later: use the in-app
> Settings page, or delete `data/chore_tracker.db` to start fresh.)

### 1.4 Start it

From `APP_DIR` over SSH:

```bash
docker compose up -d --build
```

This builds the image and starts the container in the background. First build
takes a couple of minutes.

Check it came up cleanly — you want to see `Scheduler started (minute interval)`:

```bash
docker compose logs -f
# Ctrl+C to stop watching the logs (the container keeps running)
```

> If `docker compose` isn't found, your TrueNAS may be an older (Kubernetes-era)
> release. Tell me and I'll give you the alternative.

### 1.5 Verify

From any computer on the network, open:

- `http://TRUENAS_IP:7823/healthz` → should show `{"status":"ok"}`
- `http://TRUENAS_IP:7823/admin` → the admin login (sign in with `ADMIN_PASSWORD`)
- `http://TRUENAS_IP:7823/status` → the parent status page

The summer hasn't started yet (program window opens 2026-06-22), so the kid pages
will say *"Summer Tracker isn't running right now"* until then — that's correct.

The SQLite database lives in `APP_DIR/data/` and persists across restarts and
rebuilds.

---

## Part 2 — Set up Uptime Kuma

Two monitors. The first is essential; the second is a nice extra that catches a
failure the first one can't.

### 2.1 HTTP monitor (web app + database)

In Uptime Kuma → **Add New Monitor**:
- **Monitor Type:** HTTP(s)
- **Friendly Name:** Summer Tracker
- **URL:** `http://TRUENAS_IP:7823/healthz`
- **Heartbeat Interval:** 60 seconds
- **Retries:** 1–2
- (Optional) Under *Accepted Status Codes* keep `200`; or add a **Keyword** of
  `ok` to also check the body.
- Save.

It goes green when the app + database are healthy, red on a 503 or no response.

### 2.2 Push monitor (background scheduler) — optional but recommended

The HTTP check can be green while the part that *sends reminders* has quietly
died. This second monitor confirms the scheduler is actually ticking.

1. In Uptime Kuma → **Add New Monitor** → **Monitor Type: Push**.
   - Friendly Name: Summer Tracker scheduler
   - **Heartbeat Interval:** 120 seconds (so one missed minute won't false-alarm)
   - Save. Uptime Kuma shows a **Push URL** like
     `http://KUMA_IP:3001/api/push/AbC123xyz`. Copy it.
2. On TrueNAS, edit `APP_DIR/.env` and set that URL:
   ```dotenv
   UPTIME_KUMA_PUSH_URL=http://KUMA_IP:3001/api/push/AbC123xyz
   ```
3. Restart the container to pick up the change:
   ```bash
   cd /mnt/tank/summer-tracker
   docker compose up -d
   ```

Within a minute the push monitor should go green. If it ever stops receiving
pings, the scheduler died — restart the container.

### 2.3 Notifications (optional)

In Uptime Kuma → **Settings → Notifications**, add Pushover (or email) and attach
it to both monitors so you hear about an outage.

---

## Part 3 — Friendly names via Firewalla (DNS)

DNS maps a **name** to an **IP address** — it can't include a port or a path. So
`andrew.camp` can point at TrueNAS, but the kids' bookmark still carries the
`:7823/andrew` part. The trick: make the bookmark do the work and give it a nice
icon (Part 3.3). (If you want truly bare `http://andrew.camp` URLs with no port,
that needs a reverse proxy — see 3.4.)

### 3.1 Reserve TrueNAS's IP

So the address never changes under it:
- Firewalla app → **Devices** → select your **TrueNAS** box → **Reserve IP** /
  DHCP reservation. Confirm it matches `TRUENAS_IP`.

### 3.2 Add local DNS names

Goal: `andrew.camp` and `daniel.camp` both resolve to `TRUENAS_IP` on your home
network.

- Firewalla app → open your **Firewalla box** → **DNS Service** (make sure it's
  on) → look for **Local Domain / Local DNS mappings** and add entries:
  - `andrew.camp` → `TRUENAS_IP`
  - `daniel.camp` → `TRUENAS_IP`
  - (also `summer.camp` → `TRUENAS_IP` for the status/admin pages, if you like)

> The exact menu wording moves around between Firewalla app versions. You're
> looking for wherever it lets you add a **custom local hostname → IP** mapping
> (sometimes under a device's "Local Domain", sometimes a standalone "Local DNS"
> list). If you only find a single per-device "Local Domain" field, give TrueNAS
> one name there (e.g. `summer.camp`) and use that for all the bookmarks.
>
> Tip: `.camp` is a real public domain ending. It works fine locally because
> Firewalla answers first, but if you'd rather avoid any chance of confusion you
> can use `.lan` or `.home` instead (e.g. `andrew.home`).

### 3.3 Make the iPad bookmarks (the important part)

On each kid's iPad, in Safari:
1. Go to `http://andrew.camp:7823/andrew` (for Andrew's iPad).
2. Tap the **Share** button → **Add to Home Screen**.
3. Name it "Andrew's Chores" → Add.

Now there's an app-style icon that opens straight to his page — the port and path
are hidden inside the bookmark. Do the same with `daniel.camp:7823/daniel` on
Daniel's iPad, and `…:7823/status` on your and Diane's phones.

> **iPad gotcha:** if a name won't resolve, the iPad may be bypassing Firewalla's
> DNS. On the iPad check **Settings → Wi-Fi → (your network) → Configure DNS =
> Automatic**, and turn **off** iCloud **Private Relay** (Settings → Apple ID →
> iCloud → Private Relay) and any "Private DNS". Those route DNS around Firewalla.

### 3.4 (Optional) Bare URLs with a reverse proxy

If you want the kids to type just `http://andrew.camp` (no `:7823`, no `/andrew`),
put a reverse proxy in front (e.g. Nginx Proxy Manager, common on TrueNAS):
- host `andrew.camp` → proxy to `TRUENAS_IP:7823`, and rewrite `/` → `/andrew`.
- host `daniel.camp` → `TRUENAS_IP:7823`, rewrite `/` → `/daniel`.

This is extra setup and not required — the home-screen bookmarks in 3.3 give the
same kid experience without it. Say the word if you want to go this route and I'll
write the proxy config.

### 3.5 Verify DNS

From a computer on the network:
```bash
nslookup andrew.camp      # should return TRUENAS_IP
ping andrew.camp          # should reach TRUENAS_IP
```
Then open the bookmark on the iPad.

---

## Part 4 — Day-to-day

**Update to new code:**
```bash
cd /mnt/tank/summer-tracker
# pull/copy the new files, then:
docker compose up -d --build
```
The database migrates itself in place; your data is kept.

**Restart:**
```bash
docker compose restart
```

**Logs:**
```bash
docker compose logs -f
```

**Backups:** handled by **TrueNAS snapshots** of the `summer-tracker` dataset (set
up a periodic snapshot task in the TrueNAS UI → Data Protection → Periodic
Snapshot Tasks). The app does not back itself up.

---

## First-run smoke test (5 minutes)

1. `http://TRUENAS_IP:7823/healthz` → `{"status":"ok"}` ✅
2. Sign into `/admin` with your password ✅
3. In **Settings**, confirm your Pushover tokens are filled in.
4. Temporarily set `CHORE_DEBUG=1` in `.env` and `docker compose up -d`, then open
   `http://TRUENAS_IP:7823/andrew?today=2026-06-22`, finish the daily checklist →
   you should get a Pushover. Set `CHORE_DEBUG=0` again afterward.
5. Both Uptime Kuma monitors green ✅
6. Each iPad opens its bookmark to the right page ✅

You're live.
