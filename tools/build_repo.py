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


INDEX_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(title)s</title>
</head>
<body>
<h1>%(title)s</h1>
%(intro)s<pre>
"""

INDEX_TAIL = """</pre>
</body>
</html>
"""


def write_index(directory, entries, title, intro=''):
	"""Write an Apache-autoindex-style listing that Kodi can parse.

	Kodi's HTTP directory reader scans for <a href="..."> and treats each
	href as one entry *in the current directory*, so every href must be a
	single path segment - directories with a trailing slash, files without.
	Anchors are emitted one per line with no markup nested inside them,
	which is the shape Kodi's regex expects. GitHub Pages serves no
	directory listing of its own, so every directory needs one of these.
	"""
	rows = []
	for href, label in entries:
		assert href.count('/') <= 1 and not href.startswith('/'), \
			'href must be a single path segment: %r' % href
		rows.append('<a href="%s">%s</a>' % (html.escape(href), html.escape(label)))
	body = (INDEX_HEAD % {'title': html.escape(title), 'intro': intro}
			+ '\n'.join(rows) + '\n' + INDEX_TAIL)
	with open(os.path.join(directory, 'index.html'), 'w', encoding='utf-8') as handle:
		handle.write(body)


ROOT_INTRO = """<p>Kodi source URL: <code>https://bmoorewiz.github.io/et/</code></p>
<p>Add it under Settings &rarr; File manager &rarr; Add source, then use
Add-ons &rarr; Install from zip file. Enable Settings &rarr; System &rarr;
Add-ons &rarr; Unknown sources first.</p>
"""


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
		zip_name = '%s-%s.zip' % (addon_id, version)
		write_index(os.path.join(DOCS, addon_id), [(zip_name, zip_name)],
					title=addon_id)

	# A flat copy of the repository zip at the root, so it can be installed
	# straight from the top-level listing without descending a folder.
	repo_id = 'repository.episodetracker'
	repo_version = dict((i[0], i[1]) for i in items)[repo_id]
	repo_zip = '%s-%s.zip' % (repo_id, repo_version)
	for stale in os.listdir(DOCS):
		if stale.startswith(repo_id) and stale.endswith('.zip') and stale != repo_zip:
			os.remove(os.path.join(DOCS, stale))
	shutil.copyfile(os.path.join(DOCS, repo_id, repo_zip),
					os.path.join(DOCS, repo_zip))

	root_entries = [(repo_zip, repo_zip)]
	root_entries += [('%s/' % addon_id, '%s/' % addon_id) for addon_id, _, _ in items]
	write_index(DOCS, root_entries, title='Episode Tracker Kodi Repository',
				intro=ROOT_INTRO)

	# GitHub Pages runs Jekyll by default, which skips files it considers
	# special; .nojekyll makes it serve the tree verbatim.
	open(os.path.join(DOCS, '.nojekyll'), 'w').close()

	print('  addons.xml md5: %s' % digest)
	print('Repository written to %s' % DOCS)
	return 0


if __name__ == '__main__':
	sys.exit(main())
