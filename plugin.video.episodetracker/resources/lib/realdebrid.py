# -*- coding: utf-8 -*-
"""Real-Debrid integration.

Uses the open-source device-code OAuth flow (public client id
``X245A4XAIBGVM``), which provisions per-user client credentials and needs no
pre-registered application. Provides magnet resolution to a directly playable
link and account/token management.
"""

import re
import time
import threading

import requests
from requests.adapters import HTTPAdapter

from resources.lib import control
from resources.lib import cache

REST_BASE = 'https://api.real-debrid.com/rest/1.0/'
OAUTH_BASE = 'https://api.real-debrid.com/oauth/v2/'
OPEN_SOURCE_CLIENT_ID = 'X245A4XAIBGVM'
GRANT_TYPE = 'http://oauth.net/grant_type/device/1.0'

VIDEO_EXTENSIONS = (
	'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.mpg', '.mpeg', '.wmv',
	'.flv', '.ts', '.m2ts', '.webm', '.ogv', '.iso',
)

# Real-Debrid rate limits and disallows too many simultaneous magnet resolves.
_resolve_semaphore = threading.Semaphore(3)

_session = requests.Session()
_session.mount('https://api.real-debrid.com', HTTPAdapter(pool_maxsize=20))


def authorized():
	return bool(control.setting('rd.token'))


def _token():
	return control.setting('rd.token')


# ---------------------------------------------------------------------------
# HTTP helpers (Real-Debrid takes the token as an auth_token query param)
# ---------------------------------------------------------------------------

def _get(path, retry=True):
	if not _token():
		return None
	url = REST_BASE + path
	url += ('&' if '?' in url else '?') + 'auth_token=%s' % _token()
	try:
		resp = _session.get(url, timeout=45)
		data = resp.json() if resp.content else None
	except ValueError:
		return None
	except Exception:
		control.error('rd get failed: %s' % path)
		return None
	if _is_bad_token(data) and retry:
		if refresh_token():
			return _get(path, retry=False)
	return data


def _post(path, payload, retry=True):
	if not _token():
		return None
	url = REST_BASE + path
	url += ('&' if '?' in url else '?') + 'auth_token=%s' % _token()
	try:
		resp = _session.post(url, data=payload, timeout=20)
		if resp.status_code == 204:
			return {}
		data = resp.json() if resp.content else {}
	except ValueError:
		return {}
	except Exception:
		control.error('rd post failed: %s' % path)
		return None
	if _is_bad_token(data) and retry:
		if refresh_token():
			return _post(path, payload, retry=False)
	return data


def _delete(path):
	if not _token():
		return None
	url = REST_BASE + path
	url += ('&' if '?' in url else '?') + 'auth_token=%s' % _token()
	try:
		return _session.delete(url, timeout=20)
	except Exception:
		control.error('rd delete failed: %s' % path)
		return None


def _is_bad_token(data):
	return isinstance(data, dict) and data.get('error_code') in (8,) or \
		(isinstance(data, dict) and data.get('error') == 'bad_token')


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate():
	"""Run the RD device-code flow, blocking until authorized or aborted."""
	try:
		resp = _session.get(
			OAUTH_BASE + 'device/code',
			params={'client_id': OPEN_SOURCE_CLIENT_ID, 'new_credentials': 'yes'},
			timeout=20).json()
	except Exception:
		control.error('rd device code failed')
		control.notify(33018)
		return False

	device_code = resp['device_code']
	user_code = resp['user_code']
	verify_url = resp.get('verification_url', 'https://real-debrid.com/device')
	interval = int(resp.get('interval', 5))
	expires_in = int(resp.get('expires_in', 600))

	pd = control.progress
	pd.create(control.lang(33005),
			  control.langf(33015, verify_url) + '\n[B]%s[/B]' % user_code)

	deadline = time.time() + expires_in
	secret = None
	client_id = None
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
				cred = _session.get(
					OAUTH_BASE + 'device/credentials',
					params={'client_id': OPEN_SOURCE_CLIENT_ID, 'code': device_code},
					timeout=20)
				if cred.status_code != 200 or 'error' in cred.text:
					continue
				cred = cred.json()
				client_id = cred['client_id']
				secret = cred['client_secret']
				break
			except Exception:
				continue
	finally:
		pd.close()

	if not secret:
		control.notify(33018)
		return False

	if _exchange_token(client_id, secret, device_code):
		_fetch_username()
		control.notify(33017)
		return True
	control.notify(33018)
	return False


