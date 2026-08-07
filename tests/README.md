# Episode Tracker tests

```
python3 tests/run.py                      # everything (~1.5s)
python3 tests/run.py trakt                # only matching tests
python3 tests/run.py --exclude packaging  # everything but those
python3 tests/run.py -v                   # per-test output
```

No dependencies beyond the standard library — the same constraint the
add-on itself works under. `./build.sh` runs the suite either side of a
build.

## Why these tests exist

Every test class here is anchored to something that actually went wrong, or
to a Kodi behaviour that is easy to get wrong and impossible to notice until
it is on a Fire Stick:

| File | Protects |
| --- | --- |
| `test_mediafiles.py` | File selection. "Torrent contains no playable video file" turned away playable torrents: the check knew 14 extensions and looked at one name field. |
| `test_scrapers.py` | The data handed to CocoScrapers. Trakt returns `year` as an int; `check_title()` does `title.replace(year, '')`, so every release was discarded and it looked exactly like "no sources found". |
| `test_realdebrid.py` | The resolve loop. A freshly added magnet is never immediately `downloaded`; reading the status once instead of polling made every source fail. Also that Real-Debrid's own error text reaches the user instead of a guess. |
| `test_torbox.py` | The second provider, and the cache check that Real-Debrid no longer usefully offers. |
| `test_player.py` | The per-tier fallback budget (5 × 4K, 5 × 1080p, 10 × 720p) and the resume offer. |
| `test_scrobbler.py` | The service. Kodi destroys the plugin process shortly after `setResolvedUrl()` and force-kills its threads, so watched-marking never happened; the window-property handover is what fixes it. |
| `test_trakt.py` | Next-up construction, scrobbles, playback position, hidden shows. |
| `test_router.py` | Menus, context items, quality colouring, and that every action reaches a handler. |
| `test_control.py` | Settings access, list items, and credential redaction — a Real-Debrid token once reached the Kodi log, and Kodi logs get pasted into forum threads. |
| `test_diagnostics.py` | The log upload. Includes a scan that fails if any credential-shaped setting is missing from the scrub list, which is exactly how the TorBox key nearly went to a public paste. |
| `test_packaging.py` | That the shipped artefacts agree. Running the build from the wrong directory shipped a zip named one version containing another — twice. |
| `test_catalogue.py` | That every string id and setting id the code references actually exists, and that format placeholders match their arguments. |
| `test_cache.py` | The sqlite cache, the debrid dispatcher, and the updater's version maths. |

## The Kodi stubs

`kodistubs/` stands in for `xbmc`, `xbmcaddon`, `xbmcgui`, `xbmcplugin` and
`xbmcvfs`. They are written to be **faithful rather than convenient** —
every shortcut taken in a stub becomes a test that passes here and fails on
a real install. Earlier throwaway versions of these stubs produced exactly
that: an `xbmcvfs.exists` backed by a dictionary, per-instance window
properties, and a strings catalogue that dropped format placeholders, each
of which hid a real defect or invented a fake one.

So the stubs deliberately copy Kodi's awkward behaviour:

- an unset setting reads back as its `settings.xml` **default**, not `''`
- `xbmcaddon.Addon('not.installed')` **raises** — that is how the add-on
  detects CocoScrapers is missing
- window properties belong to the **window id**, not the Python object,
  which is the entire basis of the plugin → service handover
- `getVideoInfoTag()` exists and its typed setters **reject** a string where
  they want an int, so the casting in `control._apply_info_tag` stays honest
- `setInfo` **raises** on a `None` value, so the stripping in
  `add_directory_item` stays honest
- localized strings are parsed from the real `strings.po`, placeholders
  intact, falling back to `msgid` when `msgstr` is empty as Kodi does

## Network and time

`support.block_network()` replaces `socket.connect`, so a test that forgets
to install a fake transport fails loudly instead of quietly hitting a live
API. Every provider test swaps the module's `requests` session for
`support.FakeHTTP`, which routes on `(METHOD, path)` and records what was
sent.

Real-Debrid and TorBox both poll with real deadlines. `support.FakeClock`
replaces the module's `time` and Kodi's sleep, so a test can watch a 15
second timeout elapse without waiting for it — the whole suite runs in
about a second and a half.
