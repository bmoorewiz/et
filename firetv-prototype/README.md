# Episode Tracker — Fire TV prototype

A spike to answer the one question that decides whether a standalone Fire TV
app is viable:

> **Can the CocoScrapers providers run on Android, with no Kodi installed?**

**Yes.** Verified against the live providers, off-device:

```
5. scrape_episode() - Breaking Bad S01E01
    18 providers ran, 32 unique sources
      4K        6.82GB  S:114   torrentdownload  Breaking-Bad-S01E01-Pilot-2160p-NF-WEB-DL-DD
6. scrape_movie() - Inception
    18 providers ran, 227 unique sources
      4K       79.60GB  S:56    kickass2         Inception.2010.2160p.BluRay.REMUX.HEVC.DTS-H

PASS  episode sources: 32, movie sources: 227
```

## Why this was the risk

Everything else in the Kodi add-on is plain HTTP and JSON — Trakt, Real-Debrid,
ranking, caching — and ports to any language mechanically. The scrapers are the
exception: ~20 provider modules written in Python against Kodi's API, which
break whenever a source site changes. Rewriting them natively means owning that
churn forever.

It turns out CocoScrapers touches only **four** Kodi modules — `xbmc`,
`xbmcaddon`, `xbmcgui`, `xbmcvfs` — and one non-stdlib dependency, `requests`.
So instead of rewriting anything, this prototype supplies those four modules
itself and runs the real CocoScrapers unchanged.

## How it fits together

```
app/src/main/python/
  kodi_shim/           stand-ins for xbmc, xbmcaddon, xbmcgui, xbmcvfs, xbmcplugin
  cocoscrapers/        the real module, fetched (not vendored) - see tools/
  et_scrape.py         the API Kotlin calls; returns JSON
app/src/main/java/...  MainActivity.kt - starts Python, scrapes, prints
tests/run_headless.py  runs the identical Python core on a desktop
```

`kodi_shim` is not a fake: `xbmcaddon` persists settings to JSON so provider
choices survive a restart, and `xbmcvfs` is backed by the real filesystem.
CocoScrapers stores its own caches through these.

## Run the proof without an Android device

```bash
./tools/fetch_cocoscrapers.sh     # downloads the current CocoScrapers release
python3 tests/run_headless.py
```

This exercises the exact code path the app uses. If it passes, the only
remaining unknown is Chaquopy packaging, not whether the approach works.

## Build the APK

Needs Android Studio (or the Android SDK + `ANDROID_HOME`). It was **not**
compiled here — this environment has Java and Gradle but no Android SDK, so
the Kotlin/Gradle side is unverified boilerplate. Treat it as a starting point.

```bash
./tools/fetch_cocoscrapers.sh
./gradlew assembleDebug
adb connect <firestick-ip>:5555
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb logcat -s EpisodeTrackerProto
```

Enable **Settings → My Fire TV → Developer Options → ADB debugging** first.

## One finding worth keeping

Episode scrapes need the **episode title**. Passing an empty `title` silently
drops results to zero:

```
title='Pilot' -> 27 sources
title=''      ->  0 sources
```

The Kodi add-on already passes it; the first cut of this prototype did not, and
returned nothing until that was traced. `scrape_episode()` now takes it
explicitly and documents why.

## What this prototype is not

No UI, no Trakt, no Real-Debrid, no playback. Those are the *known* parts. This
answers only the unknown one. Realistic remaining work for feature parity is
roughly 3–4 months solo, and the honest regressions to expect on Fire TV are
DTS-HD MA / TrueHD audio, AV1 on older hardware, and PGS subtitles — all things
Kodi's player handles better than ExoPlayer.

## Licensing

CocoScrapers is GPL, which is why it is fetched rather than vendored. Shipping
it inside an APK makes that APK GPL too — consistent with the add-on, which is
already GPL-3.0-or-later.
