# -*- coding: utf-8 -*-
"""Playback: resolve a chosen source through Real-Debrid, play it, and
scrobble progress back to Trakt."""


import time

import xbmcgui

from resources.lib import control
from resources.lib import debrid
from resources.lib import trakt


def is_movie(entry):
	return (entry or {}).get('media_type') == 'movie'


def display_label(entry):
	"""Human label for an episode or movie entry."""
	if is_movie(entry):
		year = entry.get('year')
		return '%s (%s)' % (entry.get('title', ''), year) if year \
			else entry.get('title', '')
	try:
		season = int(entry.get('season') or 0)
		episode = int(entry.get('episode') or 0)
	except (TypeError, ValueError):
		season, episode = 0, 0
	return '%s - %dx%02d - %s' % (entry.get('show_title', ''), season,
								  episode, entry.get('ep_title', ''))


def media_info(entry):
	"""Kodi info-label dict for an episode or movie entry."""
	if is_movie(entry):
		return {
			'mediatype': 'movie',
			'title': entry.get('title', ''),
			'plot': entry.get('plot', ''),
			'premiered': (entry.get('released') or '')[:10],
			'duration': (entry.get('runtime') or 0) * 60 or None,
		}
	info = {
		'mediatype': 'episode',
		'tvshowtitle': entry.get('show_title', ''),
		'title': entry.get('ep_title', ''),
		'season': entry.get('season'),
		'episode': entry.get('episode'),
		'plot': entry.get('plot', ''),
		'aired': (entry.get('first_aired') or '')[:10],
		'premiered': (entry.get('first_aired') or '')[:10],
	}
	# Kodi draws its own watched tick from this, so browsing a season shows
	# what has been seen without the add-on having to say so in the label.
	if entry.get('watched'):
		info['playcount'] = 1
	return info


def watched_threshold(entry):
	"""Percentage of playback after which an item counts as watched.

	Trakt's own /scrobble/stop marks an item watched at 80% server-side, so
	setting this above 80 does not stop Trakt doing that - it only controls
	whether this add-on also posts it to /sync/history.
	"""
	key = ('scrobble.threshold.movie' if is_movie(entry)
		   else 'scrobble.threshold.episode')
	return min(100, max(1, control.get_int(key, 80)))


def _magnet_for(source):
	url = source.get('url', '')
	if url.startswith('magnet:'):
		return url
	info_hash = source.get('hash')
	if info_hash:
		name = source.get('name', '')
		return 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, name)
	return url


def _resolve_one(source, entry):
	"""Resolve a single source, returning ``(url, error)``.

	Movies pass no season/episode, so Real-Debrid falls through to picking
	the largest video file in the torrent rather than episode-matching.

	The source's cache flags decide which provider is asked first, so the
	service that is named when playback starts is the one the source list
	said had it.
	"""
	if is_movie(entry):
		season, episode, title = None, None, entry.get('title', '')
	else:
		season = entry.get('season')
		episode = entry.get('episode')
		title = entry.get('show_title', '')
	return debrid.resolve_magnet(
		_magnet_for(source), source.get('hash', ''), season, episode, title,
		cached_by=source.get('cached_by'))


def runtime_seconds(entry):
	try:
		return int(entry.get('runtime') or 0) * 60
	except (TypeError, ValueError):
		return 0


def _apply_resume(entry):
	"""Ask about, and record, a Trakt resume point for this item.

	Trakt stores progress as a percentage, so the second count is derived
	from the item's runtime - close enough to land in the right place, and
	the only option since the real file duration is unknown until playback
	starts. The decision is stashed for the service to apply.
	"""
	mode = control.get_int('playback.resume', 0)  # 0 ask, 1 always, 2 never
	if mode == 2:
		return
	try:
		percent = trakt.playback_progress(entry)
	except Exception:
		control.error('resume lookup failed')
		return
	# Ignore the extremes: barely started, or effectively finished.
	if percent < 1 or percent > 95:
		return
	total = runtime_seconds(entry)
	if not total:
		return
	seconds = int(total * percent / 100.0)

	if mode == 0:
		label = time.strftime('%H:%M:%S', time.gmtime(seconds))
		if not control.yesno_dialog(control.langf(33072, label),
									heading=control.lang(33073)):
			# Starting over should not leave a stale resume point behind.
			try:
				trakt.clear_playback(entry)
			except Exception:
				pass
			return
	control.log('resuming %s at %.1f%% (~%ds)'
				% (display_label(entry), percent, seconds))
	from resources.lib import scrobbler
	scrobbler.announce_resume(percent, seconds)


def provider_tag(name):
	"""Short label for a provider, e.g. Real-Debrid -> RD."""
	return debrid.TAGS.get(name, name or '?')


# How many sources to try per quality tier, best first, before giving up.
# CAM and screener results are counted in the SD bucket.
_TIERS = (
	('4K', 'playback.try.4k', 5),
	('1080p', 'playback.try.1080p', 5),
	('720p', 'playback.try.720p', 10),
	('SD', 'playback.try.sd', 0),
)
_TIER_OF = {'4K': '4K', '1080p': '1080p', '720p': '720p',
			'SD': 'SD', 'CAM': 'SD', 'SCR': 'SD'}


def _tier(source):
	return _TIER_OF.get(source.get('quality', 'SD'), 'SD')


def _source_key(source):
	return (source.get('hash') or source.get('url') or '').lower()


