#!/usr/bin/env python3
"""Build the Kodi repository tree under docs/.

Produces the standard layout Kodi expects:

    docs/
      addons.xml                 aggregated metadata for every add-on
      addons.xml.md5             checksum Kodi uses to detect changes
      index.html                 plain-anchor index so Kodi can browse it
      <addon.id>/<addon.id>-<version>.zip

Run from the repo root:  python3 tools/build_repo.py
"""

import os
import re
import sys
import html
import shutil
import hashlib
import zipfile
import xml.etree.ElementTree as ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')

# Add-on source folders that belong in the repository.
ADDONS = ['plugin.video.episodetracker', 'repository.episodetracker']

EXCLUDE_DIRS = {'__pycache__', '.git'}
EXCLUDE_EXT = ('.pyc', '.pyo')


def addon_meta(addon_id):
	path = os.path.join(ROOT, addon_id, 'addon.xml')
	root = ElementTree.parse(path).getroot()
	return root, root.get('version')


def build_zip(addon_id, version):
	"""Zip an add-on folder into docs/<id>/<id>-<version>.zip."""
	out_dir = os.path.join(DOCS, addon_id)
	os.makedirs(out_dir, exist_ok=True)

	# drop older zips of the same add-on so the tree stays tidy
	for old in os.listdir(out_dir):
		if old.endswith('.zip') and old != '%s-%s.zip' % (addon_id, version):
			os.remove(os.path.join(out_dir, old))

	dest = os.path.join(out_dir, '%s-%s.zip' % (addon_id, version))
	src_root = os.path.join(ROOT, addon_id)
	with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
		for dirpath, dirnames, filenames in os.walk(src_root):
			dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
			for filename in sorted(filenames):
				if filename.endswith(EXCLUDE_EXT):
					continue
				full = os.path.join(dirpath, filename)
				rel = os.path.relpath(full, ROOT)
				archive.write(full, rel)
	return dest


def build_addons_xml(entries):
	"""Concatenate each add-on's manifest into a single addons.xml."""
	parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<addons>']
	for root in entries:
		xml_text = ElementTree.tostring(root, encoding='unicode').strip()
		# strip any namespace noise and re-indent one level
		xml_text = re.sub(r'\s+xmlns:\w+="[^"]*"', '', xml_text)
		parts.append('\n'.join('\t' + line for line in xml_text.splitlines()))
	parts.append('</addons>')
	return '\n'.join(parts) + '\n'


def write_index(directory, names):
	"""Write the minimal listing Kodi's HTTP directory reader understands.

	This deliberately mirrors the format used by known-working Kodi source
	repositories (e.g. umbrellaplug.github.io), which is nothing but a
	doctype and one bare anchor per entry:

	    <!DOCTYPE html>
	    <a href="repository.umbrella-2.2.6.zip">repository.umbrella-2.2.6.zip</a>

	No html/head/body wrapper, no headings, no markup nested inside the
	anchors, and each href is a single path segment - Kodi treats every href
	as one entry in the current directory. GitHub Pages serves no directory
	listing of its own, so every directory needs one of these files.
	"""
	lines = ['<!DOCTYPE html>']
	for name in names:
		assert '/' not in name.rstrip('/') and not name.startswith('/'), \
			'href must be a single path segment: %r' % name
		escaped = html.escape(name)
		lines.append('<a href="%s">%s</a>' % (escaped, escaped))
	with open(os.path.join(directory, 'index.html'), 'w', encoding='utf-8') as handle:
		handle.write('\n'.join(lines) + '\n')


def main():
	os.makedirs(DOCS, exist_ok=True)
	roots, items = [], []

	for addon_id in ADDONS:
		root, version = addon_meta(addon_id)
		dest = build_zip(addon_id, version)
		size = os.path.getsize(dest)
		roots.append(root)
		items.append((addon_id, version, size))
		print('  packaged %-32s v%-8s %6d bytes' % (addon_id, version, size))

	addons_xml = build_addons_xml(roots)
	with open(os.path.join(DOCS, 'addons.xml'), 'w', encoding='utf-8') as handle:
		handle.write(addons_xml)

	digest = hashlib.md5(addons_xml.encode('utf-8')).hexdigest()
	with open(os.path.join(DOCS, 'addons.xml.md5'), 'w', encoding='utf-8') as handle:
		handle.write(digest + '\n')

	# Per-directory listings: Pages generates none, so without these,
	# browsing into a folder in Kodi shows nothing.
	for addon_id, version, _ in items:
		write_index(os.path.join(DOCS, addon_id),
					['%s-%s.zip' % (addon_id, version)])

	# Flat copies of every zip at the root. The root listing is what a Kodi
	# source URL lands on, so the zips must be installable from there
	# directly - the same shape working repositories use.
	current = ['%s-%s.zip' % (addon_id, version) for addon_id, version, _ in items]
	for stale in os.listdir(DOCS):
		if stale.endswith('.zip') and stale not in current:
			os.remove(os.path.join(DOCS, stale))
	for addon_id, version, _ in items:
		zip_name = '%s-%s.zip' % (addon_id, version)
		shutil.copyfile(os.path.join(DOCS, addon_id, zip_name),
						os.path.join(DOCS, zip_name))

	# repository zip first - it is what users install
	current.sort(key=lambda n: (not n.startswith('repository.'), n))
	write_index(DOCS, current)

	# Publish the same listing and zips at the repository root as well, so the
	# Pages site works whether its source folder is set to / or /docs. This
	# also mirrors how working Kodi repos are laid out (umbrellaplug.github.io
	# keeps index.html and its repository zip at the repo root). The root zip
	# is additionally the path the add-on's built-in updater downloads, so it
	# has to exist there regardless.
	for stale in os.listdir(ROOT):
		if stale.endswith('.zip') and stale not in current:
			os.remove(os.path.join(ROOT, stale))
	for zip_name in current:
		addon_id = zip_name.rsplit('-', 1)[0]
		shutil.copyfile(os.path.join(DOCS, addon_id, zip_name),
						os.path.join(ROOT, zip_name))
	write_index(ROOT, current)
	# Jekyll would otherwise rebuild the root README into the served page.
	open(os.path.join(ROOT, '.nojekyll'), 'w').close()

	# GitHub Pages runs Jekyll by default, which skips files it considers
	# special; .nojekyll makes it serve the tree verbatim.
	open(os.path.join(DOCS, '.nojekyll'), 'w').close()

	print('  addons.xml md5: %s' % digest)
	print('Repository written to %s' % DOCS)
	return 0


if __name__ == '__main__':
	sys.exit(main())
