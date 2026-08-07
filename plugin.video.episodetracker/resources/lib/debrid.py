# -*- coding: utf-8 -*-
"""Debrid dispatcher.

Real-Debrid and TorBox expose the same contract, so everything above this
layer stays provider-agnostic. Both can be enabled at once: sources are
tried against each in the configured order, which is worth doing because a
torrent uncached on one is often cached on the other.
"""

from resources.lib import control
from resources.lib import realdebrid
from resources.lib import torbox

# label -> module
_MODULES = {'Real-Debrid': realdebrid, 'TorBox': torbox}
# short tags used in source labels
TAGS = {'Real-Debrid': 'RD', 'TorBox': 'TB'}


def _rd_enabled():
	# Real-Debrid has no explicit toggle: having a token is the switch, so
	# existing installs keep working untouched.
	return realdebrid.authorized()


def providers():
	"""Enabled providers, in the configured priority order."""
	active = []
	if _rd_enabled():
		active.append('Real-Debrid')
	if torbox.enabled():
		active.append('TorBox')
	# 0 = Real-Debrid first (default), 1 = TorBox first
	if control.get_int('debrid.priority', 0) == 1:
		active.reverse()
	return active


def module(name):
	return _MODULES[name]


def any_authorized():
	return bool(providers())


def account_status():
	"""``(ok, message)`` - ok when at least one enabled provider is usable."""
	names = providers()
	if not names:
		return False, ('No debrid provider is set up. Authorize Real-Debrid '
					   'or add a TorBox API key under Settings > Accounts.')
	messages, healthy = [], False
	for name in names:
		try:
			ok, message = _MODULES[name].account_status()
		except Exception:
			control.error('%s account check failed' % name)
			ok, message = False, 'could not be checked'
		healthy |= ok
		messages.append('%s: %s' % (name, message))
	return healthy, ' | '.join(messages)


def cached_hashes(hashes):
	"""Which provider has each hash cached.

	Returns ``(mapping, usable)`` where mapping is hash -> list of provider
	names. ``usable`` is True when at least one provider actually answered.
	"""
	mapping, usable = {}, False
	for name in providers():
		try:
			cached, provider_usable = _MODULES[name].cached_hashes(list(hashes))
		except Exception:
			control.error('%s cache check failed' % name)
			continue
		usable |= bool(provider_usable)
		for info_hash in cached:
			mapping.setdefault(info_hash, []).append(name)
	return mapping, usable


def resolve_magnet(magnet, info_hash, season=None, episode=None, title=''):
	"""Try each enabled provider in order. Returns ``(url, error)``."""
	names = providers()
	if not names:
		return None, 'No debrid provider is set up.'
	errors = []
	for name in names:
		try:
			url, error = _MODULES[name].resolve_magnet(
				magnet, info_hash, season, episode, title)
		except Exception as exc:
			control.error('%s resolve failed' % name)
			url, error = None, 'unexpected error: %s' % exc
		if url:
			control.log('resolved via %s' % name)
			return url, None
		errors.append('%s: %s' % (name, error))
	return None, ' | '.join(errors)
