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
	success, stored = False, False
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
				stored = _store_token(token_resp.json())
				success = True
				break
			# 400 = pending, 404 = invalid, 409 = already used, 410 = expired,
			# 418 = denied, 429 = slow down. Keep polling only on "pending".
			if token_resp.status_code not in (400, 429):
				break
	finally:
		pd.close()

	if success and not stored:
		# Trakt linked fine; the device did not keep the result. Saying
		# "authorized" here and then showing "Authorize Trakt" on the next
		# screen is the most confusing thing the add-on can do.
		control.ok_dialog(control.lang(33104), heading=control.lang(33103))
		return False
	if success:
		_fetch_username()
		control.notify(33017)
		return True
	control.notify(33018)
	return False


def _store_token(data):
	"""Save a token set. Returns False if it did not reach the disk.

	Worth the caller's attention rather than a log line: authorizing
	against a profile that cannot be written succeeds all the way up to
	here, so without this the add-on reports a linked account and then
	asks to be authorized again on the very next screen.
	"""
	stored = control.set_setting('trakt.token', data.get('access_token', ''))
	control.set_setting('trakt.refresh', data.get('refresh_token', ''))
	created = int(data.get('created_at', time.time()))
	expires_in = int(data.get('expires_in', 7776000))
	control.set_setting('trakt.expires', str(created + expires_in))
	return stored


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
	# 'full' rather than 'noseasons' so the show objects carry their images.
	# Trakt serves poster/fanart/logo itself now, so artwork costs nothing
	# beyond this one flag - no second metadata provider, no extra request.
	return _request('GET', '/sync/watched/shows', params={'extended': 'full'}) or []


# Trakt returns image paths without a scheme, e.g.
# "media.trakt.tv/images/shows/000/154/997/posters/medium/f60ddb06de.jpg.webp"
def _image(images, *names):
	"""First usable URL among the named image types."""
	for name in names:
		urls = (images or {}).get(name) or []
		for url in urls:
			if url:
				return url if url.startswith('http') else 'https://' + url
	return ''


def show_art(show):
	"""Kodi art dict for a show.

	Deliberately only four keys. Every entry gets base64-encoded into the
	plugin:// URL of each of its list items, and a source list can be 150
	rows deep, so each extra image URL is paid for many times over. Skins
	derive banner and clearart from these anyway.
	"""
	images = show.get('images') or {}
	art = {
		'poster': _image(images, 'poster'),
		'fanart': _image(images, 'fanart'),
		'clearlogo': _image(images, 'logo'),
		'thumb': _image(images, 'thumb', 'poster'),
	}
	return {key: value for key, value in art.items() if value}


def episode_art(episode, show=None):
	"""Kodi art for an episode: its own screenshot over the show's artwork."""
	art = show_art(show or {})
	screenshot = _image(episode.get('images') or {}, 'screenshot')
	if screenshot:
		# The episode still is the thumb; the show's own thumb is a poor
		# substitute for it but a fine fallback.
		art['thumb'] = screenshot
	return art


def _show_progress(trakt_id):
	return _request('GET', '/shows/%s/progress/watched' % trakt_id,
					params={'hidden': 'false', 'specials': 'false',
							'count_specials': 'false', 'extended': 'full'})


def _episode_details(show_id, season, number):
	"""Full metadata for a single episode, including its air date."""
	if not show_id or season is None or number is None:
		return {}
	return _request('GET', '/shows/%s/seasons/%s/episodes/%s'
					% (show_id, season, number),
					params={'extended': 'full'}) or {}


