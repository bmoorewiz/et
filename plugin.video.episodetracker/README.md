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

Enable **Settings → System → Add-ons → Unknown sources** first.

### Option A — via the repository (recommended)

1. **Settings → File manager → Add source → \<None\>**, enter
   `https://bmoorewiz.github.io/et/` and name it `episodetracker`.
2. **Add-ons → Install from zip file → episodetracker →
   repository.episodetracker →** pick the zip.
3. **Add-ons → Install from repository → Episode Tracker Repository →
   Video add-ons → Episode Tracker → Install.**

Kodi then updates the add-on natively, in addition to its own built-in
updater.

### Option B — direct zip

Download and install
`plugin.video.episodetracker-<version>.zip` from the repo root via
**Add-ons → Install from zip file**.

### Then

Install the CocoScrapers module (`repository.cocoscrapers` →
`script.module.cocoscrapers`) and enable at least one torrent provider, then
open Episode Tracker and authorize Trakt and Real-Debrid.

## Setup

### Real-Debrid
Real-Debrid works out of the box via the open-source device flow — no
application registration needed. Go to **Settings → Accounts → Authorize
Real-Debrid**, open the shown URL, and enter the code.

### Trakt.tv
Signing in works exactly like Real-Debrid: **Settings → Accounts → Authorize
Trakt** shows a code, you enter it at <https://trakt.tv/activate>, and that's
it. Nothing to configure.

That requires the build to carry Trakt application credentials. Unlike
Real-Debrid — which publishes an anonymous open-source client id
(`X245A4XAIBGVM`) that provisions per-user credentials — Trakt's device
endpoints always require an application's id and secret, so they have to be
baked into the add-on.

If **Authorize Trakt** tells you the build has no credentials, add them once:

1. <https://trakt.tv/oauth/applications> → **New Application**.
2. Set the **Redirect URI** to `urn:ietf:wg:oauth:2.0:oob`.
3. Either paste the **Client ID** / **Client Secret** into
   `DEFAULT_CLIENT_ID` / `DEFAULT_CLIENT_SECRET` at the top of
   `resources/lib/trakt.py` — every install then just activates with a code —
   or, for a one-off box, into **Settings → Accounts**.

A Trakt client secret carries no user data and is inherently public in a
distributed add-on; it only identifies the application.

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
