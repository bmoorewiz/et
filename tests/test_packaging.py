# -*- coding: utf-8 -*-
"""The shipped artefacts have to agree with each other.

Regression home for the build-directory mistake, which happened twice:
running the build from the wrong working directory produced an addon.xml
saying one version and a zip named another, so Kodi's repository offered an
update that installed the old code. Nothing in the add-on notices - only a
check like this does.
"""

import os
import re
import glob
import hashlib
import zipfile

import kodistubs

from support import AddonTestCase

ROOT = os.path.dirname(kodistubs.ADDON_ROOT)
ADDON_ID = 'plugin.video.episodetracker'
REPO_ID = 'repository.episodetracker'


def read(path):
	with open(path, encoding='utf-8') as handle:
		return handle.read()


def version_of(xml, addon_id):
	match = re.search(r'<addon[^>]*\bid="%s"[^>]*\bversion="([^"]+)"'
					  % re.escape(addon_id), xml)
	return match.group(1) if match else None


class VersionConsistency(AddonTestCase):
	def setUp(self):
		super(VersionConsistency, self).setUp()
		self.version = version_of(read(os.path.join(kodistubs.ADDON_ROOT,
													'addon.xml')), ADDON_ID)

	def test_the_addon_declares_a_version(self):
		self.assertRegex(self.version or '', r'^\d+\.\d+\.\d+$')

	def test_the_repository_catalogue_offers_that_version(self):
		catalogue = read(os.path.join(ROOT, 'docs/addons.xml'))
		self.assertEqual(version_of(catalogue, ADDON_ID), self.version)

	def test_the_catalogue_checksum_matches_the_catalogue(self):
		# Kodi refuses to refresh a repository whose md5 does not match, and
		# does so silently.
		catalogue = os.path.join(ROOT, 'docs/addons.xml')
		expected = hashlib.md5(open(catalogue, 'rb').read()).hexdigest()
		stored = read(catalogue + '.md5').split()[0].strip()
		self.assertEqual(stored, expected)

	def test_a_zip_exists_for_this_version_everywhere_it_is_served(self):
		for path in (
				# what the built-in updater downloads
				'%s-%s.zip' % (ADDON_ID, self.version),
				# what the Kodi repository serves
				'docs/%s/%s-%s.zip' % (ADDON_ID, ADDON_ID, self.version),
				'docs/%s-%s.zip' % (ADDON_ID, self.version)):
			self.assertTrue(os.path.exists(os.path.join(ROOT, path)),
							'missing %s' % path)

	def test_the_zip_actually_contains_this_version(self):
		# The failure mode was a correctly *named* zip holding the old
		# addon.xml, which no filename check would catch.
		for path in glob.glob(os.path.join(ROOT, '**', '%s-*.zip' % ADDON_ID),
							  recursive=True):
			with zipfile.ZipFile(path) as archive:
				inner = archive.read('%s/addon.xml' % ADDON_ID).decode('utf-8')
			named = re.search(r'-(\d+\.\d+\.\d+)\.zip$', path).group(1)
			self.assertEqual(version_of(inner, ADDON_ID), named,
							 '%s holds a different version' % os.path.basename(path))

	def test_no_older_zip_is_left_behind_to_be_served(self):
		stale = [os.path.basename(p)
				 for p in glob.glob(os.path.join(ROOT, '%s-*.zip' % ADDON_ID))
				 if not p.endswith('-%s.zip' % self.version)]
		self.assertEqual(stale, [], 'stale zips at the repository root')

	def test_the_changelog_leads_with_this_version(self):
		changelog = read(os.path.join(kodistubs.ADDON_ROOT, 'changelog.txt'))
		self.assertTrue(changelog.startswith('v%s\n' % self.version),
						'changelog does not start with v%s' % self.version)

	def test_the_news_entry_matches_too(self):
		# Kodi shows <news> on the add-on's information page.
		addon_xml = read(os.path.join(kodistubs.ADDON_ROOT, 'addon.xml'))
		news = re.search(r'<news>(.*?)</news>', addon_xml, re.S).group(1)
		self.assertTrue(news.strip().startswith('v%s' % self.version), news[:60])


class ZipContents(AddonTestCase):
	def setUp(self):
		super(ZipContents, self).setUp()
		version = version_of(read(os.path.join(kodistubs.ADDON_ROOT, 'addon.xml')),
							 ADDON_ID)
		self.archive = zipfile.ZipFile(
			os.path.join(ROOT, '%s-%s.zip' % (ADDON_ID, version)))
		self.names = self.archive.namelist()

	def test_everything_lives_under_the_addon_id(self):
		# Kodi installs by directory name; a zip rooted anywhere else
		# installs to the wrong folder and never updates.
		for name in self.names:
			self.assertTrue(name.startswith(ADDON_ID + '/'), name)

	def test_the_entry_points_are_present(self):
		for name in ('addon.xml', 'addon.py', 'service.py',
					 'resources/settings.xml',
					 'resources/language/resource.language.en_gb/strings.po'):
			self.assertIn('%s/%s' % (ADDON_ID, name), self.names, name)

	def test_every_library_module_is_shipped(self):
		on_disk = {os.path.basename(p) for p in
				   glob.glob(os.path.join(kodistubs.ADDON_ROOT,
										  'resources/lib/*.py'))}
		shipped = {os.path.basename(n) for n in self.names
				   if n.startswith('%s/resources/lib/' % ADDON_ID)
				   and n.endswith('.py')}
		self.assertEqual(on_disk - shipped, set())

	def test_no_compiled_bytecode_is_shipped(self):
		# Stale .pyc files from a different Python shadow the real source.
		self.assertEqual([n for n in self.names if '.pyc' in n or '__pycache__' in n],
						 [])

	def test_the_icon_and_fanart_are_shipped(self):
		self.assertIn('%s/resources/icon.png' % ADDON_ID, self.names)
		self.assertIn('%s/resources/fanart.png' % ADDON_ID, self.names)


