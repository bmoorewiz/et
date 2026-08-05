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


def build_index(items):
	"""Plain-anchor index page - Kodi's HTTP browser parses <a href>."""
	rows = []
	for addon_id, version, size in items:
		href = '%s/%s-%s.zip' % (addon_id, addon_id, version)
		rows.append(
			'    <li><a href="{h}">{h}</a> <span class="s">{kb} KB</span></li>'
			.format(h=html.escape(href), kb=size // 1024))
	links = '\n'.join(rows)
	return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Episode Tracker Kodi Repository</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 44rem; margin: 3rem auto; padding: 0 1.2rem;
         line-height: 1.6; color: #1b1b1b; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; margin-bottom: .2rem; }}
  p.sub {{ color: #555; margin-top: 0; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin: .35rem 0; }}
  code {{ background: #ececec; padding: .1rem .35rem; border-radius: 3px; }}
  .s {{ color: #777; font-size: .85em; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16181c; color: #e6e6e6; }}
    p.sub, .s {{ color: #9aa0a6; }}
    code {{ background: #2a2d33; }}
    a {{ color: #7ab8ff; }}
  }}
</style>
</head>
<body>
<h1>Episode Tracker &mdash; Kodi Repository</h1>
<p class="sub">Add this page as a source in Kodi, then install from zip.</p>

<h2>Add as a Kodi source</h2>
<ol>
  <li><b>Settings &rarr; File manager &rarr; Add source</b></li>
  <li>Enter <code>{PAGES_URL}</code> and name it <code>episodetracker</code></li>
  <li><b>Add-ons &rarr; Install from zip file &rarr; episodetracker &rarr;
      repository.episodetracker</b> and pick the zip</li>
  <li>Then <b>Install from repository &rarr; Episode Tracker Repository &rarr;
      Video add-ons &rarr; Episode Tracker</b></li>
</ol>
<p>Enable <b>Settings &rarr; System &rarr; Add-ons &rarr; Unknown sources</b> first.</p>

<h2>Files</h2>
<ul>
{LINKS}
</ul>
</body>
</html>
""".replace('{PAGES_URL}', 'https://bmoorewiz.github.io/et/').replace('{LINKS}', links)


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

	with open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8') as handle:
		handle.write(build_index(items))

	# GitHub Pages runs Jekyll by default, which skips files it considers
	# special; .nojekyll makes it serve the tree verbatim.
	open(os.path.join(DOCS, '.nojekyll'), 'w').close()

	print('  addons.xml md5: %s' % digest)
	print('Repository written to %s' % DOCS)
	return 0


if __name__ == '__main__':
	sys.exit(main())
