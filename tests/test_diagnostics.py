# -*- coding: utf-8 -*-
"""The log upload: what goes in it, and what must never go in it.

Regression home for the TorBox API key, which was one release away from
being uploaded to a public paste because the settings list that gets
scrubbed had not been updated alongside the new provider.
"""

import os
import re
from unittest import mock

import kodistubs

from support import AddonTestCase

from resources.lib import diagnostics

# Long enough to be scrubbed by the exact-match pass, which ignores short
# values because they would match far too much ordinary text.
SECRET = 'SUPERSECRETVALUE1234567890'


class SecretCoverage(AddonTestCase):
	def test_every_secret_setting_in_the_schema_is_scrubbed(self):
		"""Any credential-shaped setting must be on the scrub list.

		This is the test that would have caught the TorBox key: adding a
		provider adds a setting, and forgetting to add it here is what puts
		it in a public paste.
		"""
		schema = open(os.path.join(kodistubs.ADDON_ROOT, 'resources/settings.xml'),
					  encoding='utf-8').read()
		declared = set(re.findall(r'<setting\s+id="([^"]+)"', schema))
		credential_like = {
			key for key in declared
			if re.search(r'token|secret|api_key|apikey|password|refresh', key)
		}
		self.assertIn('torbox.api_key', credential_like,
					  'the scan itself has stopped working')
		missing = credential_like - set(diagnostics._SECRET_SETTINGS)
		self.assertEqual(missing, set(),
						 'these settings would be uploaded in the clear: %s'
						 % sorted(missing))

	def test_no_secret_setting_is_also_on_the_reported_list(self):
		overlap = set(diagnostics._SECRET_SETTINGS) & set(diagnostics._REPORTED_SETTINGS)
		self.assertEqual(overlap, set())

	def test_a_removed_setting_is_still_scrubbed(self):
		# An install that saved the old GitHub token still has it on disk.
		self.assertIn('logs.github_token', diagnostics._SECRET_SETTINGS)


class Scrubbing(AddonTestCase):
	def test_a_stored_token_is_removed_wherever_it_appears(self):
		self.set(**{'rd.token': SECRET})
		text = 'some log line mentioning %s in passing' % SECRET
		self.assertNotIn(SECRET, diagnostics.scrub(text))

	def test_the_torbox_key_is_removed(self):
		self.set(**{'torbox.api_key': SECRET})
		self.assertNotIn(SECRET, diagnostics.scrub('key: %s' % SECRET))

	def test_regex_redaction_still_applies(self):
		self.assertNotIn('QUERYSECRET',
						 diagnostics.scrub('?auth_token=QUERYSECRET'))

	def test_short_values_are_left_alone(self):
		# Scrubbing a 3-character setting would gut the whole report.
		self.set(**{'trakt.user': 'abc'})
		self.assertIn('abc', diagnostics.scrub('abc appears everywhere'))

	def test_an_unset_secret_does_not_blank_the_report(self):
		self.assertEqual(diagnostics.scrub('ordinary text'), 'ordinary text')


class Collect(AddonTestCase):
	def _collect(self):
		with mock.patch.object(diagnostics, '_read_log_tail', return_value=None):
			return diagnostics.collect()

	def test_the_report_identifies_the_install(self):
		report = self._collect()
		self.assertIn('Episode Tracker diagnostics', report)
		self.assertIn('addon version', report)

	def test_no_credential_reaches_the_report(self):
		self.set(**{'rd.token': SECRET, 'trakt.token': SECRET + 'A',
					'torbox.api_key': SECRET + 'B',
					'trakt.refresh': SECRET + 'C', 'updates.token': SECRET + 'D'})
		report = self._collect()
		for value in (SECRET, SECRET + 'A', SECRET + 'B', SECRET + 'C',
					  SECRET + 'D'):
			self.assertNotIn(value, report)

	def test_settings_worth_seeing_are_included(self):
		report = self._collect()
		self.assertIn('scrobble.threshold.episode', report)

	def test_whether_accounts_are_linked_is_reported_without_the_tokens(self):
		self.set(**{'trakt.token': SECRET, 'trakt.user': 'tester'})
		report = self._collect()
		self.assertIn('tester', report)
		self.assertNotIn(SECRET, report)

	def test_a_real_log_file_is_read_and_scrubbed(self):
		path = os.path.join(kodistubs.PROFILE, 'kodi.log')
		with open(path, 'w', encoding='utf-8') as handle:
			handle.write('line one\n?auth_token=%s\nline three\n' % SECRET)
		tail = diagnostics._read_log_tail(path)
		self.assertIn('line three', tail)
		self.assertNotIn(SECRET, diagnostics.scrub(tail))

	def test_a_missing_log_file_is_not_an_error(self):
		self.assertIsNone(diagnostics._read_log_tail(
			os.path.join(kodistubs.PROFILE, 'does-not-exist.log')))

	def test_only_the_tail_of_a_long_log_is_taken(self):
		path = os.path.join(kodistubs.PROFILE, 'big.log')
		with open(path, 'w', encoding='utf-8') as handle:
			handle.write('x' * 10 + '\n')
			handle.write(('filler line\n' * 20000))
			handle.write('THE INTERESTING PART\n')
		tail = diagnostics._read_log_tail(path, limit=4096)
		self.assertIn('THE INTERESTING PART', tail)
		self.assertLessEqual(len(tail.encode('utf-8')), 4096)


