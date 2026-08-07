# -*- coding: utf-8 -*-
"""Collect a troubleshooting report and upload it for sharing.

Uploads go to paste.kodi.tv, Kodi's own paste service, which accepts
anonymous posts. That is deliberate: it needs no account and no token, so
nothing secret has to be stored on the device or shipped inside the add-on.
Any credential embedded in a distributed add-on is readable by everyone who
installs it.

The part that matters more than the upload: Kodi's log is written by Kodi
and every other add-on, not just this one, and it routinely contains
credentials - Real-Debrid and TorBox both put their token in requests, so
any logged URL or traceback can carry one, and lines written before the
redaction added in v1.0.8 are still in older logs. Everything is therefore
scrubbed here, by pattern and by literal match against the tokens this
install actually holds, before a single byte leaves the device.
"""

import json
import os
import platform
import sys
import time

import requests

import xbmc
import xbmcvfs

from resources.lib import control

PASTE_API = 'https://paste.kodi.tv/documents'
PASTE_VIEW = 'https://paste.kodi.tv/%s'
# Enough log to see a whole playback attempt without producing a gist nobody
# can read. Taken from the end, which is where the interesting part is.
MAX_LOG_BYTES = 512 * 1024

# Settings whose values must never appear in a report.
_SECRET_SETTINGS = (
	'trakt.token', 'trakt.refresh', 'trakt.client_secret', 'trakt.client_id',
	'rd.token', 'rd.refresh', 'rd.client_id', 'rd.client_secret',
	'updates.token', 'torbox.api_key',
	# The GitHub upload token was removed in v1.5.1, but an install that
	# saved one still has the value on disk - keep scrubbing it.
	'logs.github_token',
)
# Settings worth seeing in full when diagnosing behaviour.
_REPORTED_SETTINGS = (
	'results.autoplay', 'results.limit',
	'quality.4k', 'quality.1080p', 'quality.720p', 'quality.sd',
	'filter.min_seeders', 'rd.keep_cloud', 'rd.resolve_timeout',
	'rd.uncached_grace', 'playback.autonext',
	'playback.try.4k', 'playback.try.1080p', 'playback.try.720p',
	'playback.try.sd', 'sources.check_cache', 'sources.only_cached',
	'rd.cache_check_minutes', 'scrobble.enabled', 'scrobble.markwatched',
	'scrobble.threshold.episode', 'scrobble.threshold.movie',
	'list.aired_only', 'list.sort', 'cache.hours',
	'updates.enabled', 'updates.interval', 'updates.repo', 'updates.branch',
	'torbox.enabled', 'torbox.cached_only', 'torbox.keep_cloud',
	'debrid.priority', 'debug.enabled',
)


def _live_secrets():
	"""The literal secret values this install holds, for exact-match scrubbing.

	The regex redaction catches `token=...` shapes; this catches a token that
	turns up somewhere unexpected, such as inside a JSON blob another add-on
	logged.
	"""
	values = []
	for key in _SECRET_SETTINGS:
		value = control.setting(key, '')
		# Short values would match far too much text.
		if value and len(value) >= 8:
			values.append(value)
	return values


def scrub(text, secrets=None):
	"""Redact credentials from arbitrary log text."""
	text = control.redact(text)
	for secret in (secrets if secrets is not None else _live_secrets()):
		if secret:
			text = text.replace(secret, '<redacted>')
	return text


def _read_log_tail(path, limit=MAX_LOG_BYTES):
	try:
		if not xbmcvfs.exists(path):
			return None
		size = os.path.getsize(path)
		with open(path, 'rb') as handle:
			if size > limit:
				handle.seek(size - limit)
				handle.readline()  # drop the partial first line
			data = handle.read()
		return data.decode('utf-8', errors='replace')
	except Exception:
		control.error('could not read %s' % path)
		return None


