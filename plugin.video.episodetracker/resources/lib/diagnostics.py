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
import xml.etree.ElementTree as ElementTree

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
	'rd.cache_check_minutes', 'sources.packs', 'sources.plausible_size',
	'playback.resume', 'playback.playnext', 'playback.show_provider',
	'playback.label_provider', 'scrobble.enabled', 'scrobble.markwatched',
	'scrobble.threshold.episode', 'scrobble.threshold.movie',
	'list.aired_only', 'list.sort', 'list.show_progress',
	'list.inprogress_first', 'cache.hours',
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


def _declared_settings():
	"""Setting ids in the shipped schema, or None if it will not parse."""
	path = os.path.join(control.addon_path, 'resources', 'settings.xml')
	try:
		root = ElementTree.parse(path).getroot()
	except Exception:
		return None
	return {element.get('id') for element in root.iter('setting')
			if element.get('id')}


def _stored_settings():
	"""Setting ids that have a value in the add-on's own data file.

	Read off disk rather than through the settings API, because the two
	disagreeing is itself the diagnosis. Kodi only hands a stored value back
	if it could also load resources/settings.xml, so a value that is on disk
	but reads back empty means the schema did not load - and when that
	happens *every* setting reads empty at once, which looks exactly like
	being signed out of everything while the settings dialog renders blank.
	Nothing else produces that pair of symptoms, and nothing in a report
	built only from the API can tell it apart from genuinely signing out.
	"""
	path = os.path.join(control.profile_path, 'settings.xml')
	try:
		root = ElementTree.parse(path).getroot()
	except Exception:
		return None
	stored = {}
	for element in root.iter('setting'):
		key = element.get('id')
		if not key:
			continue
		# Kodi 19+ writes the value as element text; older builds used a
		# value attribute. Reports come from both.
		value = element.get('value')
		stored[key] = (element.text or '') if value is None else value
	return stored


def storage_problem():
	"""One line describing why settings are not working, or None if they are.

	Cheap enough to call while building a menu: two small XML files and no
	network. Kept separate from the full report because the failure it
	looks for is the one that hides every other symptom - when settings do
	not load or do not save, the add-on presents as signed out of
	everything and nothing on screen says why.
	"""
	try:
		if _declared_settings() is None:
			return control.lang(33105)
		if unreadable_settings():
			return control.lang(33106)
		if not profile_writable():
			return control.lang(33107)
	except Exception:
		control.error('settings health check failed')
	return None


def profile_writable():
	"""Can the add-on write to its own data directory at all?

	Tested with a real file rather than by writing a setting. Kodi's
	getSetting() reads its in-memory copy, so a setting written to a
	read-only or full profile still reads back correctly and proves
	nothing - which is the trap this check exists to avoid. A device out
	of storage is the common cause, and it takes every credential with it
	the next time Kodi rewrites the file.
	"""
	path = os.path.join(control.profile_path, '.write-test')
	try:
		control.make_profile()
		with open(path, 'w', encoding='utf-8') as handle:
			handle.write('x')
		os.remove(path)
		return True
	except Exception:
		control.error('profile directory is not writable')
		return False


def unreadable_settings():
	"""Declared settings with a value on disk that reads back empty."""
	declared = _declared_settings()
	stored = _stored_settings()
	if declared is None or stored is None:
		return []
	return sorted(key for key, value in stored.items()
				  if value and key in declared and not control.setting(key, ''))


def _schema_health():
	"""Whether the stored settings are actually reaching the add-on."""
	declared = _declared_settings()
	if declared is None:
		return ['', '--- settings ---',
				'schema               : resources/settings.xml WILL NOT PARSE.',
				'                       Every setting reads back empty, so the '
				'add-on looks signed out',
				'                       of everything and its settings dialog '
				'has nothing to show.']

	stored = _stored_settings()
	if stored is None:
		return ['', '--- settings ---',
				'schema               : %d settings declared, nothing stored yet'
				% len(declared)]

	# Only ids the current schema still declares: a value left behind by a
	# setting since removed reads back empty because it no longer exists,
	# which is correct rather than a fault.
	unreadable = sorted(key for key, value in stored.items()
						if value and key in declared
						and not control.setting(key, ''))
	if not unreadable:
		return ['', '--- settings ---',
				'schema               : %d declared, %d stored, all readable'
				% (len(declared), len(stored))]
	return ['', '--- settings ---',
			'schema               : %d of %d stored values ARE NOT READABLE.'
			% (len(unreadable), len(stored)),
			'                       resources/settings.xml did not load, so '
			'these have a value on',
			'                       disk that the add-on reads back as empty:',
			'                       %s' % ', '.join(unreadable)]


def _settings_snapshot():
	lines = _schema_health()
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
