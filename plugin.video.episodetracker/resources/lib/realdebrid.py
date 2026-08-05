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
			  control.lang(33015) % verify_url + '\n[B]%s[/B]' % user_code)

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
					  control.lang(33015) % verify_url + '\n[B]%s[/B]\n%s'
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

def resolve_magnet(magnet, info_hash, season=None, episode=None, title=''):
	"""Add a magnet, pick the correct episode file, return a playable URL."""
	_maybe_refresh()
	with _resolve_semaphore:
		torrent_id = None
		try:
			info_hash = (info_hash or '').lower()
			_prune_active()
			torrent_id = add_magnet(magnet)
			if not torrent_id:
				return None
			select_files(torrent_id, 'all')
			info = torrent_info(torrent_id)
			if not info or not info.get('links') or 'error' in info:
				delete_torrent(torrent_id)
				return None

			# Wait briefly for the transfer to be recognised as cached/finished.
			control.sleep(1000)
			elapsed, finished = 0, False
			while elapsed <= 4 and not finished:
				active = active_count()
				if info_hash and info_hash in active.get('list', []):
					control.sleep(1000)
					elapsed += 1
				else:
					finished = True
			if not finished:
				delete_torrent(torrent_id)
				return None

			selected = [
				(idx, f) for idx, f in
				enumerate([f for f in info['files'] if f.get('selected') == 1])
				if f['path'].lower().endswith(VIDEO_EXTENSIONS)
			]
			selected.sort(key=lambda x: x[1].get('bytes', 0), reverse=True)
			if not selected:
				delete_torrent(torrent_id)
				return None

			index = None
			if season and episode:
				for idx, f in selected:
					if _episode_match(season, episode, f['path']):
						index = idx
						break
			if index is None:
				# fall back to the largest video file
				index = selected[0][0]

			link = info['links'][index]
			resolved = unrestrict_link(link)

			if not control.get_bool('rd.keep_cloud', False):
				delete_torrent(torrent_id)

			if resolved and resolved.lower().endswith('.rar'):
				return None
			return resolved
		except Exception:
			control.error('rd resolve_magnet failed')
			if torrent_id:
				delete_torrent(torrent_id)
			return None


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
