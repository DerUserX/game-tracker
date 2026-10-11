# Game Release Tracker

Tray app for upcoming game releases: countdown, IGDB covers, release reminders, waitlist,
trophy alerts via Exophase, and a phone view (PWA) synced via a GitHub Gist.
It updates itself automatically from the GitHub releases of this repo.

## One-time setup on the target PC

1. Create a folder, e.g. `C:\Tools\GameTracker\`.
2. Download `GameTracker.exe` from the **latest release** of this repo:
   https://github.com/DerUserX/game-tracker/releases/latest
3. Put these files from the old installation **next to the exe**:
   `games.json`, `config.json`, `waitlist.json`, `notifications.json`.
   **`config.json` must contain the credentials** (`igdb_client_id`, `igdb_client_secret`, `github_token`).
   If one is missing, the app shows a notice at startup.
4. Start `GameTracker.exe`. The icon appears in the tray (possibly under the "^" arrow).
5. Autostart: `Win+R` → `shell:startup` → put a shortcut to `GameTracker.exe` there.

Next to the exe, the app creates `covers\` (cover cache) and `gametracker.log` (log file for troubleshooting).

## Updates

Nothing to do: the app checks at startup and every 6 hours for a newer release,
downloads it, restarts itself briefly and reports "Updated to version X".
If a dialog is open, it waits until you close it.

How a new version gets made: a push to `main` that touches app files (`tracker/`, `gametracker.pyw`,
`requirements.txt`, `GameTracker.spec`, `icon.ico`) automatically builds a new exe and release
(GitHub Actions, ~3–5 minutes).

## Phone view (PWA)

https://deruserx.github.io/game-tracker/ – served from the `docs/` folder via GitHub Pages
and updated automatically about 1 minute after every push.
The game list comes from the Gist that the desktop app updates on every change.

## Trophy alerts

Once a day, the app looks up every PlayStation game in the tracker on Exophase. Exophase usually
discovers new trophy lists before release. You get an alert when a list appears for the first time
or new trophies get added (DLC). No token needed.
In the edit dialog: **Check now**, **Wrong match** (ignores the hit), **↗** opens the list on Exophase.

## Development

```
pip install -r requirements.txt
python gametracker.pyw --show
```

- `GAMETRACKER_DATA_DIR=<folder>`: use test data from a different folder
- `GAMETRACKER_NO_CLOUD=1`: no upload to the Gist (otherwise every test change ends up on your phone)
- `GAMETRACKER_UPDATE_URL=<path/URL>`: alternative release info (for testing the self-update)
- Build the exe locally: `build.bat` → `release\GameTracker.exe` (version `0.0.0-dev`, never updates itself)

Personal data and `config.json` are in `.gitignore` and must never end up in the repo.

| File | Purpose |
|---|---|
| `gametracker.pyw` | Entry point (logging, single instance) |
| `tracker/config.py` | Constants; reads credentials from `config.json` |
| `tracker/storage.py` | Load/save JSON (thread-safe, atomic) |
| `tracker/igdb.py` | IGDB search, ID lookup, name matching |
| `tracker/updater.py` | Daily update, reminders, waitlist, cover backfill |
| `tracker/trophies.py` | Exophase trophy monitoring |
| `tracker/selfupdate.py` | Self-update from GitHub releases |
| `tracker/cloud.py` | Gist upload |
| `tracker/covers.py` | Cover download/cache |
| `tracker/ui/` | CustomTkinter UI (`app.py` = a single Tk root plus a task queue) |
| `docs/` | PWA (GitHub Pages) |
| `.github/workflows/release.yml` | Automatic exe build + release |
