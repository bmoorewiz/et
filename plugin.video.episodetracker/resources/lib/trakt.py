# -*- coding: utf-8 -*-
"""Trakt.tv integration.

Implements device (PIN) OAuth, automatic token refresh, and the
"next episodes" list derived from each tracked show's watched progress.
Trakt does not expose a single "up next" endpoint, so the list is built
from `/sync/watched/shows` combined with per-show `/shows/{id}/progress/watched`.
"""

import time
import json
import threading
from queue import Queue

import requests

from resources.lib import control
from resources.lib import cache

API_BASE = 'https://api.trakt.tv'
API_VERSION = '2'
# Public device-flow endpoints
DEVICE_CODE_URL = API_BASE + '/oauth/device/code'
DEVICE_TOKEN_URL = API_BASE + '/oauth/device/token'
TOKEN_URL = API_BASE + '/oauth/token'
REDIRECT = 'urn:ietf:wg:oauth:2.0:oob'
ACTIVATE_URL = 'https://trakt.tv/activate'

# ---------------------------------------------------------------------------
# Built-in Trakt application credentials.
#
# Sign-in uses Trakt's device flow: the add-on shows a code, the user enters
# it at trakt.tv/activate, done - exactly like Real-Debrid, with nothing to
# configure. The one difference is that Trakt has no anonymous public client
# (Real-Debrid publishes the open-source client id X245A4XAIBGVM, which
# provisions per-user credentials); Trakt's device endpoints always require an
# application's id and secret. So they are baked in here, which is what other
# Kodi add-ons do too.
#
# Fill these in ONCE and every install just activates with a code:
#   1. https://trakt.tv/oauth/applications -> New Application
#   2. Redirect uri: urn:ietf:wg:oauth:2.0:oob
#   3. Paste the Client ID and Client Secret below.
#
# A Trakt client secret carries no user data and is inherently public in a
# distributed add-on - it only identifies the application. Leaving these blank
# falls back to per-user credentials entered in Settings > Accounts.
# ---------------------------------------------------------------------------
DEFAULT_CLIENT_ID = 'IUPwuToqCD7nugy77UfWzhdXjFE6KtjIhltl8tUxisQ'
DEFAULT_CLIENT_SECRET = 'hamIeKKZGXa8nSDSZG0Hs8Sf8nzMNsXHKwwYWUVs0hI'

_TIMEOUT = 20


def client_id():
	return control.setting('trakt.client_id', DEFAULT_CLIENT_ID) or DEFAULT_CLIENT_ID


def client_secret():
	return control.setting('trakt.client_secret', DEFAULT_CLIENT_SECRET) or DEFAULT_CLIENT_SECRET


def has_credentials():
	return bool(client_id() and client_secret())


def authorized():
	return bool(control.setting('trakt.token'))


def _headers(with_auth=True):
	headers = {
		'Content-Type': 'application/json',
		'trakt-api-version': API_VERSION,
		'trakt-api-key': client_id(),
	}
	if with_auth and authorized():
		headers['Authorization'] = 'Bearer %s' % control.setting('trakt.token')
	return headers


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate():
	"""Run the Trakt device-code flow, blocking until authorized or aborted."""
	if not has_credentials():
		if control.yesno_dialog(33041, heading=control.lang(33004)):
			control.open_settings()
		return False
	try:
		resp = requests.post(DEVICE_CODE_URL, json={'client_id': client_id()},
							 headers=_headers(with_auth=False), timeout=_TIMEOUT)
		resp.raise_for_status()
		data = resp.json()
	except Exception:
		control.error('trakt device code request failed')
		control.notify(33018)
		return False

	device_code = data['device_code']
	user_code = data['user_code']
	verify_url = data.get('verification_url') or ACTIVATE_URL
	interval = int(data.get('interval', 5))
	expires_in = int(data.get('expires_in', 600))

	pd = control.progress
	pd.create(control.lang(33004),
			  control.langf(33015, verify_url) + '\n[B]%s[/B]' % user_code)
	deadline = time.time() + expires_in
	success = False
	try:
		while time.time() < deadline:
			if pd.iscanceled() or control.aborted():
				break
			remaining = int(deadline - time.time())
			percent = int(100 * (1 - remaining / float(expires_in)))
			pd.update(percent,
					  control.langf(33015, verify_url) + '\n[B]%s[/B]\n%s'
					  % (user_code, control.lang(33016)))
			control.sleep(interval * 1000)
			try:
				token_resp = requests.post(
					DEVICE_TOKEN_URL,
					json={'code': device_code, 'client_id': client_id(),
						  'client_secret': client_secret()},
					headers=_headers(with_auth=False), timeout=_TIMEOUT)
			except Exception:
				continue
			if token_resp.status_code == 200:
				_store_token(token_resp.json())
				success = True
				break
			# 400 = pending, 404 = invalid, 409 = already used, 410 = expired,
			# 418 = denied, 429 = slow down. Keep polling only on "pending".
			if token_resp.status_code not in (400, 429):
				break
	finally:
		pd.close()

	if success:
		_fetch_username()
		control.notify(33017)
		return True
	control.notify(33018)
	return False


