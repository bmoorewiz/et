#!/usr/bin/env python3
"""Run the app's Python core off-device, exactly as Android would call it.

This is the point of the prototype: if this passes on a desktop, the only
remaining unknown for Fire TV is the Chaquopy packaging, not whether
CocoScrapers can run outside Kodi.

    python3 tests/run_headless.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.join(os.path.dirname(HERE), 'app', 'src', 'main', 'python')
sys.path.insert(0, PY_ROOT)

import et_scrape  # noqa: E402


def main():
    data_dir = os.path.join(tempfile.gettempdir(), 'et_firetv_proto')
    coco = os.path.join(PY_ROOT, 'cocoscrapers')
    if not os.path.isdir(coco):
        print('CocoScrapers missing - run tools/fetch_cocoscrapers.sh first')
        return 1

    print('1. configure()')
    print('   ', et_scrape.configure(data_dir, PY_ROOT))

    print('2. list_providers()')
    providers = json.loads(et_scrape.list_providers())
    print('    %d providers on disk, %d enabled'
          % (len(providers), sum(1 for p in providers if p['enabled'])))

    print('3. set_providers() - enable every torrent provider')
    names = [p['name'] for p in providers if p['group'] == 'torrents']
    enabled = json.loads(et_scrape.set_providers(names))
    print('    enabled %d' % len(enabled['enabled']))

    print('4. settings persist across a fresh Addon instance')
    import xbmcaddon
    assert xbmcaddon.Addon().getSetting('provider.%s' % names[0]) == 'true'
    print('    provider.%s == true  OK' % names[0])

    print('5. scrape_episode() - Breaking Bad S01E01')
    result = json.loads(et_scrape.scrape_episode(
        'tt0903747', 1, 1, 'Breaking Bad', 'Pilot', 2008,
        tvdb=81189, tmdb=1396, premiered='2008-01-21'))
    _report(result)
    episode_count = len(result['sources'])

    print('6. scrape_movie() - Inception')
    result = json.loads(et_scrape.scrape_movie('tt1375666', 'Inception', 2010))
    _report(result)
    movie_count = len(result['sources'])

    ok = episode_count > 0 and movie_count > 0
    print('\n%s  episode sources: %d, movie sources: %d'
          % ('PASS' if ok else 'FAIL', episode_count, movie_count))
    return 0 if ok else 1


def _report(result):
    sources = result['sources']
    print('    %d providers ran, %d unique sources' % (result['providers'], len(sources)))
    if result.get('error'):
        print('    error:', result['error'])
    for item in sources[:5]:
        print('      %-6s %7.2fGB  S:%-5s %-16s %s'
              % (item['quality'], item['size'], item['seeders'],
                 item['provider'], item['name'][:44]))
    # every source must be resolvable: a magnet or an info hash
    for item in sources:
        assert item['hash'] or item['url'].startswith('magnet:'), item


if __name__ == '__main__':
    sys.exit(main())
