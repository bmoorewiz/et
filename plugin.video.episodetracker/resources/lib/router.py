# -*- coding: utf-8 -*-
"""Request router: maps plugin:// actions to behaviour and builds menus."""

import sys
import time

import xbmc

from resources.lib import control
from resources.lib import trakt
from resources.lib import realdebrid
from resources.lib import scrapers
from resources.lib import cache
from resources.lib import player
from resources.lib import updater
from resources.lib import debrid


# Shared with the service, which builds the same play-next callback URLs.
_encode = control.encode_obj
_decode = control.decode_obj


def dispatch():
	"""Route one plugin:// request, and never leave the directory unclosed.

	If an exception escapes, Kodi never gets its endOfDirectory, so
	CPluginDirectory reports failure and CGUIMediaWindow::Update() responds
	by logging an error and falling back to the add-on's root - the user
	gets dumped on the main screen with no idea why. Closing the directory
	with the reason in it turns any crash into something readable, in the
	place the user was already looking.
	"""
	try:
		return _dispatch()
	except Exception as exc:
		control.error('request failed: %s' % (sys.argv[2:] or ''))
		if control.handle >= 0:
			control.add_directory_item('[COLOR red]%s[/COLOR]'
									   % control.langf(33092, exc),
									   {'action': 'settings'}, is_folder=False)
			control.end_directory(cache_to_disc=False, content='')
		else:
			control.ok_dialog(control.langf(33092, exc))


def _dispatch():
	params = control.parse_params(sys.argv[2] if len(sys.argv) > 2 else '')
	action = params.get('action')

	if action == 'next_episodes':
		return next_episodes_menu(refresh=params.get('refresh') == '1')
	if action == 'sources':
		return sources_menu(_decode(params['entry']))
	if action == 'autoplay':
		return autoplay(_decode(params['entry']))
	if action == 'play':
		payload = _decode(params['data'])
		return player.play(payload['source'], payload['entry'])
	if action == 'mark_watched':
		return mark_watched(_decode(params['entry']))
	if action == 'mark_through':
		return mark_watched_through(_decode(params['entry']))
	if action == 'hide_show':
		return hide_show(_decode(params['entry']))
	if action == 'hidden_shows':
		return hidden_shows_menu()
	if action == 'unhide_show':
		return unhide_show(_decode(params['entry']))
	if action == 'continue_watching':
		return continue_watching_menu()
	if action == 'refresh_sources':
		return refresh_sources(_decode(params['entry']))
	if action == 'search_shows':
		return search_menu('show')
	if action == 'search_movies':
		return search_menu('movie')
	if action == 'show_seasons':
		return seasons_menu(_decode(params['entry']))
	if action == 'season_episodes':
		return episodes_menu(_decode(params['entry']), params['season'])
	if action == 'trakt_auth':
		return _auth(trakt.authenticate)
	if action == 'trakt_revoke':
		return _revoke(trakt.revoke)
	if action == 'rd_auth':
		return _auth(realdebrid.authenticate)
	if action == 'rd_revoke':
		return _revoke(realdebrid.revoke)
	if action == 'check_updates':
		return updater.check_and_prompt()
	if action == 'install_update':
		return updater.prompt_install(params['version'])
	if action == 'torbox_check':
		return torbox_check()
	if action == 'torbox_revoke':
		from resources.lib import torbox
		return _revoke(torbox.revoke)
	if action == 'upload_logs':
		return upload_logs()
	if action == 'clear_cache':
		return clear_cache()
	if action == 'coco_settings':
		return open_coco_settings()
	if action == 'refresh':
		return xbmc.executebuiltin('Container.Refresh')
	if action == 'settings':
		control.open_settings()
		# openSettings() blocks until the dialog closes. This is the only
		# moment the add-on learns that a TorBox key was typed in, so it
		# is the only chance to back it up.
		from resources.lib import credentials
		return credentials.sync()

	return main_menu()


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

