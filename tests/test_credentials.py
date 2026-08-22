# -*- coding: utf-8 -*-
"""The credential backup.

Hard requirement from the user: updating must never cost them the TorBox
API key. It had already been lost once - Kodi rewrites its settings file
whole, so anything that disturbs that file takes every credential at once
- and re-entering an API key on a Fire TV remote is not a recovery plan.
"""

import json
import os

import kodistubs

from support import AddonTestCase

from resources.lib import control
from resources.lib import credentials

KEY = 'd8d93f8a-0000-0000-0000-000000000000'


class Backup(AddonTestCase):
	def setUp(self):
		super(Backup, self).setUp()
		self.path = os.path.join(kodistubs.PROFILE, credentials.FILENAME)
		self.addCleanup(
			lambda: os.path.exists(self.path) and os.remove(self.path))

	def vault(self):
		with open(self.path, encoding='utf-8') as handle:
			return json.load(handle)

	def wipe_settings(self):
		"""What Kodi does to the stored values when its file is rewritten."""
		for key in credentials.CREDENTIALS:
			control.set_setting(key, '')
		for companions in credentials.COMPANIONS.values():
			for companion in companions:
				control.set_setting(companion, '')

	# -- backing up ----------------------------------------------------------

	def test_a_key_that_is_set_gets_backed_up(self):
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		self.assertEqual(self.vault()['torbox.api_key'], KEY)

	def test_nothing_is_written_when_there_is_nothing_to_back_up(self):
		# A setting with a declared default always reads as set. If those
		# counted, a fresh install would write a backup of its defaults.
		credentials.sync()
		self.assertFalse(os.path.exists(self.path))

	def test_a_companion_alone_is_not_worth_backing_up(self):
		self.set(**{'torbox.enabled': True})
		credentials.sync()
		self.assertFalse(os.path.exists(self.path))

	# -- the requirement -----------------------------------------------------

	def test_the_torbox_key_survives_the_settings_file_being_emptied(self):
		self.set(**{'torbox.api_key': KEY, 'torbox.enabled': True})
		credentials.sync()
		self.wipe_settings()
		self.assertEqual(control.setting('torbox.api_key'), '')

		credentials.sync()
		self.assertEqual(control.setting('torbox.api_key'), KEY)
		# A restored key with the service switched off is not restored.
		self.assertTrue(control.get_bool('torbox.enabled'))

	def test_every_credential_comes_back_not_just_torbox(self):
		self.set(**{'torbox.api_key': KEY, 'trakt.token': 'T',
					'trakt.refresh': 'R', 'rd.token': 'RD'})
		credentials.sync()
		self.wipe_settings()
		restored = credentials.sync()
		for key in ('torbox.api_key', 'trakt.token', 'trakt.refresh', 'rd.token'):
			self.assertIn(key, restored)

	def test_restoring_reports_what_it_put_back(self):
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		self.wipe_settings()
		# The companion toggle rides along but is not counted as a
		# credential, so the caller is told about the key and only the key.
		self.assertEqual(credentials.sync(), ['torbox.api_key'])

	# -- not an override -----------------------------------------------------

	def test_a_key_edited_in_settings_wins_over_the_backup(self):
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		self.set(**{'torbox.api_key': 'a-new-key'})
		credentials.sync()
		self.assertEqual(control.setting('torbox.api_key'), 'a-new-key')
		self.assertEqual(self.vault()['torbox.api_key'], 'a-new-key')

	def test_signing_out_is_not_undone_on_the_next_launch(self):
		# The whole feature is worthless if it fights the user.
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		from resources.lib import torbox
		torbox.revoke()
		credentials.sync()
		self.assertEqual(control.setting('torbox.api_key'), '')

	def test_signing_out_of_trakt_also_sticks(self):
		self.set(**{'trakt.token': 'T', 'trakt.refresh': 'R'})
		credentials.sync()
		from resources.lib import trakt
		trakt.revoke()
		credentials.sync()
		self.assertEqual(control.setting('trakt.token'), '')

	def test_signing_out_of_real_debrid_also_sticks(self):
		self.set(**{'rd.token': 'RD', 'rd.refresh': 'R'})
		credentials.sync()
		from resources.lib import realdebrid
		realdebrid.revoke()
		credentials.sync()
		self.assertEqual(control.setting('rd.token'), '')

	def test_forgetting_one_service_leaves_the_others_alone(self):
		self.set(**{'torbox.api_key': KEY, 'trakt.token': 'T'})
		credentials.sync()
		credentials.forget(('torbox.api_key',))
		self.assertNotIn('torbox.api_key', self.vault())
		self.assertEqual(self.vault()['trakt.token'], 'T')

	# -- failure modes -------------------------------------------------------

	def test_an_unreadable_backup_is_not_an_error(self):
		os.makedirs(kodistubs.PROFILE, exist_ok=True)
		with open(self.path, 'w', encoding='utf-8') as handle:
			handle.write('not json at all')
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()  # must not raise
		self.assertEqual(self.vault()['torbox.api_key'], KEY)

	def test_a_profile_that_cannot_be_written_does_not_raise(self):
		from unittest import mock
		self.set(**{'torbox.api_key': KEY})
		with mock.patch('builtins.open', side_effect=OSError('no space')):
			credentials.sync()  # must not raise

	def test_values_are_exposed_for_redaction(self):
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		self.assertIn(KEY, credentials.values())


class Redaction(AddonTestCase):
	def test_a_backed_up_key_is_scrubbed_from_a_report(self):
		# The settings copy is gone - that is why the log is being uploaded
		# - but the token in the log is still the real one.
		from resources.lib import diagnostics
		path = os.path.join(kodistubs.PROFILE, credentials.FILENAME)
		self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		self.set(**{'torbox.api_key': KEY})
		credentials.sync()
		control.set_setting('torbox.api_key', '')
		self.assertNotIn(KEY, diagnostics.scrub('leaked %s here' % KEY))
