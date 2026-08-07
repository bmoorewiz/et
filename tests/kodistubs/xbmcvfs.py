# -*- coding: utf-8 -*-
"""Stub for Kodi's ``xbmcvfs`` module.

Backed by the real filesystem. An earlier hand-rolled version answered
exists() from a dictionary, which made the diagnostics log-reading tests
pass while the real code path could never have found a file.
"""

import os
import shutil


def translatePath(path):
	return path


def exists(path):
	# Kodi treats a trailing separator as "directory"; os.path.exists is
	# happy either way, which matches closely enough for our uses.
	return os.path.exists(path)


def mkdirs(path):
	try:
		os.makedirs(path, exist_ok=True)
		return True
	except OSError:
		return False


def mkdir(path):
	try:
		os.mkdir(path)
		return True
	except OSError:
		return False


def delete(path):
	try:
		os.remove(path)
		return True
	except OSError:
		return False


def rmdir(path, force=False):
	try:
		shutil.rmtree(path) if force else os.rmdir(path)
		return True
	except OSError:
		return False


def listdir(path):
	dirs, files = [], []
	try:
		for name in os.listdir(path):
			(dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
	except OSError:
		pass
	return dirs, files


def copy(source, destination):
	try:
		shutil.copyfile(source, destination)
		return True
	except OSError:
		return False


class File(object):
	def __init__(self, path, mode='r'):
		self._handle = open(path, mode if 'b' in mode else mode + 'b')

	def read(self):
		return self._handle.read().decode('utf-8', errors='replace')

	def readBytes(self):
		return self._handle.read()

	def write(self, data):
		if isinstance(data, str):
			data = data.encode('utf-8')
		self._handle.write(data)
		return True

	def close(self):
		self._handle.close()

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