def main_menu():
	# A previously-cached update result is shown immediately; the refresh for
	# the next visit happens on a background thread so the menu never stalls.
	pending = updater.pending_update()
	if pending:
		control.add_directory_item(
			'[COLOR lime]%s[/COLOR]' % (control.langf(33040, pending)),
			{'action': 'install_update', 'version': pending}, is_folder=False)

	if not scrapers.available():
		control.add_directory_item(
			'[COLOR red]%s[/COLOR]' % control.lang(33008),
			{'action': 'settings'}, is_folder=False)

	control.add_directory_item(
		'[B]%s[/B]' % control.lang(33001),
		{'action': 'next_episodes'},
		art={'icon': control.addon_icon},
		info={'plot': control.lang(33001)})

	control.add_directory_item(
		control.lang(33050), {'action': 'search_shows'},
		art={'icon': control.addon_icon}, info={'plot': control.lang(33050)})
	control.add_directory_item(
		control.lang(33051), {'action': 'search_movies'},
		art={'icon': control.addon_icon}, info={'plot': control.lang(33051)})

	if trakt.authorized():
		control.add_directory_item(
			control.lang(33084), {'action': 'continue_watching'},
			art={'icon': control.addon_icon}, info={'plot': control.lang(33085)})
		# Hiding is otherwise only undoable on Trakt's own site, so keep a way
		# back within reach of the list it removes shows from.
		control.add_directory_item(
			control.lang(33079), {'action': 'hidden_shows'},
			art={'icon': control.addon_icon}, info={'plot': control.lang(33082)})

	if not trakt.authorized():
		control.add_directory_item(
			'[COLOR orange]%s[/COLOR]' % control.lang(33004),
			{'action': 'trakt_auth'}, is_folder=False)
	if not realdebrid.authorized():
		control.add_directory_item(
			'[COLOR orange]%s[/COLOR]' % control.lang(33005),
			{'action': 'rd_auth'}, is_folder=False)

	# Only when something already looks unauthorized: that is exactly when
	# "you are signed out" and "this device cannot keep your settings" look
	# identical, and the second one is not worth a disk check on every visit
	# to a menu that is working.
	if not trakt.authorized() or not realdebrid.authorized():
		from resources.lib import diagnostics
		problem = diagnostics.storage_problem()
		if problem:
			control.add_directory_item('[COLOR red]%s[/COLOR]' % problem,
									   {'action': 'upload_logs'},
									   is_folder=False)

	if not pending:
		control.add_directory_item(
			control.lang(33029), {'action': 'check_updates'}, is_folder=False)

	control.add_directory_item(
		control.lang(33003), {'action': 'settings'}, is_folder=False)

	# Reachable from here as well as from Settings > Tools. The one tool for
	# diagnosing a broken settings dialog cannot live only behind the
	# settings dialog.
	control.add_directory_item(
		control.lang(33103), {'action': 'upload_logs'}, is_folder=False)

	control.end_directory(content='')

	# fire-and-forget refresh of the cached update state
	updater.auto_check()


def next_episodes_menu(refresh=False):
	"""The next-up list.

	Never cached to disc. This list changes while you are away from it -
	finishing an episode removes it - and Kodi's directory cache would
	otherwise redisplay the copy it made before playback started, so an
	episode you just watched would still be sitting there when you backed
	out of the player.
	"""
	if not trakt.authorized():
		control.notify(33006)
		control.add_directory_item(control.lang(33004),
								   {'action': 'trakt_auth'}, is_folder=False)
		control.end_directory(cache_to_disc=False, content='')
		return

	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33009))
	try:
		entries = trakt.next_episodes(refresh=refresh)
	finally:
		pd.close()

	if not entries:
		return nothing_here(33010)

	autoplay = control.get_bool('results.autoplay', False)
	for entry in entries:
		_add_episode_item(entry, autoplay)

	control.end_directory(cache_to_disc=False, content='episodes')