def _store_token(data):
	control.set_setting('trakt.token', data.get('access_token', ''))
	control.set_setting('trakt.refresh', data.get('refresh_token', ''))
	created = int(data.get('created_at', time.time()))
	expires_in = int(data.get('expires_in', 7776000))
	control.set_setting('trakt.expires', str(created + expires_in))


def _refresh_token():
	refresh = control.setting('trakt.refresh')
	if not refresh or not has_credentials():
		return False
	try:
		resp = requests.post(TOKEN_URL, json={
			'refresh_token': refresh,
			'client_id': client_id(),
			'client_secret': client_secret(),
			'redirect_uri': REDIRECT,
			'grant_type': 'refresh_token',
		}, headers=_headers(with_auth=False), timeout=_TIMEOUT)
		if resp.status_code == 200:
			_store_token(resp.json())
			return True
	except Exception:
		control.error('trakt token refresh failed')
	return False


def _maybe_refresh():
	try:
		expires = int(control.setting('trakt.expires', '0') or '0')
	except ValueError:
		expires = 0
	# refresh a day before expiry
	if expires and expires - time.time() < 86400:
		_refresh_token()


def revoke():
	for key in ('trakt.token', 'trakt.refresh', 'trakt.expires', 'trakt.user'):
		control.set_setting(key, '')


def _fetch_username():
	try:
		data = _request('GET', '/users/settings')
		if data:
			control.set_setting('trakt.user', data.get('user', {}).get('username', ''))
	except Exception:
		pass


# ---------------------------------------------------------------------------
# Request helper
# ---------------------------------------------------------------------------

def _request(method, path, params=None, payload=None, auth=True, retry=True):
	if auth:
		_maybe_refresh()
	url = API_BASE + path
	try:
		resp = requests.request(method, url, params=params,
								json=payload, headers=_headers(with_auth=auth),
								timeout=_TIMEOUT)
	except Exception:
		control.error('trakt request failed: %s' % path)
		return None
	if resp.status_code == 401 and auth and retry:
		# token likely expired - refresh once and retry
		if _refresh_token():
			return _request(method, path, params, payload, auth, retry=False)
		return None
	if resp.status_code not in (200, 201, 204):
		control.debug('trakt %s %s -> %s' % (method, path, resp.status_code))
		return None
	if resp.status_code == 204 or not resp.content:
		return True
	try:
		return resp.json()
	except ValueError:
		return None


# ---------------------------------------------------------------------------
# Next episodes
# ---------------------------------------------------------------------------

def _watched_shows():
	return _request('GET', '/sync/watched/shows', params={'extended': 'noseasons'}) or []


def _show_progress(trakt_id):
	return _request('GET', '/shows/%s/progress/watched' % trakt_id,
					params={'hidden': 'false', 'specials': 'false',
							'count_specials': 'false', 'extended': 'full'})


