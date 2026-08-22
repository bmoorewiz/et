# -*- coding: utf-8 -*-
"""Benching a debrid provider that has stopped answering.

Playback walks a queue of up to twelve sources, asking each provider about
every one. When a provider is simply not reachable - the box lost DNS, the
service is down, the wifi dropped - each of those asks waits out the full
network timeout, and the queue turns a failed playback into several minutes
of nothing happening.

This is the memory that stops that: two transport failures in a row and the
provider is set aside for a few minutes. A refusal by the service is not a
transport failure, so a rejected key or an uncached torrent never benches
anything - only silence does.

Kept in the add-on's cache rather than in memory, because the source list
and the playback attempt are separate plugin processes and a provider that
timed out building the list should not have to time out again to prove it.
"""

import time

from resources.lib import cache
from resources.lib import control

# One blip is not a pattern; two in a row is.
FAILURES_BEFORE_BENCH = 2
BENCH_SECONDS = 300

_PREFIX = 'health_'


def _key(name):
	return _PREFIX + (name or '').lower().replace(' ', '')


def _state(name):
	return cache.get(_key(name)) or {'failures': 0, 'until': 0}


def _store(name, state):
	# Just past the bench, so an expired entry cannot linger.
	cache.set(_key(name), state, hours=(BENCH_SECONDS + 60) / 3600.0)


def record_failure(name):
	"""A request to this provider got no answer at all."""
	state = _state(name)
	already = (state.get('until') or 0) > time.time()
	state['failures'] = int(state.get('failures') or 0) + 1
	if state['failures'] >= FAILURES_BEFORE_BENCH:
		state['until'] = time.time() + BENCH_SECONDS
		if not already:
			# A fresh bench gets a fresh trial request. Not a renewed one:
			# that failure *was* the trial, and handing out another every
			# time one fails would be no bench at all.
			state['probed'] = False
			control.log('%s is not answering; setting it aside for %d minutes'
						% (name, BENCH_SECONDS // 60))
	_store(name, state)
	return state['failures']


def record_success(name):
	"""A request came back. Whatever it said, the provider is reachable."""
	if _state(name)['failures']:
		cache.delete(_key(name))


def benched(name, now=None):
	"""Is this provider currently set aside? Asking does not change it."""
	now = time.time() if now is None else now
	return (_state(name).get('until') or 0) > now


def probe_available(name, now=None):
	"""Is the one trial request for this bench still unused?

	A bench with no way out until the clock runs down turns a half-minute
	of bad wifi into five minutes of nothing playing, which is what
	happened: two timeouts benched TorBox, and every source after that
	failed instantly against a service that was already answering again.
	One trial request per bench is the missing half-open state - cheap
	when the provider is still down, and immediate when it is not.
	"""
	return benched(name, now) and not _state(name).get('probed')


def take_probe(name, now=None):
	"""Claim the trial request. True if this call may go ahead."""
	if not probe_available(name, now):
		return False
	state = _state(name)
	state['probed'] = True
	_store(name, state)
	control.log('%s is benched; trying it once to see if it is back' % name)
	return True


def clear(name=None):
	if name:
		cache.delete(_key(name))