def _show_from_entry(entry):
	"""The show an episode belongs to, shaped like a search result.

	Lets an episode anywhere in the add-on open its show's seasons without
	the browse menus needing to know where the episode came from.
	"""
	return {
		'media_type': 'show',
		'show_title': entry.get('show_title', ''),
		'show_year': entry.get('show_year'),
		'show_trakt': entry.get('show_trakt'),
		'show_slug': entry.get('show_slug'),
		'show_imdb': entry.get('show_imdb'),
		'show_tvdb': entry.get('show_tvdb'),
		'show_tmdb': entry.get('show_tmdb'),
		'plot': entry.get('plot', ''),
		'art': dict(entry.get('art') or {}),
	}


def _progress_suffix(entry):
	"""How far through the show this episode is, e.g. "3/10"."""
	if not control.get_bool('list.show_progress', True):
		return ''
	total, watched = entry.get('aired_count'), entry.get('completed_count')
	try:
		total, watched = int(total), int(watched)
	except (TypeError, ValueError):
		return ''
	if total <= 0:
		return ''
	return '  [COLOR grey](%d/%d)[/COLOR]' % (watched, total)


def _resume_suffix(entry):
	"""How far into this episode you got, for the Continue Watching list."""
	try:
		percent = float(entry.get('progress') or 0)
	except (TypeError, ValueError):
		return ''
	if percent <= 0:
		return ''
	return '  [COLOR grey](%d%%)[/COLOR]' % percent


def _format_minutes(minutes):
	minutes = int(round(minutes))
	if minutes >= 60:
		return '%dh %02dm' % divmod(minutes, 60)
	return '%dm' % minutes


def _remaining_suffix(entry):
	"""How much of a part-watched episode is left, e.g. "24m left".

	The point of picking a half-finished episode back up is knowing whether
	there is time for it, so this says what is left rather than how far in
	it got.
	"""
	try:
		percent = float(entry.get('progress') or 0)
	except (TypeError, ValueError):
		return ''
	runtime = player.runtime_seconds(entry)
	if percent <= 0 or not runtime:
		return ''
	remaining = runtime * (100.0 - percent) / 100.0 / 60.0
	if remaining < 1:
		return '  [COLOR gold](%s)[/COLOR]' % control.lang(33101)
	return '  [COLOR gold](%s)[/COLOR]' % control.langf(
		33100, _format_minutes(remaining))


def _resume_point(entry):
	"""``(seconds, total)`` for a part-watched episode, or None."""
	try:
		percent = float(entry.get('progress') or 0)
	except (TypeError, ValueError):
		return None
	runtime = player.runtime_seconds(entry)
	if percent <= 0 or percent >= 100 or not runtime:
		return None
	return int(runtime * percent / 100.0), runtime