def _exchange_token(client_id, secret, code):
	try:
		resp = _session.post(OAUTH_BASE + 'token', data={
			'client_id': client_id,
			'client_secret': secret,
			'code': code,
			'grant_type': GRANT_TYPE,
		}, timeout=20).json()
		if 'access_token' not in resp:
			control.debug('rd token exchange: %s' % resp.get('error'))
			return False
		control.set_setting('rd.client_id', client_id)
		control.set_setting('rd.client_secret', secret)
		control.set_setting('rd.token', resp['access_token'])
		control.set_setting('rd.refresh', resp.get('refresh_token', ''))
		control.set_setting('rd.expires',
							str(int(time.time()) + int(resp.get('expires_in', 3600))))
		return True
	except Exception:
		control.error('rd token exchange failed')
		return False


def refresh_token():
	client_id = control.setting('rd.client_id')
	secret = control.setting('rd.client_secret')
	refresh = control.setting('rd.refresh')
	if not (client_id and secret and refresh):
		return False
	return _exchange_token(client_id, secret, refresh)


def _maybe_refresh():
	try:
		expires = int(control.setting('rd.expires', '0') or '0')
	except ValueError:
		expires = 0
	if expires and expires - time.time() < 600:
		refresh_token()


def revoke():
	for key in ('rd.token', 'rd.refresh', 'rd.expires', 'rd.client_id',
				'rd.client_secret', 'rd.user'):
		control.set_setting(key, '')


def _fetch_username():
	info = account_info()
	if info and info.get('username'):
		control.set_setting('rd.user', info['username'])


def account_info():
	return _get('user')


