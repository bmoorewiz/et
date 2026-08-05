# Episode Tracker — Kodi add-on & repository

A Kodi add-on that tracks and plays your **Trakt.tv "next up" episodes**,
scraping sources with **CocoScrapers** and resolving them through
**Real-Debrid**.

Full add-on documentation: [`plugin.video.episodetracker/README.md`](plugin.video.episodetracker/README.md)

## Install

Enable **Settings → System → Add-ons → Unknown sources** in Kodi first.

### Via the repository (recommended)

1. **Settings → File manager → Add source → \<None\>**
   → enter `https://bmoorewiz.github.io/et/`, name it `episodetracker`.
2. **Add-ons → Install from zip file → episodetracker →
   repository.episodetracker →** pick the zip.
3. **Add-ons → Install from repository → Episode Tracker Repository →
   Video add-ons → Episode Tracker.**

Kodi will then keep the add-on updated natively.

### Direct zip

Download `plugin.video.episodetracker-<version>.zip` from the repo root, then
**Add-ons → Install from zip file**.

## Layout

| Path | What it is |
| --- | --- |
| `plugin.video.episodetracker/` | the add-on source |
| `repository.episodetracker/` | the Kodi repository add-on source |
| `docs/` | generated repository tree, served by GitHub Pages |
| `tools/build_repo.py` | generates `docs/` |
| `build.sh` | builds the root zip **and** `docs/` |
| `plugin.video.episodetracker-<v>.zip` | root zip the built-in updater fetches |

## Releasing a new version

```bash
# bump version= in plugin.video.episodetracker/addon.xml, then:
./build.sh
git add -A && git commit -m "Release vX.Y.Z" && git push
```

`build.sh` produces both distribution paths:

- the **root zip**, which the add-on's built-in updater downloads (existing
  installs have this path baked in, so it must keep working), and
- **`docs/`**, which serves the Kodi repository.

Kodi only upgrades when the version number increases, so the bump is what
triggers clients.

> Note: `raw.githubusercontent.com` caches for 5 minutes (`max-age=300`), so a
> freshly pushed version can take that long to become visible to clients.

## GitHub Pages

The repository source URL `https://bmoorewiz.github.io/et/` requires Pages to
be enabled: **repo Settings → Pages → Source: Deploy from a branch →**
branch `claude/episode-tracker-kodi-app-qu73ll`, folder `/docs`.

The repository add-on itself points at `raw.githubusercontent.com`, so
add-on updates through the repository work whether or not Pages is enabled —
Pages is only needed for the browsable *Add source* URL.