def _add_episode_item(entry, autoplay, suffix=''):
	# Reuse the player's label/info builders so the two never drift apart.
	# What is left of a half-watched episode beats how far through the
	# series it is - that is the thing being decided when looking at it.
	label = player.display_label(entry) + (
		suffix or _remaining_suffix(entry) or _progress_suffix(entry))
	encoded = _encode(entry)
	info = player.media_info(entry)
	context = [
		(control.lang(33022),
		 'RunPlugin(%s)' % control.build_url(
			 {'action': 'mark_watched', 'entry': encoded})),
		(control.lang(33086),
		 'Container.Update(%s)' % control.build_url(
			 {'action': 'refresh_sources', 'entry': encoded})),
	]
	if entry.get('show_trakt') or entry.get('show_slug'):
		# Browsing opens the show's seasons; marking through covers
		# everything before this episode in one go, for a show picked up
		# part-way through.
		context.append(
			(control.lang(33094),
			 'Container.Update(%s)' % control.build_url(
				 {'action': 'show_seasons',
				  'entry': _encode(_show_from_entry(entry))})))
		context.append(
			(control.lang(33095),
			 'RunPlugin(%s)' % control.build_url(
				 {'action': 'mark_through', 'entry': encoded})))
	if entry.get('show_trakt'):
		context.append(
			(control.lang(33076),
			 'RunPlugin(%s)' % control.build_url(
				 {'action': 'hide_show', 'entry': encoded})))
	context.append((control.lang(33002), 'Container.Refresh'))
	action = 'autoplay' if autoplay else 'sources'
	# Skins draw their own progress bar from a resume point. Only set it on
	# folder items: on a playable one Kodi raises its own "Resume / Play
	# from beginning" chooser, which would ask the same question the
	# add-on's own resume prompt is about to.
	resume = None if autoplay else _resume_point(entry)
	control.add_directory_item(
		label,
		{'action': action, 'entry': encoded},
		is_folder=not autoplay,
		is_playable=autoplay,
		art=dict(entry.get('art') or {}),
		info=info,
		context=context,
		resume=resume)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_menu(media_type):
	"""Prompt for a query, then list matching shows or movies.

	Cancelling the keyboard must not end up handing Kodi an empty
	directory. Kodi has already committed to navigating into this folder by
	the time the prompt appears, so an empty listing drops the user on a
	blank screen they never asked for - and returning a *failed* directory
	instead is worse, because CGUIMediaWindow::Update() responds to that by
	logging an error and falling back to the add-on's root. Either way the
	user is somewhere they did not choose to be, which is what "hitting
	back errors out to the main screen" was. So there is always something
	in the listing, and Back from it behaves normally.
	"""
	heading = control.lang(33050 if media_type == 'show' else 33051)
	query = control.keyboard(heading=heading)
	if not query:
		return search_again_menu(media_type)

	pd = control.progress_bg
	pd.create(control.addon_name, control.langf(33052, query))
	try:
		if media_type == 'show':
			results = trakt.search_shows(query)
		else:
			results = trakt.search_movies(query)
	finally:
		pd.close()

	if not results:
		control.notify(control.langf(33053, query))
		return search_again_menu(media_type)

	for item in results:
		if media_type == 'show':
			_add_show_item(item)
		else:
			_add_movie_item(item)

	# search results are query-specific; caching them to disc is unhelpful
	control.end_directory(cache_to_disc=False,
						  content='tvshows' if media_type == 'show' else 'movies')


def nothing_here(message, params=None):
	"""Close a folder with the reason in it, never with nothing at all.

	By the time a handler discovers it has nothing to show, Kodi has
	already navigated into the folder - it cannot be told to stay put. An
	empty listing therefore reads as a blank screen or a failure, so every
	dead end says what happened instead.
	"""
	if isinstance(message, int):
		message = control.lang(message)
	control.add_directory_item(message, params or {'action': 'refresh'},
							   is_folder=False,
							   art={'icon': control.addon_icon},
							   info={'plot': message})
	control.end_directory(cache_to_disc=False, content='')


def search_again_menu(media_type):
	"""A one-item listing offering another go at the search.

	Somewhere to land when a search is cancelled or finds nothing. Kodi is
	already navigating into this folder and cannot be told otherwise, so
	the only choice is what it navigates into.
	"""
	action = 'search_shows' if media_type == 'show' else 'search_movies'
	control.add_directory_item(control.lang(33090), {'action': action},
							   art={'icon': control.addon_icon},
							   info={'plot': control.lang(33091)})
	control.end_directory(cache_to_disc=False, content='')


def _add_show_item(show):
	year = show.get('show_year')
	label = '%s (%s)' % (show.get('show_title', ''), year) if year \
		else show.get('show_title', '')
	control.add_directory_item(
		label,
		{'action': 'show_seasons', 'entry': _encode(show)},
		is_folder=True,
		art=dict(show.get('art') or {}),
		info={'mediatype': 'tvshow',
			  'title': show.get('show_title', ''),
			  'plot': show.get('plot', '')})


