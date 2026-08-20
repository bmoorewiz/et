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
from resources.lib import health
from resources.lib import mediafiles

REST_BASE = 'https://api.real-debrid.com/rest/1.0/'
OAUTH_BASE = 'https://api.real-debrid.com/oauth/v2/'
OPEN_SOURCE_CLIENT_ID = 'X245A4XAIBGVM'
GRANT_TYPE = 'http://oauth.net/grant_type/device/1.0'

# Kept as an alias: the canonical list now lives in mediafiles.
VIDEO_EXTENSIONS = mediafiles.VIDEO_EXTENSIONS

# Real-Debrid rate limits and disallows too many simultaneous magnet resolves.
_resolve_semaphore = threading.Semaphore(3)

NAME = 'Real-Debrid'
# (connect, read). These are small JSON round trips; the old flat 45 meant
# a box that had lost its network spent three quarters of a minute per
# request finding that out.
_TIMEOUT = (10, 20)

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
	if health.benched(NAME):
		control.debug('skipping Real-Debrid GET %s: not answering' % path)
		return None
	try:
		resp = _session.get(url, timeout=_TIMEOUT)
	except Exception:
		control.error('rd get failed: %s' % path)
		health.record_failure(NAME)
		return None
	health.record_success(NAME)
	try:
		data = resp.json() if resp.content else None
	except ValueError:
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
	if health.benched(NAME):
		control.debug('skipping Real-Debrid POST %s: not answering' % path)
		return None
	try:
		resp = _session.post(url, data=payload, timeout=_TIMEOUT)
	except Exception:
		control.error('rd post failed: %s' % path)
		health.record_failure(NAME)
		return None
	health.record_success(NAME)
	try:
		if resp.status_code == 204:
			return {}
		data = resp.json() if resp.content else {}
	except ValueError:
		return {}
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
		return _session.delete(url, timeout=_TIMEOUT)
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
	days = seconds // 86400
	if info.get('type') != 'premium' or seconds <= 0:
		result = (False, 'Real-Debrid account "%s" has no active premium time. '
						 'Torrents cannot be resolved without it.'
				  % info.get('username', '?'))
		days = 0
	else:
		result = (True, 'premium, %d days left' % days)
	cache.set('rd_account_status',
			  {'ok': result[0], 'message': result[1], 'days': days}, hours=1)
	return result


def days_left():
	"""Days of subscription remaining, or None if not known.

	Read from the cached account result rather than asked for, so this
	costs nothing and can be consulted freely.
	"""
	cached = cache.get('rd_account_status')
	if not cached or cached.get('days') is None:
		return None
	try:
		return int(cached['days'])
	except (TypeError, ValueError):
		return None


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


# Official Real-Debrid error_code table (api.real-debrid.com).
RD_ERRORS = {
	-1: 'Internal error', 1: 'Missing parameter', 2: 'Bad parameter value',
	3: 'Unknown method', 4: 'Method not allowed', 5: 'Slow down',
	6: 'Resource unreachable', 7: 'Resource not found', 8: 'Bad token',
	9: 'Permission denied', 10: 'Two-Factor authentication needed',
	11: 'Two-Factor authentication pending', 12: 'Invalid login',
	13: 'Invalid password', 14: 'Account locked', 15: 'Account not activated',
	16: 'Unsupported hoster', 17: 'Hoster in maintenance',
	18: 'Hoster limit reached', 19: 'Hoster temporarily unavailable',
	20: 'Hoster not available for free users', 21: 'Too many active downloads',
	22: 'IP address not allowed', 23: 'Traffic exhausted',
	24: 'File unavailable', 25: 'Service unavailable', 26: 'Upload too big',
	27: 'Upload error', 28: 'File not allowed', 29: 'Torrent too big',
	30: 'Torrent file invalid', 31: 'Action already done',
	32: 'Image resolution error', 33: 'Torrent already active',
	34: 'Too many requests', 35: 'Infringing file', 36: 'Fair usage limit',
	37: 'Disabled endpoint',
}
# Advice worth adding to the bare Real-Debrid wording.
_ERROR_HINT = {
	9: 'your Real-Debrid account may be locked or not premium',
	21: 'delete some transfers in your Real-Debrid account',
	22: 'Real-Debrid saw a different IP than the one your account is tied to',
	23: 'your Real-Debrid traffic allowance is used up',
	35: 'Real-Debrid blocks this particular torrent',
	36: 'you have hit Real-Debrid\'s fair usage limit',
}