class RepositoryAddon(AddonTestCase):
	def setUp(self):
		super(RepositoryAddon, self).setUp()
		self.xml = read(os.path.join(ROOT, REPO_ID, 'addon.xml'))

	def test_it_points_at_the_catalogue_and_the_zips(self):
		info = re.search(r'<info[^>]*>([^<]+)</info>', self.xml).group(1)
		checksum = re.search(r'<checksum>([^<]+)</checksum>', self.xml).group(1)
		datadir = re.search(r'<datadir[^>]*>([^<]+)</datadir>', self.xml).group(1)
		self.assertTrue(info.endswith('/addons.xml'), info)
		self.assertEqual(checksum, info + '.md5')
		self.assertTrue(datadir.endswith('/'), datadir)
		self.assertTrue(info.startswith(datadir), 'catalogue is outside the datadir')

	def test_the_datadir_layout_matches_what_is_published(self):
		# Kodi fetches <datadir>/<addon id>/<addon id>-<version>.zip
		version = version_of(read(os.path.join(kodistubs.ADDON_ROOT, 'addon.xml')),
							 ADDON_ID)
		self.assertTrue(os.path.exists(os.path.join(
			ROOT, 'docs', ADDON_ID, '%s-%s.zip' % (ADDON_ID, version))))

	def test_the_declared_assets_are_published_beside_the_zip(self):
		# Kodi's add-on browser fetches <datadir>/<id>/<asset path>, not the
		# copy inside the zip. Without these the listing shows a blank tile
		# and the Kodi log fills with 404s.
		for addon_id in (ADDON_ID, REPO_ID):
			manifest = read(os.path.join(ROOT, addon_id, 'addon.xml'))
			assets = re.findall(r'<(?:icon|fanart)>([^<]+)</(?:icon|fanart)>',
								manifest)
			self.assertTrue(assets, addon_id)
			for relative in assets:
				self.assertTrue(
					os.path.exists(os.path.join(ROOT, 'docs', addon_id, relative)),
					'docs/%s/%s is missing' % (addon_id, relative))

	def test_the_repository_is_listed_in_its_own_catalogue(self):
		catalogue = read(os.path.join(ROOT, 'docs/addons.xml'))
		self.assertIsNotNone(version_of(catalogue, REPO_ID))

	def test_the_repository_zip_matches_its_addon_xml(self):
		version = version_of(self.xml, REPO_ID)
		path = os.path.join(ROOT, '%s-%s.zip' % (REPO_ID, version))
		self.assertTrue(os.path.exists(path), path)
		with zipfile.ZipFile(path) as archive:
			inner = archive.read('%s/addon.xml' % REPO_ID).decode('utf-8')
		self.assertEqual(version_of(inner, REPO_ID), version)


class AddonManifest(AddonTestCase):
	def setUp(self):
		super(AddonManifest, self).setUp()
		self.xml = read(os.path.join(kodistubs.ADDON_ROOT, 'addon.xml'))

	def test_the_plugin_and_the_service_are_both_declared(self):
		# Tracking has to live in a service: Kodi tears the plugin process
		# down right after it resolves a URL.
		self.assertIn('point="xbmc.python.pluginsource"', self.xml)
		self.assertIn('point="xbmc.service"', self.xml)
		self.assertIn('library="service.py"', self.xml)
		self.assertIn('start="startup"', self.xml)

	def test_the_declared_entry_points_exist(self):
		for name in re.findall(r'library="([^"]+)"', self.xml):
			self.assertTrue(os.path.exists(os.path.join(kodistubs.ADDON_ROOT, name)),
							name)

	def test_the_declared_assets_exist(self):
		for tag in ('icon', 'fanart'):
			path = re.search(r'<%s>([^<]+)</%s>' % (tag, tag), self.xml).group(1)
			self.assertTrue(os.path.exists(os.path.join(kodistubs.ADDON_ROOT, path)),
							path)

	def test_cocoscrapers_is_an_optional_import(self):
		# It is not on any official repository, so a hard requirement would
		# make the add-on itself uninstallable.
		self.assertRegex(self.xml,
						 r'<import addon="script\.module\.cocoscrapers"[^>]*'
						 r'optional="true"')