def _add_movie_item(movie, suffix=''):
	year = movie.get('year')
	label = '%s (%s)' % (movie.get('title', ''), year) if year \
		else movie.get('title', '')
	encoded = _encode(movie)
	autoplay = control.get_bool('results.autoplay', False)
	context = [
		(control.lang(33022),
		 'RunPlugin(%s)' % control.build_url(
			 {'action': 'mark_watched', 'entry': encoded})),
		(control.lang(33086),
		 'Container.Update(%s)' % control.build_url(
			 {'action': 'refresh_sources', 'entry': encoded})),
	]
	control.add_directory_item(
		label + suffix,
		{'action': 'autoplay' if autoplay else 'sources', 'entry': encoded},
		is_folder=not autoplay,
		is_playable=autoplay,
		art=dict(movie.get('art') or {}),
		info={'mediatype': 'movie',
			  'title': movie.get('title', ''),
			  'plot': movie.get('plot', ''),
			  'premiered': (movie.get('released') or '')[:10],
			  'duration': (movie.get('runtime') or 0) * 60 or None},
		context=context)


def seasons_menu(show):
	seasons = trakt.show_seasons(show.get('show_trakt') or show.get('show_slug'))
	if not seasons:
		return nothing_here(33054)
	for season in seasons:
		number = season.get('number')
		count = season.get('episode_count') or 0
		control.add_directory_item(
			control.langf(33055, number, count),
			{'action': 'season_episodes', 'entry': _encode(show),
			 'season': str(number)},
			is_folder=True,
			art=dict(show.get('art') or {}),
			info={'mediatype': 'season',
				  'title': control.langf(33055, number, count),
				  'season': number,
				  'plot': season.get('overview', '') or show.get('plot', '')})
	control.end_directory(cache_to_disc=False, content='seasons')


def episodes_menu(show, season_number):
	entries = trakt.season_episodes(show, season_number)
	if not entries:
		return nothing_here(33054)
	autoplay = control.get_bool('results.autoplay', False)
	for entry in entries:
		_add_episode_item(entry, autoplay)
	control.end_directory(cache_to_disc=False, content='episodes')


def sources_menu(entry):
	if not _preflight():
		return nothing_here(33093, {'action': 'settings'})

	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33011))

	def cb(percent, count):
		pd.update(percent, control.langf(33025, count))

	try:
		sources = scrapers.scrape(entry, progress_cb=cb)
	finally:
		pd.close()

	if sources is scrapers.MODULE_MISSING:
		return nothing_here(33008, {'action': 'settings'})
	if sources == scrapers.NO_PROVIDERS:
		if control.yesno_dialog(33042):
			scrapers.open_settings()
		return nothing_here(33008, {'action': 'coco_settings'})
	if not sources:
		return nothing_here(33012)

	sources, cache_known = scrapers.annotate_cached(sources)
	if not sources:
		return nothing_here(33045, {'action': 'settings'})

	for source in sources:
		_add_source_item(source, entry, cache_known)

	control.end_directory(content='files')


# Per-quality label colour (Kodi AARRGGBB hex). Higher tiers stand out.
_QUALITY_COLOR = {
	'4K': 'FFFFD700',    # gold
	'1080p': 'FF32CD32',  # lime green
	'720p': 'FF1E90FF',   # dodger blue
	'SD': 'FFB0B0B0',     # grey
	'SCR': 'FFFF8C00',    # dark orange
	'CAM': 'FFFF4444',    # red
}
_DEFAULT_QUALITY_COLOR = 'FFB0B0B0'


def _color(text, hex_color):
	return '[COLOR %s]%s[/COLOR]' % (hex_color, text)