def error_text(data):
	"""Human-readable text for a Real-Debrid error payload, or '' if fine."""
	if not isinstance(data, dict):
		return ''
	code = data.get('error_code')
	message = data.get('error')
	if code is None and not message:
		return ''
	text = RD_ERRORS.get(code) or (str(message) if message else 'Unknown error')
	hint = _ERROR_HINT.get(code)
	if hint:
		text = '%s - %s' % (text, hint)
	if code is not None:
		text = '%s (Real-Debrid error %s)' % (text, code)
	return text


def find_torrent_by_hash(info_hash):
	"""Return the id of a torrent already in the account with this hash."""
	if not info_hash:
		return ''
	try:
		for torrent in (_get('torrents?limit=100') or []):
			if (torrent.get('hash') or '').lower() == info_hash.lower():
				return torrent.get('id', '')
	except Exception:
		control.error('rd torrent lookup failed')
	return ''


def add_magnet(magnet, info_hash=''):
	"""Add a magnet. Returns ``(torrent_id, error)``.

	Real-Debrid's own error is reported rather than a guess. Two cases are
	recovered from rather than failed: a torrent already in the account
	(error 33) is reused, and hitting the active-transfer limit (error 21)
	is retried once after pruning.
	"""
	resp = _post('torrents/addMagnet', {'magnet': magnet})
	if isinstance(resp, dict) and resp.get('id'):
		return resp['id'], None
	if resp is None:
		return '', 'No response from Real-Debrid (network or token problem)'

	code = resp.get('error_code') if isinstance(resp, dict) else None

	if code == 33:  # already in the account - use the existing transfer
		existing = find_torrent_by_hash(info_hash)
		if existing:
			control.log('reusing torrent already in the Real-Debrid account')
			return existing, None

	if code == 21:  # too many active transfers - free some and retry once
		if _prune_active(force=True):
			retry = _post('torrents/addMagnet', {'magnet': magnet})
			if isinstance(retry, dict) and retry.get('id'):
				return retry['id'], None
			resp = retry if isinstance(retry, dict) else resp

	return '', error_text(resp) or 'Real-Debrid rejected the magnet'


def select_files(torrent_id, file_ids='all'):
	return _post('torrents/selectFiles/%s' % torrent_id, {'files': file_ids})


def torrent_info(torrent_id):
	return _get('torrents/info/%s' % torrent_id)


def active_count():
	return _get('torrents/activeCount') or {'nb': 0, 'list': []}


def unrestrict(link):
	"""Turn a Real-Debrid file link into ``(direct_url, error)``."""
	resp = _post('unrestrict/link', {'link': link})
	if isinstance(resp, dict) and resp.get('download'):
		return resp['download'], None
	detail = error_text(resp) or 'Real-Debrid returned no download link'
	control.log('Real-Debrid unrestrict failed: %s' % detail)
	return None, detail


def unrestrict_link(link):
	"""Backwards-compatible wrapper returning just the URL."""
	return unrestrict(link)[0]


def delete_torrent(torrent_id):
	if not torrent_id:
		return
	_delete('torrents/delete/%s' % torrent_id)


