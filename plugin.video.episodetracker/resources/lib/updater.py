# -*- coding: utf-8 -*-
"""Self-update: check GitHub for a newer release zip and install it.

The addon's own ``addon.xml`` is fetched from the configured repo/branch and
its version compared against the installed one. If a newer version exists the
user is asked to confirm, then the matching zip is downloaded, validated and
extracted over the installed add-on.
"""

import os
import re
import shutil
import zipfile
import threading
import xml.etree.ElementTree as ElementTree

import requests

import xbmc
import xbmcvfs

from resources.lib import control
from resources.lib import cache
from resources.lib import credentials

ADDON_ID = 'plugin.video.episodetracker'
DEFAULT_REPO = 'bmoorewiz/et'
DEFAULT_BRANCH = 'claude/episode-tracker-kodi-app-qu73ll'

_CACHE_KEY = 'update_check'
_TIMEOUT = 15


def _repo():
	return control.setting('updates.repo', DEFAULT_REPO) or DEFAULT_REPO


def _branch():
	return control.setting('updates.branch', DEFAULT_BRANCH) or DEFAULT_BRANCH


def _token():
	return control.setting('updates.token', '')


def manifest_path():
	return '%s/addon.xml' % ADDON_ID


def zip_path(version):
	return '%s-%s.zip' % (ADDON_ID, version)


def _fetch(path, stream=False):
	"""Fetch a file from the configured repo/branch.

	Public repos are read straight from raw.githubusercontent.com with no
	credentials. If an access token is configured the GitHub contents API is
	used instead, which is what private repos require.

	The ``refs/heads/`` raw form and the API's separate ``ref`` parameter both
	keep branch names containing slashes unambiguous.
	"""
	token = _token()
	if token:
		url = 'https://api.github.com/repos/%s/contents/%s' % (_repo(), path)
		headers = {
			'Accept': 'application/vnd.github.raw',
			'Authorization': 'Bearer %s' % token,
			'X-GitHub-Api-Version': '2022-11-28',
		}
		params = {'ref': _branch()}
	else:
		url = 'https://raw.githubusercontent.com/%s/refs/heads/%s/%s' % (
			_repo(), _branch(), path)
		headers = {}
		params = None
	try:
		resp = requests.get(url, headers=headers, params=params,
							stream=stream, timeout=_TIMEOUT)
	except Exception:
		control.error('update fetch failed: %s' % path)
		return None
	if resp.status_code != 200:
		control.debug('update fetch %s -> http %s%s' % (
			path, resp.status_code,
			' (private repo needs an access token)'
			if resp.status_code in (403, 404) and not token else ''))
		return None
	return resp


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

def parse_version(text):
	"""Turn '1.2.3' (or '1.2.3~beta1') into a comparable tuple of ints."""
	parts = re.split(r'[.\-+~]', (text or '').strip())
	numbers = []
	for part in parts:
		match = re.match(r'^(\d+)', part)
		numbers.append(int(match.group(1)) if match else 0)
	return tuple(numbers) or (0,)


def compare_versions(remote, local):
	"""Return True when remote is strictly newer than local."""
	a, b = parse_version(remote), parse_version(local)
	length = max(len(a), len(b))
	a = a + (0,) * (length - len(a))
	b = b + (0,) * (length - len(b))
	return a > b


def installed_version():
	return control.addon_version


# ---------------------------------------------------------------------------
# Remote lookup
# ---------------------------------------------------------------------------

def remote_version():
	"""Fetch the published addon.xml and return its version string."""
	resp = _fetch(manifest_path())
	if resp is None:
		return None
	try:
		root = ElementTree.fromstring(resp.content)
		# the <addon> root carries the addon version; <import> children have
		# their own version attributes, so only read the root's.
		version = root.get('version')
		if root.get('id') and root.get('id') != ADDON_ID:
			return None
		return version
	except Exception:
		control.error('remote version lookup failed')
		return None


def check(force=False):
	"""Return the remote version string if an update is available, else None.

	Results are cached for the configured interval so repeated menu visits do
	not hit the network.
	"""
	if not force:
		cached = cache.get(_CACHE_KEY)
		if cached is not None:
			version = cached.get('version')
			if version and compare_versions(version, installed_version()):
				return version
			return None

	version = remote_version()
	if version is None:
		return None

	hours = max(1, control.get_int('updates.interval', 24))
	cache.set(_CACHE_KEY, {'version': version}, hours=hours)

	if compare_versions(version, installed_version()):
		return version
	return None