def _build_entry(show, progress):
	next_ep = progress.get('next_episode') if progress else None
	if not next_ep:
		return None
	show_ids = show.get('ids', {})

	# The progress endpoint does not reliably fill in the next episode's air
	# date or runtime, even with extended=full - and without an air date an
	# unaired episode is indistinguishable from an aired one, which is what
	# put unaired episodes in the list. Ask for the episode itself when it is
	# missing. One extra request per affected show, once per cache window.
	if not next_ep.get('first_aired'):
		detail = _episode_details(show_ids.get('trakt') or show_ids.get('slug'),
								  next_ep.get('season'), next_ep.get('number'))
		if detail:
			next_ep = dict(next_ep)
			next_ep['first_aired'] = detail.get('first_aired')
			if not next_ep.get('runtime'):
				next_ep['runtime'] = detail.get('runtime')
			if not next_ep.get('overview'):
				next_ep['overview'] = detail.get('overview') or ''
			if not next_ep.get('images'):
				next_ep['images'] = detail.get('images') or {}

	ep_ids = next_ep.get('ids', {})
	return {
		'media_type': 'episode',
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
		# Left as None when absent rather than defaulted to 0: the aired-yet
		# check reads these, and "Trakt did not say" must not look like
		# "nothing has aired".
		'aired_count': (progress or {}).get('aired'),
		'completed_count': (progress or {}).get('completed'),
		'art': episode_art(next_ep, show),
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

	# A full result is cached normally. An empty result is cached only
	# briefly: it is usually a genuinely caught-up account, but it can also
	# be every progress call failing at once, and we do not want a transient
	# failure to hide the list for the whole cache window.
	if results:
		cache.set(cache_key, results,
				  hours=max(1, control.get_int('cache.hours', 6)))
	else:
		cache.set(cache_key, results, hours=0.25)
	return _post_filter(results)


def _count(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def has_aired(entry, now=None):
	"""Has this entry's episode actually been broadcast yet?

	Two independent signals, because neither is always available:

	* the episode's own ``first_aired`` - exact, and authoritative whenever
	  Trakt provides it;
	* the show's aired/completed counts - Trakt counts only episodes that
	  have already aired in ``aired``, so once ``completed`` has caught up
	  with it, everything broadcast has been watched and whatever comes
	  next has not aired.

	The second signal is the fix for unaired episodes appearing in the
	list: the progress endpoint does not reliably populate ``first_aired``
	on ``next_episode``, and the old check treated a missing date as
	"assume it aired", so those episodes sailed straight through.
	"""
	now = time.time() if now is None else now
	aired_at = _parse_iso(entry.get('first_aired'))
	if aired_at is not None:
		return aired_at <= now
	total = _count(entry.get('aired_count'))
	watched = _count(entry.get('completed_count'))
	if total is not None and watched is not None:
		return watched < total
	# Nothing to go on at all. Show it: a missing episode is much harder to
	# notice than an extra one.
	return True


def playback_index(kind='episodes', limit=100):
	"""Every id of every part-watched item, mapped to how far in it got.

	Keyed on each id Trakt knows separately, so an entry can be matched by
	whichever id it happens to carry without scanning the whole list.
	"""
	if not authorized():
		return {}
	items = _request('GET', '/sync/playback/%s' % kind, params={'limit': limit})
	if not isinstance(items, list):
		return {}
	key = 'movie' if kind == 'movies' else 'episode'
	index = {}
	for item in items:
		try:
			progress = float(item.get('progress') or 0)
		except (TypeError, ValueError):
			continue
		if progress <= 0:
			continue
		for id_key, value in ((item.get(key) or {}).get('ids') or {}).items():
			if value:
				index[(id_key, value)] = progress
	return index


def apply_playback(entries, index=None):
	"""Tag entries that were started and not finished with their position.

	Deliberately applied to the list on the way out rather than baked into
	the cached entries: where you got to changes every time you stop
	watching, and the cached next-up list does not.
	"""
	if index is None:
		index = playback_index('episodes')
	if not index:
		return entries
	for entry in entries:
		for id_key, value in (_media_ref(entry)['ids']).items():
			progress = index.get((id_key, value))
			if progress:
				entry['progress'] = progress
				break
	return entries


def _post_filter(entries):
	entries = apply_playback([dict(e) for e in entries])
	if control.get_bool('list.aired_only', True):
		now = time.time()
		kept = [e for e in entries if has_aired(e, now)]
		hidden = len(entries) - len(kept)
		if hidden:
			control.log('hiding %d unaired episode(s)' % hidden)
		entries = kept

	sort_mode = control.get_int('list.sort', 0)
	if sort_mode == 0:  # last watched (most recent first)
		entries.sort(key=lambda e: e.get('last_watched') or '', reverse=True)
	elif sort_mode == 1:  # recently aired first
		entries.sort(key=lambda e: e.get('first_aired') or '', reverse=True)
	else:  # alphabetical
		entries.sort(key=lambda e: (e.get('show_title') or '').lower())

	# Something half-watched is the thing most likely to be wanted next, so
	# it goes to the top whatever the chosen order. A stable sort, so the
	# chosen order still decides everything within each group.
	if control.get_bool('list.inprogress_first', True):
		entries.sort(key=lambda e: not e.get('progress'))
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
# Search and browse
# ---------------------------------------------------------------------------

def _ids(item):
	return item.get('ids', {}) or {}


def search_shows(query, limit=40):
	"""Search Trakt for shows. Returns light show dicts."""
	results = _request('GET', '/search/show', auth=False, params={
		'query': query, 'limit': limit, 'extended': 'full'}) or []
	shows = []
	for row in results:
		show = row.get('show') or {}
		if not show:
			continue
		ids = _ids(show)
		shows.append({
			'media_type': 'show',
			'show_title': show.get('title', ''),
			'show_year': show.get('year'),
			'show_trakt': ids.get('trakt'),
			'show_slug': ids.get('slug'),
			'show_imdb': ids.get('imdb'),
			'show_tvdb': ids.get('tvdb'),
			'show_tmdb': ids.get('tmdb'),
			'plot': show.get('overview', '') or '',
			'seasons_count': show.get('aired_episodes'),
			'art': show_art(show),
		})
	return shows


def search_movies(query, limit=40):
	"""Search Trakt for movies. Returns playable movie entries."""
	results = _request('GET', '/search/movie', auth=False, params={
		'query': query, 'limit': limit, 'extended': 'full'}) or []
	movies = []
	for row in results:
		movie = row.get('movie') or {}
		if not movie:
			continue
		ids = _ids(movie)
		movies.append({
			'media_type': 'movie',
			'title': movie.get('title', ''),
			'year': movie.get('year'),
			'movie_trakt': ids.get('trakt'),
			'movie_slug': ids.get('slug'),
			'imdb': ids.get('imdb'),
			'tmdb': ids.get('tmdb'),
			'plot': movie.get('overview', '') or '',
			'runtime': movie.get('runtime'),
			'released': movie.get('released', '') or '',
			'art': show_art(movie),
		})
	return movies


def show_seasons(show_id):
	"""Seasons for a show, specials (season 0) excluded."""
	seasons = _request('GET', '/shows/%s/seasons' % show_id, auth=False,
					   params={'extended': 'full'}) or []
	return [s for s in seasons if (s.get('number') or 0) > 0]


def season_episodes(show, season_number):
	"""Episodes of one season, returned as playable episode entries."""
	show_id = show.get('show_trakt') or show.get('show_slug')
	episodes = _request('GET', '/shows/%s/seasons/%s/episodes'
						% (show_id, season_number), auth=False,
						params={'extended': 'full'}) or []
	# One extra call for the whole season, so browsing shows what has
	# already been watched rather than an undifferentiated list.
	watched = watched_map(show_id) if authorized() else {}
	entries = []
	for episode in episodes:
		ids = _ids(episode)
		screenshot = _image(episode.get('images') or {}, 'screenshot')
		entries.append({
			'media_type': 'episode',
			'show_title': show.get('show_title', ''),
			'show_year': show.get('show_year'),
			'show_trakt': show.get('show_trakt'),
			'show_imdb': show.get('show_imdb'),
			'show_tvdb': show.get('show_tvdb'),
			'show_tmdb': show.get('show_tmdb'),
			'season': episode.get('season'),
			'episode': episode.get('number'),
			'ep_title': episode.get('title') or 'Episode %s' % episode.get('number'),
			'ep_trakt': ids.get('trakt'),
			'ep_imdb': ids.get('imdb'),
			'ep_tvdb': ids.get('tvdb'),
			'ep_tmdb': ids.get('tmdb'),
			'first_aired': episode.get('first_aired'),
			'runtime': episode.get('runtime'),
			'plot': episode.get('overview', '') or '',
			# The show's artwork was already resolved when it was searched
			# for; only the episode still is its own.
			'art': dict(show.get('art') or {},
						**({'thumb': screenshot} if screenshot else {})),
			'watched': bool(watched.get((episode.get('season'),
										 episode.get('number')))),
		})
	return entries


def watched_map(show_id):
	"""``{(season, episode): True}`` for everything watched of this show."""
	progress = _show_progress(show_id) or {}
	watched = {}
	for season in progress.get('seasons') or []:
		number = season.get('number')
		for episode in season.get('episodes') or []:
			if episode.get('completed'):
				watched[(number, episode.get('number'))] = True
	return watched


def episodes_through(seasons, target_season, target_episode, watched=None):
	"""Season/episode payload for everything up to and including one episode.

	Already-watched episodes are left out. Trakt's history is a list of
	plays, not a set of flags, so re-adding one does not "confirm" it -
	it records a second viewing and inflates the play count.
	"""
	try:
		target_season = int(target_season)
		target_episode = int(target_episode)
	except (TypeError, ValueError):
		return []
	watched = watched or {}
	payload = []
	for season in seasons:
		try:
			number = int(season.get('number'))
		except (TypeError, ValueError):
			continue
		# Specials are numbered 0 and are not "before" anything.
		if number < 1 or number > target_season:
			continue
		if number == target_season:
			last = target_episode
		else:
			# Only what has aired: an episode that does not exist yet
			# cannot have been watched.
			last = season.get('aired_episodes')
			if last is None:
				last = season.get('episode_count')
		try:
			last = int(last or 0)
		except (TypeError, ValueError):
			last = 0
		episodes = [{'number': n} for n in range(1, last + 1)
					if not watched.get((number, n))]
		if episodes:
			payload.append({'number': number, 'episodes': episodes})
	return payload


def _show_ref(entry):
	"""Trakt id reference for an entry's show."""
	ids = {}
	for src, key in (('show_trakt', 'trakt'), ('show_imdb', 'imdb'),
					 ('show_tvdb', 'tvdb'), ('show_slug', 'slug')):
		if entry.get(src):
			ids[key] = entry[src]
	return ids


def count_unwatched_through(entry):
	"""How many episodes marking up to this one would actually add."""
	show_id = entry.get('show_trakt') or entry.get('show_slug')
	if not authorized() or not show_id or is_movie(entry):
		return 0, []
	seasons = episodes_through(show_seasons(show_id), entry.get('season'),
							   entry.get('episode'), watched_map(show_id))
	return sum(len(s['episodes']) for s in seasons), seasons


def mark_watched_through(entry, seasons=None):
	"""Add every unwatched episode up to and including this one.

	One request whatever the size of the back catalogue: Trakt's history
	endpoint takes a show with nested seasons and episode numbers, so there
	is no need to look up an id per episode.
	"""
	ids = _show_ref(entry)
	if not authorized() or not ids or is_movie(entry):
		return 0
	if seasons is None:
		_count, seasons = count_unwatched_through(entry)
	total = sum(len(s['episodes']) for s in seasons)
	if not total:
		return 0
	result = _request('POST', '/sync/history',
					  payload={'shows': [{'ids': ids, 'seasons': seasons}]})
	cache.delete('trakt_next_%s' % control.setting('trakt.user', 'me'))
	return total if result else 0


# ---------------------------------------------------------------------------
# Scrobble / history
# ---------------------------------------------------------------------------

def is_movie(entry):
	return (entry or {}).get('media_type') == 'movie'


def _media_ref(entry):
	"""Trakt id reference for an entry, whichever media type it is."""
	ids = {}
	if is_movie(entry):
		for src, key in (('movie_trakt', 'trakt'), ('imdb', 'imdb'),
						 ('tmdb', 'tmdb'), ('movie_slug', 'slug')):
			if entry.get(src):
				ids[key] = entry[src]
	else:
		for src, key in (('ep_trakt', 'trakt'), ('ep_imdb', 'imdb'),
						 ('ep_tvdb', 'tvdb'), ('ep_tmdb', 'tmdb')):
			if entry.get(src):
				ids[key] = entry[src]
	return {'ids': ids}


def _scrobble_body(entry):
	key = 'movie' if is_movie(entry) else 'episode'
	return key, {key: _media_ref(entry)}


def scrobble(entry, action, progress_percent):
	"""Send a scrobble start/pause/stop event for an episode or movie."""
	if not control.get_bool('scrobble.enabled', True) or not authorized():
		return
	if not _media_ref(entry)['ids']:
		control.log('scrobble skipped: entry carries no Trakt ids')
		return
	_key, payload = _scrobble_body(entry)
	payload['progress'] = float(progress_percent)
	_request('POST', '/scrobble/%s' % action, payload=payload)


def _ids_match(stored_ids, entry):
	"""Does a /sync/playback item refer to this entry?"""
	wanted = _media_ref(entry)['ids']
	for key, value in wanted.items():
		if stored_ids.get(key) and stored_ids[key] == value:
			return True
	return False


def playback_progress(entry):
	"""Percentage watched of this item, per Trakt, or 0.

	Trakt keeps a resume point whenever a scrobble stops below the watched
	threshold, so this is what makes "carry on where you left off" work
	across devices rather than only within one Kodi install.
	"""
	if not authorized():
		return 0.0
	path = '/sync/playback/movies' if is_movie(entry) else '/sync/playback/episodes'
	items = _request('GET', path, params={'limit': 100}) or []
	key = 'movie' if is_movie(entry) else 'episode'
	for item in items:
		media = item.get(key) or {}
		if _ids_match(media.get('ids') or {}, entry):
			try:
				return float(item.get('progress') or 0)
			except (TypeError, ValueError):
				return 0.0
	return 0.0


def in_progress(limit=100):
	"""Everything part-watched, per Trakt, newest first.

	The same ``/sync/playback`` records that drive the resume prompt, listed
	as a menu of their own - so picking up something half-finished does not
	mean remembering which show it was and navigating back to it.
	"""
	if not authorized():
		return []
	entries = []
	for kind in ('episodes', 'movies'):
		items = _request('GET', '/sync/playback/%s' % kind,
						 params={'limit': limit, 'extended': 'full'})
		if not isinstance(items, list):
			continue
		for item in items:
			entry = _playback_entry(item)
			if entry:
				entries.append(entry)
	entries.sort(key=lambda e: e.get('paused_at') or '', reverse=True)
	return entries


def _playback_entry(item):
	"""Turn one /sync/playback record into a playable entry."""
	try:
		progress = float(item.get('progress') or 0)
	except (TypeError, ValueError):
		progress = 0.0
	shared = {'progress': progress, 'paused_at': item.get('paused_at') or ''}

	movie = item.get('movie')
	if movie:
		ids = _ids(movie)
		return dict(shared, **{
			'media_type': 'movie',
			'title': movie.get('title', ''),
			'year': movie.get('year'),
			'movie_trakt': ids.get('trakt'),
			'movie_slug': ids.get('slug'),
			'imdb': ids.get('imdb'),
			'tmdb': ids.get('tmdb'),
			'plot': movie.get('overview', '') or '',
			'runtime': movie.get('runtime'),
			'released': movie.get('released', '') or '',
			'art': show_art(movie),
		})

	episode, show = item.get('episode'), item.get('show')
	if not episode or not show:
		return None
	ep_ids, show_ids = _ids(episode), _ids(show)
	return dict(shared, **{
		'media_type': 'episode',
		'show_title': show.get('title', ''),
		'show_year': show.get('year'),
		'show_trakt': show_ids.get('trakt'),
		'show_slug': show_ids.get('slug'),
		'show_imdb': show_ids.get('imdb'),
		'show_tvdb': show_ids.get('tvdb'),
		'show_tmdb': show_ids.get('tmdb'),
		'season': episode.get('season'),
		'episode': episode.get('number'),
		'ep_title': episode.get('title') or 'Episode %s' % episode.get('number'),
		'ep_trakt': ep_ids.get('trakt'),
		'ep_imdb': ep_ids.get('imdb'),
		'ep_tvdb': ep_ids.get('tvdb'),
		'ep_tmdb': ep_ids.get('tmdb'),
		'first_aired': episode.get('first_aired'),
		'runtime': episode.get('runtime'),
		'plot': episode.get('overview', '') or '',
		'art': episode_art(episode, show),
	})


def clear_playback(entry):
	"""Drop Trakt's resume point once something has been finished."""
	if not authorized():
		return
	path = '/sync/playback/movies' if is_movie(entry) else '/sync/playback/episodes'
	items = _request('GET', path, params={'limit': 100}) or []
	key = 'movie' if is_movie(entry) else 'episode'
	for item in items:
		media = item.get(key) or {}
		if _ids_match(media.get('ids') or {}, entry) and item.get('id'):
			_request('DELETE', '/sync/playback/%s' % item['id'])
			return


def hide_show(show_trakt_id):
	"""Hide a show from progress, so it stops appearing in Next Episodes."""
	if not authorized() or not show_trakt_id:
		return False
	payload = {'shows': [{'ids': {'trakt': show_trakt_id}}]}
	result = _request('POST', '/users/hidden/progress_watched', payload=payload)
	cache.delete('trakt_next_%s' % control.setting('trakt.user', 'me'))
	return bool(result)


def hidden_shows():
	"""Shows currently hidden from progress, newest first.

	Hiding is otherwise a one-way door - Trakt's own apps are the only place
	to undo it - so the add-on lists them and offers to put them back.
	"""
	if not authorized():
		return []
	items = _request('GET', '/users/hidden/progress_watched',
					 params={'type': 'show', 'limit': 100})
	if not isinstance(items, list):
		return []
	shows = []
	for item in items:
		show = item.get('show') or {}
		ids = show.get('ids') or {}
		if not ids.get('trakt'):
			continue
		shows.append({
			'media_type': 'episode',
			'show_title': show.get('title', ''),
			'show_year': show.get('year'),
			'show_trakt': ids.get('trakt'),
			'show_slug': ids.get('slug'),
			'show_imdb': ids.get('imdb'),
			'show_tvdb': ids.get('tvdb'),
			'show_tmdb': ids.get('tmdb'),
		})
	return shows


def unhide_show(show_trakt_id):
	if not authorized() or not show_trakt_id:
		return False
	payload = {'shows': [{'ids': {'trakt': show_trakt_id}}]}
	result = _request('POST', '/users/hidden/progress_watched/remove',
					  payload=payload)
	cache.delete('trakt_next_%s' % control.setting('trakt.user', 'me'))
	return bool(result)


def next_episode_after(entry):
	"""The next unwatched episode of this entry's show, or None.

	Asked of Trakt after the current episode is marked watched, so it
	reflects what was just finished.
	"""
	show_id = entry.get('show_trakt') or entry.get('show_slug')
	if not show_id or is_movie(entry):
		return None
	progress = _show_progress(show_id)
	show = {
		'title': entry.get('show_title', ''),
		'year': entry.get('show_year'),
		'ids': {
			'trakt': entry.get('show_trakt'), 'slug': entry.get('show_slug'),
			'imdb': entry.get('show_imdb'), 'tvdb': entry.get('show_tvdb'),
			'tmdb': entry.get('show_tmdb'),
		},
	}
	return _build_entry(show, progress)


def add_to_history(entry):
	"""Mark an episode or movie watched by adding it to the Trakt history."""
	if not authorized():
		return False
	ref = _media_ref(entry)
	if not ref['ids']:
		control.log('mark watched skipped: entry carries no Trakt ids')
		return False
	payload = {'movies': [ref]} if is_movie(entry) else {'episodes': [ref]}
	result = _request('POST', '/sync/history', payload=payload)
	# invalidate the cached next-up list so it recomputes
	cache.delete('trakt_next_%s' % control.setting('trakt.user', 'me'))
	return bool(result)
