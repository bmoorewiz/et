# -*- coding: utf-8 -*-
"""Shared test scaffolding: base case, fake clock and fake HTTP."""

import json
import socket
import unittest
from urllib.parse import urlparse, parse_qs

import kodistubs
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from resources.lib import control

ADDON_ROOT = kodistubs.ADDON_ROOT


def block_network():
	"""Make any real outbound connection an immediate, obvious failure.

	Every test that touches Trakt, Real-Debrid or TorBox installs a fake
	transport. This guarantees that one which forgets does not quietly hit
	the live API - and that the suite runs offline.
	"""
	def refuse(*args, **kwargs):
		raise AssertionError(
			'a test tried to open a real network connection; '
			'install a FakeHTTP transport instead')

	socket.socket.connect = refuse
	socket.socket.connect_ex = refuse
	socket.create_connection = refuse


class FakeClock(object):
	"""A clock the tests move by hand.

	Real-Debrid's resolve loop waits up to 15 seconds and TorBox's the same.
	Letting those elapse for real would make the suite unusable, so the
	modules' ``time`` and Kodi's sleep are both pointed here instead.
	"""

	def __init__(self, start=1700000000.0):
		self.now = float(start)

	def time(self):
		return self.now

	def sleep(self, seconds):
		self.now += float(seconds)

	def advance(self, seconds):
		self.now += float(seconds)


class FakeResponse(object):
	def __init__(self, payload=None, status_code=200, text=None):
		self.status_code = status_code
		if text is not None:
			self._body = text
		elif payload is None:
			self._body = ''
		else:
			self._body = json.dumps(payload)
		self.content = self._body.encode('utf-8')
		self.text = self._body

	def json(self):
		if not self._body:
			raise ValueError('no content')
		return json.loads(self._body)


class FakeHTTP(object):
	"""A requests-Session-shaped object driven by registered route handlers.

	Routes match on ``(METHOD, path)`` where path is the URL path with the
	API's base prefix still attached, e.g. ``/rest/1.0/torrents/addMagnet``.
	A handler receives a small request record and returns either a
	FakeResponse, a payload dict (wrapped in a 200), or raises to simulate a
	transport failure.
	"""

	def __init__(self):
		self.routes = {}
		self.calls = []

	def route(self, method, path, handler):
		self.routes[(method.upper(), path)] = handler
		return self

	def json_route(self, method, path, payload, status_code=200):
		return self.route(method, path,
						  lambda request: FakeResponse(payload, status_code))

	# -- requests.Session surface -------------------------------------------

	def request(self, method, url, **kwargs):
		parsed = urlparse(url)
		record = {
			'method': method.upper(),
			'url': url,
			'path': parsed.path,
			'query': {k: v[0] for k, v in parse_qs(parsed.query).items()},
			'params': kwargs.get('params') or {},
			'data': kwargs.get('data'),
			'json': kwargs.get('json'),
			'headers': kwargs.get('headers') or {},
		}
		self.calls.append(record)
		handler = self.routes.get((record['method'], parsed.path))
		if handler is None:
			raise AssertionError('unrouted %s %s' % (record['method'], parsed.path))
		result = handler(record)
		return result if isinstance(result, FakeResponse) else FakeResponse(result)

	def get(self, url, **kwargs):
		return self.request('GET', url, **kwargs)

	def post(self, url, **kwargs):
		return self.request('POST', url, **kwargs)

	def put(self, url, **kwargs):
		return self.request('PUT', url, **kwargs)

	def delete(self, url, **kwargs):
		return self.request('DELETE', url, **kwargs)

	def mount(self, prefix, adapter):
		pass

	# -- assertions ----------------------------------------------------------

	def paths(self, method=None):
		return [c['path'] for c in self.calls
				if method is None or c['method'] == method.upper()]

	def called(self, method, path):
		return any(c['method'] == method.upper() and c['path'] == path
				   for c in self.calls)


class AddonTestCase(unittest.TestCase):
	"""Base case: fresh stub state and a clean cache for every test."""

	def setUp(self):
		kodistubs.reset()
		from resources.lib import cache
		cache.clear()
		self.addCleanup(kodistubs.reset)

	# -- helpers -------------------------------------------------------------

	def set(self, **settings):
		"""Set add-on settings, e.g. ``self.set(**{'rd.token': 'x'})``."""
		for key, value in settings.items():
			if isinstance(value, bool):
				value = 'true' if value else 'false'
			control.set_setting(key, value)

	def use_clock(self, *modules, **kwargs):
		"""Point modules' ``time`` and Kodi's sleep at a fake clock."""
		clock = FakeClock(**kwargs)
		from unittest import mock
		for module in modules:
			patcher = mock.patch.object(module, 'time', clock)
			patcher.start()
			self.addCleanup(patcher.stop)
		patcher = mock.patch.object(control, 'sleep',
									lambda ms: clock.sleep(ms / 1000.0))
		patcher.start()
		self.addCleanup(patcher.stop)
		return clock

	def use_http(self, module, http=None):
		"""Swap a module's requests Session for a FakeHTTP."""
		from unittest import mock
		http = http or FakeHTTP()
		patcher = mock.patch.object(module, '_session', http)
		patcher.start()
		self.addCleanup(patcher.stop)
		return http

	def answer_yes(self, *answers):
		xbmcgui.YESNO_QUEUE.extend(answers if answers else [True])

	def dialogs(self, kind=None):
		return [d for d in xbmcgui.DIALOGS if kind is None or d[0] == kind]

	def last_dialog(self, kind=None):
		found = self.dialogs(kind)
		return found[-1] if found else None

	def builtins(self):
		return list(xbmc.BUILTINS)

	def items(self):
		return list(xbmcplugin.ITEMS)

	def logged(self):
		return '\n'.join(message for _level, message in xbmc.LOG)
