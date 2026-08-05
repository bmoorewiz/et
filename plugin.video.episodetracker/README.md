# Episode Tracker

A focused Kodi add-on that does one thing well: it tracks and plays your
**Trakt.tv "next up" episodes**. For every show you are progressing through on
Trakt, it surfaces the next unwatched (aired) episode, scrapes torrent sources
with the **CocoScrapers** module, resolves a cached link through
**Real-Debrid**, plays it, and scrobbles your progress back to Trakt so the
list advances automatically.

It works like Fen / Umbrella / The Crew, but deliberately scoped down to the
single "next episodes" workflow.

## Requirements

- Kodi 19 (Matrix), 20 (Nexus), or 21 (Omega) — Python 3.
- `script.module.cocoscrapers` installed and enabled, with at least one torrent
  provider turned on in its settings.
- A **Trakt.tv** account.
- A **Real-Debrid** subscription.

## Installation

1. Install the CocoScrapers repository/module
   (`repository.cocoscrapers` → `script.module.cocoscrapers`).
2. Install this add-on from zip: zip the `plugin.video.episodetracker` folder
   and use **Add-ons → Install from zip file**, or drop the folder into your
   Kodi `addons` directory.
3. Open the add-on. It will prompt you to authorize Trakt and Real-Debrid.

## Setup

### Real-Debrid
Real-Debrid works out of the box via the open-source device flow — no
application registration needed. Go to **Settings → Accounts → Authorize
Real-Debrid**, open the shown URL, and enter the code.

### Trakt.tv
Trakt's device authentication requires your own free Trakt application:

1. Go to <https://trakt.tv/oauth/applications> and create a new application.
2. Set the **Redirect URI** to `urn:ietf:wg:oauth:2.0:oob`.
3. Copy the **Client ID** and **Client Secret** into
   **Settings → Accounts → Trakt app Client ID / Client Secret**.
4. Use **Authorize Trakt**, open the shown URL, and enter the code.

(If you fork this add-on for personal use you can hard-code your own
credentials in `resources/lib/trakt.py` via `DEFAULT_CLIENT_ID` /
`DEFAULT_CLIENT_SECRET` to skip step 3.)

## Usage

- **Next Episodes** — the next aired, unwatched episode for each show you're
  tracking on Trakt. Selecting one scrapes sources and lists them ranked by
  quality and seeders; pick one to play. Enable **Auto-play best source** in
  settings to skip the source list.
- Playback scrobbles to Trakt automatically and marks the episode watched at
  the end, so the show's next episode appears on your next visit.
- Right-click an episode for **Mark as watched on Trakt** and **Refresh**.

## Updating

The add-on can update itself. It checks the configured GitHub repo/branch for
a newer `addon.xml` version, and if it finds one:

- an **"Update available: vX.Y.Z"** entry appears at the top of the main menu,
- selecting it asks for confirmation, then downloads and installs the matching
  `plugin.video.episodetracker-X.Y.Z.zip`,
- Kodi is asked to reload add-ons, and you're offered a restart to finish.

Checks run on a background thread (default every 24h), so menus never stall on
the network. **Settings → Updates → Check for updates now** forces an
immediate check.

Updates are read from `raw.githubusercontent.com`, which needs no credentials
because `bmoorewiz/et` is a **public** repository — nothing to configure.

> If you ever make the repository private, raw URLs stop working and update
> checks will fail silently (they're logged, not shouted). In that case set
> *Settings → Updates → Access token*; the add-on switches to the GitHub
> contents API, which can read private repos. Note this stores a GitHub token
> in plain text in Kodi's settings — use a fine-grained token limited to
> read-only *Contents* on this one repository, never a broad classic token.

Downloaded zips are validated before extraction: archives containing absolute
paths, `..` traversal, or files outside the add-on folder are rejected.

To publish an update: bump `version=` in `addon.xml`, run `./build.sh`, and
commit both the source and the new zip to the branch clients track.

## Settings overview

- **Accounts** — Trakt and Real-Debrid authorization.
- **Playback** — auto-play, quality filters, minimum seeders, scrobble options.
- **Lists** — aired-only filter, sort order, source cache duration.
- **Updates** — auto-check toggle, interval, repo/branch, optional token,
  manual check.
- **Tools** — clear source cache, open CocoScrapers settings, verbose logging.

## Notes / disclaimer

This add-on hosts no content. It is a front-end for services you independently
subscribe to (Trakt.tv, Real-Debrid) and third-party scraper modules you
independently install. You are responsible for how you use it.

Licensed under GPL-3.0-or-later.