def account_status():
	"""Return ``(ok, message)`` for the account behind the stored token.

	Worth checking before anything else: an expired or non-premium account
	fails every resolve with an unhelpful error, and there is no point
	querying availability for it either. Cached briefly so navigation does
	not re-query.
	"""
	if not authorized():
		return False, 'Real-Debrid is not authorized'
	cached = cache.get('rd_account_status')
	if cached is not None:
		return bool(cached.get('ok')), cached.get('message', '')

	info = account_info()
	if not info or not isinstance(info, dict) or 'username' not in info:
		# don't cache a transient failure
		return False, ('Real-Debrid did not accept the stored token. '
					   'Re-authorize under Settings > Accounts.')
	try:
		seconds = int(info.get('premium', 0) or 0)
	except (TypeError, ValueError):
		seconds = 0
	if info.get('type') != 'premium' or seconds <= 0:
		result = (False, 'Real-Debrid account "%s" has no active premium time. '
						 'Torrents cannot be resolved without it.'
				  % info.get('username', '?'))
	else:
		result = (True, 'premium, %d days left' % (seconds // 86400))
	cache.set('rd_account_status', {'ok': result[0], 'message': result[1]}, hours=1)
	return result


# ---------------------------------------------------------------------------
# Torrent operations
# ---------------------------------------------------------------------------

def check_cache(hashes):
	"""Query instantAvailability for one hash or a list of hashes."""
	if not hashes:
		return {}
	if isinstance(hashes, (list, tuple)):
		suffix = '/' + '/'.join(hashes)
	else:
		suffix = '/' + hashes
	return _get('torrents/instantAvailability' + suffix) or {}


def cached_hashes(hashes, batch=40):
	"""Return ``(cached_set, usable)`` for the given info hashes.

	``usable`` reports whether Real-Debrid actually answered with cache
	information. Real-Debrid deprecated instantAvailability and it now
	commonly replies with nothing useful, in which case the caller must not
	treat "not listed" as "not cached" - that would hide every source.
	"""
	cached, usable = set(), False
	hashes = [h.lower() for h in hashes if h]
	if not hashes:
		return cached, usable

	# Querying availability for an account that cannot resolve anything is
	# pointless and just burns rate limit.
	ok, _message = account_status()
	if not ok:
		return cached, usable

	minutes = max(1, control.get_int('rd.cache_check_minutes', 20))
	pending = []
	for info_hash in hashes:
		entry = cache.get('rd_avail_%s' % info_hash)
		if entry is None:
			pending.append(info_hash)
			continue
		usable = True
		if entry.get('cached'):
			cached.add(info_hash)

	for start in range(0, len(pending), batch):
		chunk = pending[start:start + batch]
		result = check_cache(chunk)
		if not isinstance(result, dict):
			continue
		_remember(chunk, result, minutes)
		for info_hash, value in result.items():
			# a cached torrent maps to a non-empty dict of file variants
			if isinstance(value, dict) and value:
				variants = value.get('rd') if 'rd' in value else value
				if variants:
					usable = True
					cached.add(info_hash.lower())
			elif isinstance(value, list) and value:
				usable = True
				cached.add(info_hash.lower())
	return cached, usable


def _remember(chunk, result, minutes):
	"""Cache the availability answer for each hash in a queried batch.

	Both outcomes are stored, so repeated navigation over the same source
	list does not re-query Real-Debrid and risk its rate limit.
	"""
	hours = minutes / 60.0
	lowered = {k.lower(): v for k, v in result.items()}
	for info_hash in chunk:
		value = lowered.get(info_hash)
		if isinstance(value, dict):
			value = value.get('rd') if 'rd' in value else value
		is_cached = bool(value)
		cache.set('rd_avail_%s' % info_hash, {'cached': is_cached}, hours=hours)


def add_magnet(magnet):
	resp = _post('torrents/addMagnet', {'magnet': magnet})
	return resp.get('id', '') if resp else ''


def select_files(torrent_id, file_ids='all'):
	return _post('torrents/selectFiles/%s' % torrent_id, {'files': file_ids})


def torrent_info(torrent_id):
	return _get('torrents/info/%s' % torrent_id)


def active_count():
	return _get('torrents/activeCount') or {'nb': 0, 'list': []}


def unrestrict_link(link):
	resp = _post('unrestrict/link', {'link': link})
	if resp and 'download' in resp:
		return resp['download']
	return None


def delete_torrent(torrent_id):
	if not torrent_id:
		return
	_delete('torrents/delete/%s' % torrent_id)


def _prune_active():
	"""Real-Debrid caps concurrent transfers; drop the oldest if at the limit."""
	try:
		active = active_count()
		if int(active.get('nb', 0)) >= 5:
			stale = active.get('list', [])
			if stale:
				torrents = _get('torrents') or []
				match = [t for t in torrents if t.get('hash') == stale[0]]
				if match:
					delete_torrent(match[0]['id'])
	except Exception:
		control.error('rd prune failed')


# ---------------------------------------------------------------------------
# Magnet -> playable link
# ---------------------------------------------------------------------------

# Terminal Real-Debrid transfer states, mapped to what to tell the user.
_TERMINAL = {
	'magnet_error': 'Real-Debrid could not read the magnet',
	'error': 'Real-Debrid reported a transfer error',
	'virus': 'Real-Debrid flagged this torrent as containing a virus',
	'dead': 'Torrent is dead - Real-Debrid found no seeders',
}
# States that mean the torrent is not already on Real-Debrid's servers.
_PENDING = ('queued', 'downloading', 'compressing', 'uploading')


def resolve_magnet(magnet, info_hash, season=None, episode=None, title=''):
	"""Add a magnet and return ``(playable_url, error)``.

	Exactly one of the two is set. Real-Debrid only fills in a torrent's
	``links`` once its status reaches ``downloaded``; a freshly added magnet
	passes through magnet_conversion / waiting_files_selection / queued
	first, so the status has to be polled rather than read once. Cached
	torrents reach ``downloaded`` in a few seconds; anything still
	downloading after the timeout is simply not cached.
	"""
	_maybe_refresh()
	with _resolve_semaphore:
		torrent_id = None
		try:
			info_hash = (info_hash or '').lower()
			_prune_active()

			torrent_id = add_magnet(magnet)
			if not torrent_id:
				return None, 'Real-Debrid refused the magnet (check your account is active)'

			timeout = max(5, control.get_int('rd.resolve_timeout', 20))
			deadline = time.time() + timeout
			info, status, selected_once = None, '', False

			while time.time() < deadline:
				info = torrent_info(torrent_id)
				if not info:
					control.sleep(1000)
					continue
				status = info.get('status', '')
				if status in _TERMINAL:
					return _fail(torrent_id, _TERMINAL[status])
				if status == 'waiting_files_selection':
					select_files(torrent_id, 'all')
					selected_once = True
				elif status == 'downloaded' and info.get('links'):
					break
				control.sleep(1000)
			else:
				status = (info or {}).get('status', status)

			if not info or status != 'downloaded' or not info.get('links'):
				if status in _PENDING:
					progress = (info or {}).get('progress', 0)
					return _fail(torrent_id,
								 'Not cached on Real-Debrid - it started downloading '
								 '(%s%%). Pick another source.' % progress)
				if status == 'magnet_conversion':
					return _fail(torrent_id,
								 'Real-Debrid was still converting the magnet after %ss'
								 % timeout)
				if not selected_once and status == 'waiting_files_selection':
					return _fail(torrent_id, 'Real-Debrid never accepted the file selection')
				return _fail(torrent_id,
							 'Real-Debrid returned no download links (status: %s)'
							 % (status or 'unknown'))

			selected = [
				(idx, f) for idx, f in
				enumerate([f for f in info['files'] if f.get('selected') == 1])
				if f['path'].lower().endswith(VIDEO_EXTENSIONS)
			]
			selected.sort(key=lambda x: x[1].get('bytes', 0), reverse=True)
			if not selected:
				return _fail(torrent_id, 'Torrent contains no playable video file')

			index = None
			if season and episode:
				for idx, f in selected:
					if _episode_match(season, episode, f['path']):
						index = idx
						break
				if index is None and len(selected) > 1:
					# a pack that does not actually carry this episode
					return _fail(torrent_id,
								 'No file matching S%02dE%02d in this torrent'
								 % (int(season), int(episode)))
			if index is None:
				index = selected[0][0]

			try:
				link = info['links'][index]
			except IndexError:
				return _fail(torrent_id, 'Real-Debrid link list did not match the file list')

			resolved = unrestrict_link(link)
			if not control.get_bool('rd.keep_cloud', False):
				delete_torrent(torrent_id)

			if not resolved:
				return None, 'Real-Debrid could not unrestrict the file link'
			if resolved.lower().endswith('.rar'):
				return None, 'Real-Debrid returned a .rar archive, which cannot be played'
			return resolved, None
		except Exception as exc:
			control.error('rd resolve_magnet failed')
			if torrent_id:
				delete_torrent(torrent_id)
			return None, 'Unexpected Real-Debrid error: %s' % exc


def _fail(torrent_id, reason):
	control.log('Real-Debrid resolve failed: %s' % reason)
	if torrent_id and not control.get_bool('rd.keep_cloud', False):
		delete_torrent(torrent_id)
	return None, reason


def _episode_match(season, episode, path):
	filename = path.rsplit('/', 1)[-1].lower()
	s, e = int(season), int(episode)
	patterns = (
		r's%02de%02d' % (s, e),
		r'%dx%02d' % (s, e),
		r's%de%d' % (s, e),
		r'season.?%d.*episode.?%d' % (s, e),
	)
	return any(re.search(p, filename) for p in patterns)