def auto_check():
	"""Kick off a throttled background check; notifies if an update is found.

	Safe to call while building a directory - it never blocks the UI.
	"""
	if not control.get_bool('updates.enabled', True):
		return

	def worker():
		try:
			version = check(force=False)
			if version:
				control.notify(control.langf(33031, version))
		except Exception:
			control.error('auto update check failed')

	thread = threading.Thread(target=worker)
	thread.daemon = True
	thread.start()


def pending_update():
	"""Return a cached available-update version without any network access."""
	cached = cache.get(_CACHE_KEY)
	if not cached:
		return None
	version = cached.get('version')
	if version and compare_versions(version, installed_version()):
		return version
	return None


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def check_and_prompt():
	"""Manual 'check for updates' - shows progress and result to the user."""
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33030))
	try:
		version = remote_version()
	finally:
		pd.close()

	if version is None:
		control.ok_dialog(33038)
		return False

	hours = max(1, control.get_int('updates.interval', 24))
	cache.set(_CACHE_KEY, {'version': version}, hours=hours)

	if not compare_versions(version, installed_version()):
		control.ok_dialog(control.langf(33032, installed_version()))
		return False

	return prompt_install(version)


def prompt_install(version):
	if not control.yesno_dialog(
			control.langf(33033, version, installed_version())):
		return False
	return install(version)


def install(version):
	"""Download, validate and extract the given version. Returns success."""
	control.make_profile()
	# Snapshot the credentials before anything is written. The extract only
	# touches special://home/addons and the stored values live in
	# addon_data, so in principle they are never at risk - but "in
	# principle" is what the last three releases each assumed, and an
	# update that loses the TorBox API key is not recoverable from here.
	credentials.sync()
	temp_dir = xbmcvfs.translatePath('special://temp/')
	local_zip = os.path.join(temp_dir, '%s-%s.zip' % (ADDON_ID, version))

	if not _download(zip_path(version), local_zip):
		control.notify(33037)
		return False

	addons_dir = xbmcvfs.translatePath('special://home/addons/')
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33035))
	try:
		with zipfile.ZipFile(local_zip, 'r') as archive:
			if not _validate(archive):
				control.notify(33037)
				return False
			archive.extractall(addons_dir)
	except Exception:
		control.error('update extract failed')
		control.notify(33037)
		return False
	finally:
		pd.close()
		try:
			os.remove(local_zip)
		except Exception:
			pass

	# Drop stale bytecode so the new sources are definitely what gets loaded.
	_purge_pycache(os.path.join(addons_dir, ADDON_ID))

	# Put anything back that the new version cannot see. A setting only
	# survives an update while its id stays in the schema, so this is the
	# guarantee that an id changing by accident cannot cost the user a key.
	credentials.sync()

	cache.delete(_CACHE_KEY)
	xbmc.executebuiltin('UpdateLocalAddons')
	control.ok_dialog(control.langf(33036, version))
	if control.yesno_dialog(33039):
		xbmc.executebuiltin('RestartApp')
	return True


def _download(path, dest):
	pd = control.progress
	pd.create(control.addon_name, control.lang(33034))
	try:
		resp = _fetch(path, stream=True)
		if resp is None:
			return False
		total = int(resp.headers.get('content-length') or 0)
		written = 0
		with open(dest, 'wb') as handle:
			for chunk in resp.iter_content(chunk_size=32768):
				if not chunk:
					continue
				if pd.iscanceled() or control.aborted():
					return False
				handle.write(chunk)
				written += len(chunk)
				if total:
					pd.update(int(written * 100 / total), control.lang(33034))
		return written > 0
	except Exception:
		control.error('update download failed')
		return False
	finally:
		pd.close()


def _validate(archive):
	"""Reject anything that is not strictly our add-on folder (zip-slip safe)."""
	prefix = ADDON_ID + '/'
	has_manifest = False
	for name in archive.namelist():
		normalised = name.replace('\\', '/')
		if normalised.startswith('/') or os.path.isabs(normalised):
			control.debug('rejected absolute path in zip: %s' % name)
			return False
		if '..' in normalised.split('/'):
			control.debug('rejected traversal path in zip: %s' % name)
			return False
		if not normalised.startswith(prefix):
			control.debug('rejected out-of-tree path in zip: %s' % name)
			return False
		if normalised == prefix + 'addon.xml':
			has_manifest = True
	if not has_manifest:
		control.debug('zip has no %saddon.xml' % prefix)
	return has_manifest


def _purge_pycache(root):
	try:
		for dirpath, dirnames, _ in os.walk(root):
			for name in list(dirnames):
				if name == '__pycache__':
					shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
					dirnames.remove(name)
	except Exception:
		pass