def _prune_active(force=False):
	"""Free up Real-Debrid transfer slots. Returns True if anything was removed.

	Real-Debrid caps concurrent transfers and refuses new magnets with error
	21 once the cap is reached. Removing a single transfer is not always
	enough, so clear every stalled one when we are actually blocked.
	"""
	removed = False
	try:
		active = active_count()
		count = int(active.get('nb', 0) or 0)
		limit = int(active.get('limit', 0) or 0)
		stale = active.get('list', []) or []
		if not force and not (limit and count >= limit) and count < 5:
			return False
		if not stale:
			return False
		torrents = _get('torrents?limit=100') or []
		by_hash = {(t.get('hash') or '').lower(): t.get('id')
				   for t in torrents if t.get('id')}
		# oldest first; when forced, clear them all rather than just one
		targets = stale if force else stale[:1]
		for info_hash in targets:
			torrent_id = by_hash.get((info_hash or '').lower())
			if torrent_id:
				delete_torrent(torrent_id)
				removed = True
		if removed:
			control.log('freed %d Real-Debrid transfer slot(s)' % len(targets))
	except Exception:
		control.error('rd prune failed')
	return removed


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

			torrent_id, add_error = add_magnet(magnet, info_hash)
			if not torrent_id:
				return None, add_error or 'Real-Debrid rejected the magnet'

			timeout = max(5, control.get_int('rd.resolve_timeout', 15))
			grace = max(2, control.get_int('rd.uncached_grace', 4))
			deadline = time.time() + timeout
			info, status, selected_once = None, '', False
			pending_since = None

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
					pending_since = None
				elif status == 'downloaded' and info.get('links'):
					break
				elif status in _PENDING:
					# Real-Debrid is actually fetching this, so it was not
					# cached. A cached torrent passes through queued/downloading
					# almost instantly, so allow a short grace period and then
					# give up rather than burning the whole timeout on a source
					# we already know will not be instant.
					if pending_since is None:
						pending_since = time.time()
					elif time.time() - pending_since >= grace:
						return _fail(torrent_id, _uncached_message(info))
				else:
					pending_since = None
				control.sleep(1000)
			else:
				status = (info or {}).get('status', status)

			if not info or status != 'downloaded' or not info.get('links'):
				if status in _PENDING:
					return _fail(torrent_id, _uncached_message(info))
				if status == 'magnet_conversion':
					return _fail(torrent_id,
								 'Real-Debrid was still converting the magnet after %ss'
								 % timeout)
				if not selected_once and status == 'waiting_files_selection':
					return _fail(torrent_id, 'Real-Debrid never accepted the file selection')
				return _fail(torrent_id,
							 'Real-Debrid returned no download links (status: %s)'
							 % (status or 'unknown'))

			# links[] lines up with the selected files in order, so the index
			# into that enumeration is what identifies the file - keep it.
			chosen_files = list(enumerate(
				[f for f in info['files'] if f.get('selected') == 1]))
			control.log('Real-Debrid torrent %s files: %s'
						% (torrent_id,
						   mediafiles.describe([f for _i, f in chosen_files])))
			by_entry = {id(f): idx for idx, f in chosen_files}
			entry, error = mediafiles.pick([f for _i, f in chosen_files],
										   season, episode, _episode_match,
										   info_hash=info_hash)
			if entry is None:
				return _fail(torrent_id, error)
			index = by_entry[id(entry)]

			try:
				link = info['links'][index]
			except IndexError:
				return _fail(torrent_id, 'Real-Debrid link list did not match the file list')

			resolved, unrestrict_error = unrestrict(link)
			if not control.get_bool('rd.keep_cloud', False):
				delete_torrent(torrent_id)

			if not resolved:
				return None, unrestrict_error or 'Real-Debrid could not unrestrict the file link'
			if resolved.lower().endswith('.rar'):
				return None, 'Real-Debrid returned a .rar archive, which cannot be played'
			return resolved, None
		except Exception as exc:
			control.error('rd resolve_magnet failed')
			if torrent_id:
				delete_torrent(torrent_id)
			return None, 'Unexpected Real-Debrid error: %s' % exc


def _uncached_message(info):
	progress = (info or {}).get('progress', 0)
	seeders = (info or {}).get('seeders')
	detail = 'Not cached on Real-Debrid - it started downloading (%s%%)' % progress
	if seeders is not None:
		detail += ', %s seeders' % seeders
	return detail + '. Trying the next source.'


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