def _add_source_item(source, entry, cache_known=False):
	quality = source.get('quality', 'SD')
	provider = source.get('provider', '')
	seeders = source.get('seeders', 0)
	try:
		size = float(source.get('size', 0) or 0)
	except (ValueError, TypeError):
		size = 0
	size_gb = '%.2f GB' % size if size else '?'
	info_line = source.get('info', '')
	prefix = ''
	if cache_known:
		holders = source.get('cached_by') or []
		if holders:
			tags = '/'.join(debrid.TAGS.get(n, n) for n in holders)
			prefix = '[COLOR lime][%s+][/COLOR] ' % tags
		else:
			prefix = '[COLOR grey][Download][/COLOR] '
	# Colour the quality badge by tier so 4K/1080p/720p are scannable at a glance.
	quality_badge = _color('[B]%s[/B]' % quality,
						   _QUALITY_COLOR.get(quality, _DEFAULT_QUALITY_COLOR))
	# A pack is worth flagging: its size covers a whole season, so the size
	# column reads oddly next to single episodes.
	if scrapers.is_packed(source):
		prefix += '[COLOR deepskyblue][%s][/COLOR] ' % control.lang(33089)
	label = '%s%s | %s | S:%s | %s | [I]%s[/I]' % (
		prefix, quality_badge, size_gb, seeders, provider,
		source.get('name', '')[:80])
	if info_line:
		label += ' | %s' % info_line

	payload = _encode({'source': source, 'entry': entry})
	control.add_directory_item(
		label,
		{'action': 'play', 'data': payload},
		is_folder=False,
		is_playable=True,
		art=dict(entry.get('art') or {}),
		info=player.media_info(entry))


def autoplay(entry):
	if not _preflight():
		control.resolve_failed()
		return
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33011))
	try:
		sources = scrapers.scrape(entry)
	finally:
		pd.close()
	if sources == scrapers.NO_PROVIDERS:
		sources = []
	if not sources:
		control.notify(33012)
		control.resolve_failed()
		return
	player.play(sources[0], entry)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _preflight():
	"""Ensure prerequisites are met before scraping/playing."""
	if not scrapers.available():
		control.ok_dialog(33008)
		return False
	if not debrid.any_authorized():
		# A notification is too small for this: it is the reason nothing
		# will play, and it names two services and where to set them up.
		control.ok_dialog(control.lang(33007),
						  heading=control.lang(33108))
		return False
	if not trakt.authorized():
		control.notify(33006)
		return False
	# An expired or non-premium account fails every single resolve, so say
	# so up front rather than after four failed attempts. Only fatal when
	# *no* provider can serve: with two set up, one lapsing is not a
	# problem worth stopping for - the other takes over on its own.
	ok, message = debrid.account_status()
	if not ok:
		# The message already names each provider and its own complaint;
		# heading it "Authorize Real-Debrid" blamed Real-Debrid for a
		# TorBox-only failure.
		control.ok_dialog(message, heading=control.lang(33108))
		return False
	_warn_if_expiring()
	return True


_EXPIRY_WARNED = 'debrid_expiry_warned'


def _warn_if_expiring():
	"""Mention a subscription about to run out, at most once a day.

	Worth saying before it lapses rather than after: with a second service
	set up nothing will visibly break when it does, so the only signal
	would be one provider quietly doing all the work.

	The date last warned on is kept in the add-on's own cache rather than in
	a setting. It is bookkeeping, not a preference, and it is written from a
	path that runs during ordinary browsing - and a setting write is not a
	quiet thing in Kodi. setSetting() rewrites the whole stored settings
	file, and when the settings dialog happens to be open it is injected
	into that dialog instead of being saved. Neither belongs behind a
	notification.
	"""
	try:
		soon = debrid.expiring_soon()
	except Exception:
		return
	if not soon:
		return
	today = time.strftime('%Y-%m-%d')
	if cache.get(_EXPIRY_WARNED) == today:
		return
	cache.set(_EXPIRY_WARNED, today, hours=48)
	for name, days in soon:
		control.notify(control.langf(33102, name, days))


def mark_watched(entry):
	if trakt.add_to_history(entry):
		control.notify(33023)
		xbmc.executebuiltin('Container.Refresh')
	else:
		control.notify(33018)