class Upload(AddonTestCase):
	def test_a_paste_key_becomes_a_viewable_link(self):
		response = mock.Mock(status_code=200)
		response.json.return_value = {'key': 'abcdef'}
		with mock.patch.object(diagnostics.requests, 'post', return_value=response), \
				mock.patch.object(diagnostics, 'collect', return_value='report'):
			url, error = diagnostics.upload()
		self.assertIsNone(error)
		self.assertEqual(url, diagnostics.PASTE_VIEW % 'abcdef')

	def test_the_report_is_what_gets_posted(self):
		response = mock.Mock(status_code=200)
		response.json.return_value = {'key': 'abcdef'}
		with mock.patch.object(diagnostics.requests, 'post',
							   return_value=response) as post:
			diagnostics.upload('the report body')
		self.assertIn('the report body', str(post.call_args))

	def test_a_rejected_upload_reports_why(self):
		response = mock.Mock(status_code=503, text='service unavailable')
		with mock.patch.object(diagnostics.requests, 'post', return_value=response):
			url, error = diagnostics.upload('report')
		self.assertIsNone(url)
		self.assertTrue(error)

	def test_being_offline_is_reported_not_raised(self):
		with mock.patch.object(diagnostics.requests, 'post',
							   side_effect=RuntimeError('offline')):
			url, error = diagnostics.upload('report')
		self.assertIsNone(url)
		self.assertTrue(error)


class SchemaHealth(AddonTestCase):
	"""Telling a schema that did not load apart from being signed out.

	Both look identical through the settings API - every value reads back
	empty - and in v1.11.0 that ambiguity cost a whole round trip: the
	add-on reported "Trakt is not authorized" and its settings dialog came
	up blank, and a report built only from the API could not say which of
	the two had happened.
	"""

	def _store(self, **values):
		"""Write values into the profile settings file only.

		Deliberately not through control.set_setting: the point is a value
		that exists on disk but does not reach the add-on.
		"""
		os.makedirs(kodistubs.PROFILE, exist_ok=True)
		body = ''.join('\t<setting id="%s">%s</setting>\n' % item
					   for item in values.items())
		path = os.path.join(kodistubs.PROFILE, 'settings.xml')
		with open(path, 'w', encoding='utf-8') as handle:
			handle.write('<settings version="2">\n%s</settings>\n' % body)
		self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		return path

	def test_a_value_on_disk_the_addon_cannot_read_is_called_out(self):
		self._store(**{'trakt.token': 'abc123'})
		report = '\n'.join(diagnostics._schema_health())
		self.assertIn('ARE NOT READABLE', report)
		self.assertIn('trakt.token', report)

	def test_a_value_the_addon_can_read_is_not_called_out(self):
		self.set(**{'trakt.token': 'abc123'})
		self._store(**{'trakt.token': 'abc123'})
		report = '\n'.join(diagnostics._schema_health())
		self.assertIn('all readable', report)
		self.assertNotIn('NOT READABLE', report)

	def test_a_setting_the_schema_no_longer_declares_is_not_a_fault(self):
		# A value left behind by a removed setting reads back empty because
		# it no longer exists, which is correct rather than broken.
		self._store(**{'logs.github_token': 'left-over'})
		report = '\n'.join(diagnostics._schema_health())
		self.assertIn('all readable', report)

	def test_an_unparseable_schema_is_reported_as_the_cause(self):
		with mock.patch.object(diagnostics, '_declared_settings',
							   return_value=None):
			report = '\n'.join(diagnostics._schema_health())
		self.assertIn('WILL NOT PARSE', report)

	def test_no_stored_file_yet_is_not_reported_as_a_fault(self):
		path = os.path.join(kodistubs.PROFILE, 'settings.xml')
		if os.path.exists(path):
			os.remove(path)
		report = '\n'.join(diagnostics._schema_health())
		self.assertIn('nothing stored yet', report)

	def test_the_check_reaches_the_report(self):
		self._store(**{'trakt.token': 'abc123'})
		with mock.patch.object(diagnostics, '_read_log_tail', return_value=None):
			report = diagnostics.collect()
		self.assertIn('ARE NOT READABLE', report)
		# The ids are named; the values behind them still must not be.
		self.assertNotIn('abc123', report)
