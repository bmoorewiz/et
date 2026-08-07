# -*- coding: utf-8 -*-
"""TorBox integration.

Same contract as the Real-Debrid module - ``resolve_magnet()`` returns
``(url, error)`` and ``cached_hashes()`` returns ``(cached_set, usable)`` -
so the dispatcher can treat the two interchangeably.

One real difference: TorBox's cache check works. Real-Debrid deprecated
instantAvailability and it answers with nothing useful, but TorBox's
``/torrents/checkcached`` is live and authoritative, so an uncached source
can be skipped before a transfer is ever created instead of being added and
then abandoned.
"""

import time

import requests
from requests.adapters import HTTPAdapter

from resources.lib import control
from resources.lib import cache
from resources.lib import mediafiles
from resources.lib.realdebrid import _episode_match

BASE = 'https://api.torbox.app/v1/api'
CREATE = '/torrents/createtorrent'
MYLIST = '/torrents/mylist'
CHECK_CACHED = '/torrents/checkcached'
REQUEST_DL = '/torrents/requestdl'
CONTROL = '/torrents/controltorrent'
USER = '/user/me'

_TIMEOUT = 30
_session = requests.Session()
_session.mount(BASE, HTTPAdapter(pool_maxsize=10))


def api_key():
	return control.setting('torbox.api_key', '')


def authorized():
	return bool(api_key())


def enabled():
	return authorized() and control.get_bool('torbox.enabled', False)


def _headers():
	return {
		'Authorization': 'Bearer %s' % api_key(),
		'User-Agent': 'plugin.video.episodetracker',
	}


def _request(method, path, **kwargs):
	if not api_key():
		return None
	try:
		resp = _session.request(method, BASE + path, headers=_headers(),
								timeout=_TIMEOUT, **kwargs)
		return resp.json() if resp.content else None
	except ValueError:
		return None
	except Exception:
		control.error('torbox %s %s failed' % (method, path))
		return None


def _get(path, **kwargs):
	return _request('GET', path, **kwargs)


def _post(path, **kwargs):
	return _request('POST', path, **kwargs)


def error_text(data):
	"""Human-readable text for a TorBox failure payload, or '' if fine."""
	if not isinstance(data, dict):
		return 'No response from TorBox'
	if data.get('success'):
		return ''
	detail = data.get('detail') or data.get('error') or 'Unknown error'
	return 'TorBox: %s' % detail


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

def account_info():
	result = _get(USER, params={'settings': 'false'})
	if isinstance(result, dict) and result.get('success'):
		return result.get('data') or {}
	return None


def account_status():
	"""Return ``(ok, message)`` for the stored API key."""
	if not authorized():
		return False, 'TorBox is not authorized'
	cached = cache.get('torbox_account_status')
	if cached is not None:
		return bool(cached.get('ok')), cached.get('message', '')

	info = account_info()
	if not info:
		# never cache a transient failure
		return False, ('TorBox did not accept the stored API key. '
					   'Re-enter it under Settings > Accounts.')
	plan = info.get('plan')
	expires = info.get('premium_expires_at') or ''
	# plan 0 is the free tier, which cannot serve cached torrents.
	if plan in (0, '0', None):
		result = (False, 'TorBox account "%s" is on the free plan.'
				  % info.get('email', '?'))
	else:
		result = (True, 'plan %s, expires %s' % (plan, expires[:10] or 'unknown'))
	cache.set('torbox_account_status', {'ok': result[0], 'message': result[1]},
			  hours=1)
	return result


# ---------------------------------------------------------------------------
# Cache check - the thing TorBox does better than Real-Debrid
# ---------------------------------------------------------------------------

def cached_hashes(hashes, batch=100):
	"""Return ``(cached_set, usable)`` for the given info hashes."""
	cached, usable = set(), False
	hashes = [h.lower() for h in hashes if h]
	if not hashes or not enabled():
		return cached, usable
	ok, _message = account_status()
	if not ok:
		return cached, usable

	minutes = max(1, control.get_int('rd.cache_check_minutes', 20))
	pending = []
	for info_hash in hashes:
		entry = cache.get('tb_avail_%s' % info_hash)
		if entry is None:
			pending.append(info_hash)
			continue
		usable = True
		if entry.get('cached'):
			cached.add(info_hash)

	for start in range(0, len(pending), batch):
		chunk = pending[start:start + batch]
		result = _post(CHECK_CACHED, params={'format': 'list'},
					   json={'hashes': chunk})
		if not isinstance(result, dict) or not result.get('success'):
			continue
		usable = True
		found = set()
		for item in (result.get('data') or []):
			value = item.get('hash') if isinstance(item, dict) else item
			if value:
				found.add(str(value).lower())
		for info_hash in chunk:
			hit = info_hash in found
			if hit:
				cached.add(info_hash)
			cache.set('tb_avail_%s' % info_hash, {'cached': hit},
					  hours=minutes / 60.0)
	return cached, usable