def _candidates(source, entry):
	"""Build the fallback queue: the chosen source, then a per-quality budget.

	Rather than a flat number of attempts, each quality tier gets its own
	allowance and they are spent best-first - so a failing episode burns
	through the good 4K/1080p options before dropping to 720p, and stops
	instead of grinding through hundreds of low-quality results. The chosen
	source is always tried first and counts against its own tier.
	"""
	queue = [source]
	if not control.get_bool('playback.autonext', True):
		return queue
	try:
		from resources.lib import scrapers
		ranked = scrapers.cached_sources(entry) or []
	except Exception:
		return queue

	quotas = {tier: max(0, control.get_int(key, default))
			  for tier, key, default in _TIERS}
	used = {tier: 0 for tier, _key, _default in _TIERS}
	used[_tier(source)] = 1
	seen = {_source_key(source)}

	for tier, _key, _default in _TIERS:
		if used[tier] >= quotas[tier]:
			continue
		for item in ranked:
			key = _source_key(item)
			if not key or key in seen or _tier(item) != tier:
				continue
			seen.add(key)
			queue.append(item)
			used[tier] += 1
			if used[tier] >= quotas[tier]:
				break

	control.log('fallback queue: %d source(s) - %s'
				% (len(queue), ', '.join('%s %d/%d' % (t, used[t], quotas[t])
										 for t, _k, _d in _TIERS if quotas[t])))
	return queue


def untried_tiers(source, entry):
	"""Tiers that had sources available but no budget to try them.

	Worth saying out loud on a failure. A film that only exists as a CAM
	will fail every time with the default budgets - not because nothing is
	available, but because the SD allowance is zero - and without this the
	user is left thinking there were no sources at all.
	"""
	try:
		from resources.lib import scrapers
		ranked = scrapers.cached_sources(entry) or []
	except Exception:
		return []
	skipped = []
	for tier, key, default in _TIERS:
		if max(0, control.get_int(key, default)) > 0:
			continue
		available = sum(1 for item in ranked if _tier(item) == tier)
		if available:
			skipped.append((tier, available))
	return skipped


def play(source, entry):
	"""Resolve and play, falling through to the next ranked source on failure."""
	# The per-tier budgets in _candidates() bound this; there is deliberately
	# no separate flat cap, which would otherwise silently truncate them.
	queue = _candidates(source, entry)

	pd = control.progress_bg
	# Name the service that will actually be tried. This said "Real-Debrid"
	# unconditionally, so an install running on TorBox alone was told its
	# dead provider was doing the work - and the caption is the only thing
	# on screen during a resolve.
	pd.create(control.addon_name,
			  control.langf(33013, ', '.join(debrid.providers()) or '-'))
	resolved, error, provider = None, None, None
	try:
		for position, candidate in enumerate(queue, 1):
			pd.update(int((position - 1) * 100 / len(queue)),
					  control.langf(33043, position, len(queue),
									candidate.get('quality', '')))
			resolved, error, provider = _resolve_one(candidate, entry)
			if resolved:
				source = candidate
				break
			control.log('source %d/%d failed: %s' % (position, len(queue), error))
			if control.aborted():
				break
	finally:
		pd.close()

	if not resolved:
		# Show why, rather than a generic failure. The underlying reason is the
		# whole value here, so never let a missing translation swallow it.
		message = error or control.lang(33014) or 'Could not resolve a playable link.'
		if len(queue) > 1:
			wrapped = control.langf(33044, len(queue), message)
			message = (wrapped if wrapped.strip() and message in wrapped
					   else '%s (tried %d sources)' % (message, len(queue)))
		skipped = untried_tiers(source, entry)
		if skipped:
			message += '[CR][CR]' + control.langf(
				33083, ', '.join('%d %s' % (count, tier)
								 for tier, count in skipped))
		control.ok_dialog(message,
						  heading=control.lang(33014) or control.addon_name)
		control.resolve_failed()
		return

	# Say which service is actually serving this, since with both enabled
	# the cache flags on the source list only predict it.
	control.log('playing %s via %s (%s)'
				% (display_label(entry), provider, source.get('quality', '?')))
	if provider and control.get_bool('playback.show_provider', True):
		control.notify(control.langf(33071, provider))

	# Resuming is done by the service, so only offer it when the service will
	# be watching this playback - otherwise the prompt would have no effect.
	tracked = control.get_bool('scrobble.enabled', True) and trakt.authorized()
	if tracked:
		_apply_resume(entry)

	item = xbmcgui.ListItem(path=resolved)
	label = display_label(entry)
	if provider and control.get_bool('playback.label_provider', False):
		label = '%s  [%s]' % (label, provider_tag(provider))
	item.setLabel(label)
	info = media_info(entry)
	try:
		tag = item.getVideoInfoTag()
		control._apply_info_tag(tag, info)
	except Exception:
		item.setInfo('video', info)
	# The item's own artwork, so the player OSD and the "now playing" widget
	# show the episode rather than the add-on's generic icon.
	art = {'icon': control.addon_icon, 'thumb': control.addon_icon,
		   'fanart': control.addon_fanart}
	art.update({k: v for k, v in (entry.get('art') or {}).items() if v})
	item.setArt(art)

	# Hand the item to the background service BEFORE resolving. Kodi destroys
	# this plugin process moments after setResolvedUrl(), so anything started
	# here would be killed long before playback ends; the service outlives us
	# and does the tracking.
	if tracked:
		from resources.lib import scrobbler
		scrobbler.announce(entry)

	control.resolve(item)