def mark_watched_through(entry):
	"""Mark everything up to and including this episode as watched.

	For a show picked up part-way through, where the alternative is
	marking each earlier episode by hand. Already-watched episodes are
	left alone: Trakt's history records plays rather than flags, so
	re-adding one would count as a second viewing.
	"""
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33096))
	try:
		count, seasons = trakt.count_unwatched_through(entry)
	finally:
		pd.close()

	label = player.display_label(entry)
	if not count:
		control.ok_dialog(control.langf(33099, label))
		return
	if not control.yesno_dialog(control.langf(33097, count, label),
								heading=control.lang(33095)):
		return
	marked = trakt.mark_watched_through(entry, seasons)
	if marked:
		control.notify(control.langf(33098, marked))
		xbmc.executebuiltin('Container.Refresh')
	else:
		control.notify(33018)


def hide_show(entry):
	"""Hide this show from Trakt's progress, and so from Next Episodes."""
	title = entry.get('show_title', '')
	if not control.yesno_dialog(control.langf(33077, title),
								heading=control.lang(33076)):
		return
	if trakt.hide_show(entry.get('show_trakt')):
		control.notify(control.langf(33078, title))
		xbmc.executebuiltin('Container.Refresh')
	else:
		control.notify(33018)


def unhide_show(entry):
	if trakt.unhide_show(entry.get('show_trakt')):
		control.notify(control.langf(33080, entry.get('show_title', '')))
		xbmc.executebuiltin('Container.Refresh')
	else:
		control.notify(33018)


def continue_watching_menu():
	"""Everything part-watched, from the same Trakt records resume uses."""
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33009))
	try:
		entries = trakt.in_progress()
	finally:
		pd.close()

	if not entries:
		return nothing_here(33087)

	autoplay = control.get_bool('results.autoplay', False)
	for entry in entries:
		suffix = _resume_suffix(entry)
		if player.is_movie(entry):
			_add_movie_item(entry, suffix=suffix)
		else:
			_add_episode_item(entry, autoplay, suffix=suffix)
	# Same reasoning as Next Episodes: finishing something changes this list
	# while you are away from it.
	control.end_directory(cache_to_disc=False, content='videos')


def refresh_sources(entry):
	"""Throw away this item's cached source list and scrape it again."""
	scrapers.forget(entry)
	control.notify(33088)
	sources_menu(entry)


def hidden_shows_menu():
	shows = trakt.hidden_shows()
	if not shows:
		return nothing_here(33081)
	for show in shows:
		year = show.get('show_year')
		label = '%s (%s)' % (show.get('show_title', ''), year) if year \
			else show.get('show_title', '')
		control.add_directory_item(
			label,
			{'action': 'unhide_show', 'entry': _encode(show)},
			is_folder=False,
			art={'icon': control.addon_icon},
			info={'mediatype': 'tvshow', 'title': show.get('show_title', ''),
				  'plot': control.lang(33082)})
	control.end_directory(cache_to_disc=False, content='tvshows')


def _auth(fn):
	fn()
	xbmc.executebuiltin('Container.Refresh')


def _revoke(fn):
	fn()
	control.notify(33019)
	xbmc.executebuiltin('Container.Refresh')


def torbox_check():
	"""Report whether the stored TorBox key works."""
	from resources.lib import torbox
	ok, message = torbox.account_status()
	control.ok_dialog(message, heading=control.lang(33070))


def upload_logs():
	"""Collect a redacted diagnostics report and put it in a secret gist."""
	from resources.lib import diagnostics
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33060))
	try:
		report = diagnostics.collect()
		url, error = diagnostics.upload(report)
	except Exception:
		control.error('log upload failed')
		url, error = None, 'Unexpected error while collecting logs'
	finally:
		pd.close()
	if url:
		control.log('diagnostics uploaded: %s' % url)
		control.ok_dialog(control.langf(33061, url), heading=control.lang(33059))
	else:
		control.ok_dialog(error or control.lang(33062), heading=control.lang(33059))


def clear_cache():
	if cache.clear():
		control.notify(33020)


def open_coco_settings():
	if not scrapers.open_settings():
		control.ok_dialog(33008)