def _environment():
	lines = []
	add = lines.append
	add('Episode Tracker diagnostics')
	add('generated       : %s UTC' % time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()))
	add('addon version   : %s' % control.addon_version)
	try:
		add('kodi version    : %s' % xbmc.getInfoLabel('System.BuildVersion'))
		add('kodi build date : %s' % xbmc.getInfoLabel('System.BuildDate'))
		add('skin            : %s' % xbmc.getInfoLabel('Skin.CurrentSkin'))
	except Exception:
		pass
	add('platform        : %s %s' % (platform.system(), platform.machine()))
	add('python          : %s' % sys.version.split()[0])
	return lines


def _accounts():
	from resources.lib import realdebrid
	from resources.lib import trakt
	lines = ['', '--- accounts (no credentials included) ---']
	lines.append('trakt authorized: %s (user: %s)'
				 % (trakt.authorized(), control.setting('trakt.user', '-') or '-'))
	lines.append('trakt app creds : %s' % trakt.has_credentials())
	lines.append('rd authorized   : %s (user: %s)'
				 % (realdebrid.authorized(), control.setting('rd.user', '-') or '-'))
	from resources.lib import debrid
	lines.append('debrid providers: %s' % (', '.join(debrid.providers()) or 'none'))
	try:
		ok, message = debrid.account_status()
		lines.append('debrid accounts : %s - %s' % ('OK' if ok else 'PROBLEM', message))
	except Exception:
		lines.append('debrid accounts : could not be checked')
	return lines


def _scrapers():
	lines = ['', '--- scrapers ---']
	try:
		from resources.lib import scrapers
		coco = scrapers._load()
		if coco is None:
			lines.append('cocoscrapers    : NOT INSTALLED OR NOT ENABLED')
			return lines
		providers = coco.sources()
		lines.append('cocoscrapers    : loaded')
		lines.append('providers enabled: %d' % len(providers))
		lines.append('  %s' % ', '.join(sorted(name for name, _cls in providers)) or '  none')
	except Exception as exc:
		lines.append('cocoscrapers    : error - %s' % exc)
	return lines


def _settings_snapshot():
	lines = ['', '--- settings ---']
	for key in _REPORTED_SETTINGS:
		lines.append('%-28s = %s' % (key, control.setting(key, '<unset>')))
	for key in _SECRET_SETTINGS:
		value = control.setting(key, '')
		lines.append('%-28s = %s' % (key, '<set, redacted>' if value else '<empty>'))
	return lines


def collect():
	"""Build the full, already-redacted report."""
	secrets = _live_secrets()
	sections = _environment() + _accounts() + _scrapers() + _settings_snapshot()

	log_path = xbmcvfs.translatePath('special://logpath/kodi.log')
	sections += ['', '--- kodi.log (last %d KB, credentials redacted) ---'
				 % (MAX_LOG_BYTES // 1024)]
	log_text = _read_log_tail(log_path)
	if log_text is None:
		sections.append('log not readable at %s' % log_path)
	else:
		sections.append(scrub(log_text, secrets))

	# Scrub the whole thing once more: the header sections quote settings and
	# account messages that could themselves carry something sensitive.
	return scrub('\n'.join(sections), secrets)


def upload(report=None):
	"""Post the report to paste.kodi.tv. Returns ``(url, error)``.

	No account or token: the service takes anonymous posts, so there is
	nothing to configure and nothing secret to ship.
	"""
	report = report if report is not None else collect()
	try:
		resp = requests.post(
			PASTE_API,
			data=report.encode('utf-8'),
			timeout=90,
			headers={'Content-Type': 'text/plain; charset=utf-8',
					 'User-Agent': 'plugin.video.episodetracker'})
	except Exception as exc:
		control.error('paste upload failed')
		return None, 'Could not reach paste.kodi.tv: %s' % exc

	if resp.status_code in (200, 201):
		try:
			key = resp.json().get('key')
		except ValueError:
			return None, 'paste.kodi.tv returned an unreadable response'
		if key:
			return PASTE_VIEW % key, None
		return None, 'paste.kodi.tv did not return a paste id'
	if resp.status_code == 413:
		return None, ('The report was too large for paste.kodi.tv. '
					  'Clear the Kodi log and reproduce the problem first.')
	if resp.status_code == 429:
		return None, 'paste.kodi.tv is rate limiting; try again in a minute.'
	return None, 'paste.kodi.tv returned HTTP %s' % resp.status_code