def is_cached(info_hash):
	"""Single-hash check used just before creating a transfer."""
	result = _get(CHECK_CACHED, params={'hash': info_hash, 'format': 'list'})
	if not isinstance(result, dict) or not result.get('success'):
		return None  # unknown, not "no"
	for item in (result.get('data') or []):
		value = item.get('hash') if isinstance(item, dict) else item
		if value and str(value).lower() == info_hash.lower():
			return True
	return False


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------

def add_magnet(magnet):
	"""Create a transfer. Returns ``(torrent_id, error)``."""
	result = _post(CREATE, data={'magnet': magnet, 'seed': 3,
								 'allow_zip': 'false'})
	if isinstance(result, dict) and result.get('success'):
		data = result.get('data') or {}
		torrent_id = data.get('torrent_id') or data.get('id')
		if torrent_id:
			return torrent_id, None
	return None, error_text(result) or 'TorBox rejected the magnet'


def torrent_info(torrent_id):
	result = _get('%s?id=%s' % (MYLIST, torrent_id),
				  params={'bypass_cache': 'true'})
	if isinstance(result, dict) and result.get('success'):
		return result.get('data') or {}
	return None


def delete_torrent(torrent_id):
	if not torrent_id:
		return
	_post(CONTROL, json={'torrent_id': torrent_id, 'operation': 'delete'})


def unrestrict(torrent_id, file_id):
	"""Turn a file into ``(direct_url, error)``."""
	result = _get(REQUEST_DL, params={'token': api_key(),
									  'torrent_id': torrent_id,
									  'file_id': file_id})
	if isinstance(result, dict) and result.get('success') and result.get('data'):
		return result['data'], None
	return None, error_text(result) or 'TorBox returned no download link'


def resolve_magnet(magnet, info_hash, season=None, episode=None, title=''):
	"""Add a magnet and return ``(playable_url, error)``."""
	info_hash = (info_hash or '').lower()
	torrent_id = None
	try:
		# TorBox's cache check is reliable, so an uncached source is rejected
		# before a transfer exists rather than created and then abandoned.
		if info_hash and control.get_bool('torbox.cached_only', True):
			cached = is_cached(info_hash)
			if cached is False:
				return None, 'Not cached on TorBox. Trying the next source.'

		torrent_id, error = add_magnet(magnet)
		if not torrent_id:
			return None, error

		timeout = max(5, control.get_int('rd.resolve_timeout', 15))
		deadline = time.time() + timeout
		files = []
		while time.time() < deadline:
			info = torrent_info(torrent_id) or {}
			files = info.get('files') or []
			state = (info.get('download_state') or '').lower()
			if files and (info.get('download_present') or
						  info.get('download_finished') or state == 'completed'):
				break
			if state in ('error', 'stalled (no seeds)'):
				return _fail(torrent_id, 'TorBox transfer failed (%s)' % state)
			control.sleep(1000)

		if not files:
			return _fail(torrent_id, 'TorBox returned no files for this torrent')

		control.log('TorBox torrent %s files: %s'
					% (torrent_id, mediafiles.describe(files)))
		chosen, error = mediafiles.pick(files, season, episode, _episode_match)
		if chosen is None:
			return _fail(torrent_id, error)

		url, error = unrestrict(torrent_id, chosen.get('id'))
		if not control.get_bool('torbox.keep_cloud', False):
			delete_torrent(torrent_id)
		if not url:
			return None, error
		return url, None
	except Exception as exc:
		control.error('torbox resolve_magnet failed')
		if torrent_id:
			delete_torrent(torrent_id)
		return None, 'Unexpected TorBox error: %s' % exc


def _fail(torrent_id, reason):
	control.log('TorBox resolve failed: %s' % reason)
	if torrent_id and not control.get_bool('torbox.keep_cloud', False):
		delete_torrent(torrent_id)
	return None, reason


def revoke():
	control.set_setting('torbox.api_key', '')
	# The account status is cached for an hour; without clearing it, a
	# re-entered key would be judged on the old key's answer.
	cache.delete('torbox_account_status')
	cache.delete('torbox_account_status')