def _build_entry(show, progress):
	next_ep = progress.get('next_episode') if progress else None
	if not next_ep:
		return None
	show_ids = show.get('ids', {})
	ep_ids = next_ep.get('ids', {})
	return {
		'show_title': show.get('title', ''),
		'show_year': show.get('year'),
		'show_trakt': show_ids.get('trakt'),
		'show_slug': show_ids.get('slug'),
		'show_imdb': show_ids.get('imdb'),
		'show_tvdb': show_ids.get('tvdb'),
		'show_tmdb': show_ids.get('tmdb'),
		'season': next_ep.get('season'),
		'episode': next_ep.get('number'),
		'ep_title': next_ep.get('title') or 'Episode %s' % next_ep.get('number'),
		'ep_trakt': ep_ids.get('trakt'),
		'ep_imdb': ep_ids.get('imdb'),
		'ep_tvdb': ep_ids.get('tvdb'),
		'ep_tmdb': ep_ids.get('tmdb'),
		'first_aired': next_ep.get('first_aired'),
		'runtime': next_ep.get('runtime'),
		'plot': next_ep.get('overview', ''),
		'last_watched': show.get('last_watched_at', ''),
		'aired_count': (progress or {}).get('aired', 0),
		'completed_count': (progress or {}).get('completed', 0),
	}


def next_episodes(refresh=False):
	"""Return the list of next-up episode entries across tracked shows."""
	cache_key = 'trakt_next_%s' % control.setting('trakt.user', 'me')
	if not refresh:
		cached = cache.get(cache_key)
		if cached is not None:
			return _post_filter(cached)

	shows = _watched_shows()
	if not shows:
		return []

	# Fetch each show's progress concurrently (bounded worker pool).
	results = []
	results_lock = threading.Lock()
	work = Queue()
	for item in shows:
		work.put(item)

	def worker():
		while True:
			try:
				item = work.get_nowait()
			except Exception:
				return
			try:
				show = item.get('show', {})
				trakt_id = show.get('ids', {}).get('trakt')
				if trakt_id:
					progress = _show_progress(trakt_id)
					entry = _build_entry(dict(show, last_watched_at=item.get('last_watched_at')),
										 progress)
					if entry:
						with results_lock:
							results.append(entry)
			except Exception:
				control.error('progress worker failed')
			finally:
				work.task_done()

	threads = [threading.Thread(target=worker) for _ in range(min(10, len(shows)))]
	for t in threads:
		t.start()
	for t in threads:
		t.join()

	cache.set(cache_key, results, hours=max(1, control.get_int('cache.hours', 6)))
	return _post_filter(results)


def _post_filter(entries):
	entries = list(entries)
	if control.get_bool('list.aired_only', True):
		now = time.time()
		filtered = []
		for e in entries:
			aired = _parse_iso(e.get('first_aired'))
			if aired is None or aired <= now:
				filtered.append(e)
		entries = filtered

	sort_mode = control.get_int('list.sort', 0)
	if sort_mode == 0:  # last watched (most recent first)
		entries.sort(key=lambda e: e.get('last_watched') or '', reverse=True)
	elif sort_mode == 1:  # recently aired first
		entries.sort(key=lambda e: e.get('first_aired') or '', reverse=True)
	else:  # alphabetical
		entries.sort(key=lambda e: (e.get('show_title') or '').lower())
	return entries


def _parse_iso(value):
	if not value:
		return None
	try:
		# Trakt returns e.g. 2024-01-15T00:00:00.000Z
		value = value.replace('Z', '+0000')
		for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
			try:
				import datetime
				return datetime.datetime.strptime(value, fmt).timestamp()
			except ValueError:
				continue
	except Exception:
		return None
	return None


# ---------------------------------------------------------------------------
# Scrobble / history
# ---------------------------------------------------------------------------

def _episode_ref(entry):
	ids = {}
	for src, key in (('ep_trakt', 'trakt'), ('ep_imdb', 'imdb'),
					 ('ep_tvdb', 'tvdb'), ('ep_tmdb', 'tmdb')):
		if entry.get(src):
			ids[key] = entry[src]
	return {'ids': ids}


def scrobble(entry, action, progress_percent):
	"""Send a scrobble start/pause/stop event for the given episode entry."""
	if not control.get_bool('scrobble.enabled', True) or not authorized():
		return
	payload = {'episode': _episode_ref(entry), 'progress': float(progress_percent)}
	_request('POST', '/scrobble/%s' % action, payload=payload)


def add_to_history(entry):
	"""Mark the episode watched by adding it to the Trakt history."""
	if not authorized():
		return False
	payload = {'episodes': [_episode_ref(entry)]}
	result = _request('POST', '/sync/history', payload=payload)
	# invalidate the cached next-up list so it recomputes
	cache.delete('trakt_next_%s' % control.setting('trakt.user', 'me'))
	return bool(result)
