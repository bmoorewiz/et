# -*- coding: utf-8 -*-
"""Settings access, list items, and credential redaction.

Regression home for the Real-Debrid token that reached the Kodi log. RD
takes its token as a query parameter, so any network error puts the whole
URL into a traceback, and Kodi logs get pasted into forum threads.
"""

import xbmcgui
import xbmcplugin

from support import AddonTestCase

from resources.lib import control


class Settings(AddonTestCase):
	def test_an_untouched_setting_reads_back_its_declared_default(self):
		# Kodi serves the settings.xml default until the user changes it.
		self.assertEqual(control.setting('updates.repo'), 'bmoorewiz/et')
		self.assertTrue(control.get_bool('scrobble.enabled'))
		self.assertEqual(control.get_int('scrobble.threshold.episode'), 80)

	def test_a_missing_setting_falls_back_to_the_supplied_default(self):
		self.assertEqual(control.setting('no.such.setting', 'fallback'), 'fallback')
		self.assertTrue(control.get_bool('no.such.setting', True))
		self.assertEqual(control.get_int('no.such.setting', 7), 7)

	def test_booleans_are_the_kodi_string_form(self):
		control.set_setting('scrobble.enabled', 'false')
		self.assertFalse(control.get_bool('scrobble.enabled', True))

	def test_a_non_numeric_int_setting_falls_back(self):
		control.set_setting('results.limit', 'lots')
		self.assertEqual(control.get_int('results.limit', 150), 150)

	def test_a_float_valued_int_setting_is_truncated(self):
		control.set_setting('results.limit', '12.9')
		self.assertEqual(control.get_int('results.limit'), 12)

	def test_setting_none_stores_an_empty_string(self):
		control.set_setting('trakt.token', None)
		self.assertEqual(control.setting('trakt.token'), '')


class Strings(AddonTestCase):
	def test_a_known_string_comes_back(self):
		self.assertTrue(control.lang(33001))

	def test_langf_formats(self):
		self.assertIn('TorBox', control.langf(33071, 'TorBox'))

	def test_langf_never_raises_on_a_mismatch(self):
		# A missing translation must never turn into a crash mid-playback.
		control.langf(33071)
		control.langf(33071, 'a', 'b', 'c')


class Urls(AddonTestCase):
	def test_build_and_parse_round_trip(self):
		url = control.build_url({'action': 'sources', 'entry': 'abc=='})
		self.assertEqual(control.parse_params(url.split('?', 1)[1]),
						 {'action': 'sources', 'entry': 'abc=='})

	def test_a_leading_question_mark_is_tolerated(self):
		self.assertEqual(control.parse_params('?a=b'), {'a': 'b'})

	def test_an_empty_query_is_empty(self):
		self.assertEqual(control.parse_params(''), {})


class DirectoryItems(AddonTestCase):
	def test_empty_info_values_never_reach_kodi(self):
		# Kodi rejects None in setInfo, and the stub enforces that too.
		control.add_directory_item('Label', {'action': 'x'},
								   info={'title': 'T', 'plot': None, 'aired': ''})
		tag = self.items()[0]['item'].getVideoInfoTag()
		self.assertEqual(tag.values.get('title'), 'T')
		self.assertNotIn('plot', tag.values)
		self.assertNotIn('aired', tag.values)

	def test_numeric_info_is_cast_for_the_typed_setters(self):
		# Kodi 20's InfoTagVideo rejects a string where it wants an int.
		control.add_directory_item('Label', {'action': 'x'},
								   info={'season': '2', 'episode': '3',
										 'duration': '2700'})
		tag = self.items()[0]['item'].getVideoInfoTag()
		self.assertEqual(tag.values['season'], 2)
		self.assertEqual(tag.values['episode'], 3)
		self.assertEqual(tag.values['duration'], 2700)

	def test_an_unsettable_info_key_is_skipped_not_fatal(self):
		control.add_directory_item('Label', {'action': 'x'},
								   info={'title': 'T', 'nonsense': 'value'})
		self.assertEqual(len(self.items()), 1)

	def test_art_always_has_an_icon_and_fanart(self):
		control.add_directory_item('Label', {'action': 'x'})
		art = self.items()[0]['item'].art
		self.assertIn('icon', art)
		self.assertIn('fanart', art)

	def test_playable_items_are_marked_for_kodi(self):
		control.add_directory_item('Label', {'action': 'x'}, is_playable=True)
		self.assertEqual(self.items()[0]['item'].getProperty('IsPlayable'), 'true')

	def test_end_directory_sets_the_content_type(self):
		control.end_directory(content='episodes')
		self.assertEqual(xbmcplugin.CONTENT, ['episodes'])

	def test_a_failed_resolve_is_reported_to_kodi(self):
		control.resolve_failed()
		self.assertFalse(xbmcplugin.RESOLVED[0]['succeeded'])


class Redaction(AddonTestCase):
	def test_a_token_in_a_url_is_masked(self):
		url = ('https://api.real-debrid.com/rest/1.0/torrents/info/T1'
			   '?auth_token=SECRETVALUE123')
		masked = control.redact(url)
		self.assertNotIn('SECRETVALUE123', masked)
		self.assertIn('auth_token=<redacted>', masked)

	def test_every_credential_shaped_parameter_is_masked(self):
		for key in ('auth_token', 'access_token', 'refresh_token', 'token',
					'client_secret', 'secret', 'password', 'api_key',
					'apikey', 'code'):
			text = 'https://host/x?%s=SECRETVALUE123&other=fine' % key
			masked = control.redact(text)
			self.assertNotIn('SECRETVALUE123', masked, key)
			self.assertIn('other=fine', masked, key)

	def test_bearer_headers_are_masked(self):
		self.assertNotIn('SECRETVALUE123',
						 control.redact('Authorization: Bearer SECRETVALUE123'))

	def test_redaction_is_case_insensitive(self):
		self.assertNotIn('SECRETVALUE123',
						 control.redact('?AUTH_TOKEN=SECRETVALUE123'))

	def test_ordinary_text_is_left_alone(self):
		self.assertEqual(control.redact('nothing secret here'),
						 'nothing secret here')

	def test_logging_goes_through_redaction(self):
		control.log('rd get failed: ?auth_token=SECRETVALUE123')
		self.assertNotIn('SECRETVALUE123', self.logged())

	def test_a_traceback_is_redacted_too(self):
		try:
			raise RuntimeError('failed for https://host?auth_token=SECRETVALUE123')
		except RuntimeError:
			control.error('rd get failed')
		self.assertNotIn('SECRETVALUE123', self.logged())

	def test_redaction_never_raises(self):
		self.assertTrue(control.redact(object()))
