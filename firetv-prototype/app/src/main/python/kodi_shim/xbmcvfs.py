# -*- coding: utf-8 -*-
"""Minimal xbmcvfs replacement backed by the ordinary filesystem."""

import os
import shutil


def translatePath(path):
    return path


def exists(path):
    return os.path.exists(path)


def mkdir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def mkdirs(path):
    return mkdir(path)


def delete(path):
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def rmdir(path, force=False):
    try:
        shutil.rmtree(path) if force else os.rmdir(path)
        return True
    except Exception:
        return False


def rename(source, destination):
    try:
        os.replace(source, destination)
        return True
    except Exception:
        return False


def copy(source, destination):
    try:
        shutil.copyfile(source, destination)
        return True
    except Exception:
        return False


def listdir(path):
    dirs, files = [], []
    try:
        for name in os.listdir(path):
            (dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
    except Exception:
        pass
    return dirs, files


class File(object):
    def __init__(self, path, mode='r'):
        if 'b' not in mode:
            self._handle = open(path, mode, encoding='utf-8', errors='replace')
        else:
            self._handle = open(path, mode)

    def read(self, num_bytes=-1):
        return self._handle.read() if num_bytes < 0 else self._handle.read(num_bytes)

    def readBytes(self, num_bytes=-1):
        return self.read(num_bytes)

    def write(self, data):
        self._handle.write(data)
        return True

    def size(self):
        try:
            current = self._handle.tell()
            self._handle.seek(0, 2)
            size = self._handle.tell()
            self._handle.seek(current)
            return size
        except Exception:
            return 0

    def seek(self, offset, whence=0):
        return self._handle.seek(offset, whence)

    def close(self):
        try:
            self._handle.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
