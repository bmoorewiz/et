# -*- coding: utf-8 -*-
"""Strings and settings referenced by the code must actually exist.

A missing string id shows as a blank dialog and a missing setting id reads
back as '' - both silent, and both only visible on a real install. These
checks are static, so they catch the whole surface rather than the paths a
test happens to exercise.
"""

import os
import re
import glob

import xbmcaddon
import kodistubs

from support import AddonTestCase

LIB = os.path.join(kodistubs.ADDON_ROOT, 'resources/lib')
SETTINGS_XML = os.path.join(kodistubs.ADDON_ROOT, 'resources/settings.xml')


def sources():
	paths = sorted(glob.glob(os.path.join(LIB, '*.py')))
	paths += [os.path.join(kodistubs.ADDON_ROOT, name)
			  for name in ('addon.py', 'service.py')]
	return {path: open(path, encoding='utf-8').read() for path in paths}


def all_code():
	return '\n'.join(sources().values())


def call_arguments(text, function):
	"""Top-level arguments of every ``function(...)`` call in ``text``.

	Written out rather than done with a regex because the arguments are
	full expressions - ``control.langf(33043, position, len(queue),
	candidate.get('quality', ''))`` has four commas and three arguments, and
	counting commas gets that wrong.
	"""
	calls = []
	for match in re.finditer(r'\b%s\(' % re.escape(function), text):
		depth, index, quote = 1, match.end(), None
		while index < len(text) and depth:
			char = text[index]
			if quote:
				if char == quote and text[index - 1] != '\\':
					quote = None
			elif char in '\'"':
				quote = char
			elif char in '([{':
				depth += 1
			elif char in ')]}':
				depth -= 1
			index += 1
		calls.append(_split(text[match.end():index - 1]))
	return calls


def _split(text):
	depth, quote, parts = 0, None, ['']
	for char in text:
		if quote:
			parts[-1] += char
			if char == quote:
				quote = None
			continue
		if char in '\'"':
			quote = char
		elif char in '([{':
			depth += 1
		elif char in ')]}':
			depth -= 1
		if char == ',' and depth == 0:
			parts.append('')
			continue
		parts[-1] += char
	return [part.strip() for part in parts if part.strip()]


class StringCatalogue(AddonTestCase):
	def setUp(self):
		super(StringCatalogue, self).setUp()
		self.addon = xbmcaddon.Addon()

	def test_every_string_id_used_in_code_exists(self):
		code = all_code()
		used = set()
		for function in ('lang', 'langf', 'notify', 'ok_dialog', 'yesno_dialog'):
			for args in call_arguments(code, function):
				if args and re.fullmatch(r'\d{5}', args[0]):
					used.add(int(args[0]))
		self.assertTrue(used, 'the scan itself has stopped working')
		missing = sorted(i for i in used if not self.addon.getLocalizedString(i))
		self.assertEqual(missing, [], 'missing string ids: %s' % missing)

	def test_every_string_id_used_in_settings_xml_exists(self):
		schema = open(SETTINGS_XML, encoding='utf-8').read()
		used = {int(n) for n in re.findall(r'label="(3\d{4})"', schema)}
		self.assertTrue(used)
		missing = sorted(i for i in used if not self.addon.getLocalizedString(i))
		self.assertEqual(missing, [], 'missing string ids: %s' % missing)

	def test_format_placeholders_match_the_arguments_passed(self):
		# A string with a %s and a call with no argument produces a literal
		# "%s" on screen; the other way round raises mid-playback.
		problems = []
		for args in call_arguments(all_code(), 'langf'):
			if not args or not re.fullmatch(r'\d{5}', args[0]):
				continue
			text = self.addon.getLocalizedString(int(args[0]))
			slots = len(re.findall(r'%[sdif]', text))
			if slots != len(args) - 1:
				problems.append((args[0], slots, len(args) - 1))
		self.assertEqual(problems, [],
						 'id, placeholders, arguments: %s' % problems)

	def test_plain_lang_is_never_used_for_a_format_string(self):
		# lang() does no substitution, so the user would see the raw %s.
		problems = []
		for function in ('lang', 'notify', 'ok_dialog', 'yesno_dialog'):
			for args in call_arguments(all_code(), function):
				if len(args) == 1 and re.fullmatch(r'\d{5}', args[0]):
					text = self.addon.getLocalizedString(int(args[0]))
					if re.search(r'%[sdif]', text):
						problems.append(args[0])
		self.assertEqual(problems, [], 'format strings used unformatted: %s'
						 % problems)

	def test_the_catalogue_has_no_duplicate_ids(self):
		text = open(os.path.join(
			kodistubs.ADDON_ROOT,
			'resources/language/resource.language.en_gb/strings.po'),
			encoding='utf-8').read()
		ids = re.findall(r'msgctxt "#(\d+)"', text)
		duplicates = sorted({i for i in ids if ids.count(i) > 1})
		self.assertEqual(duplicates, [])

	def test_every_entry_has_text(self):
		text = open(os.path.join(
			kodistubs.ADDON_ROOT,
			'resources/language/resource.language.en_gb/strings.po'),
			encoding='utf-8').read()
		empty = re.findall(r'msgctxt "#(\d+)"\s*\nmsgid ""\s*\nmsgstr ""', text)
		self.assertEqual(empty, [])


class SettingsSchema(AddonTestCase):
	def setUp(self):
		super(SettingsSchema, self).setUp()
		schema = open(SETTINGS_XML, encoding='utf-8').read()
		self.declared = set(re.findall(r'<setting\s+id="([^"]+)"', schema))
		self.schema = schema

	def _used_at_a_call_site(self):
		"""Setting ids passed literally to a settings accessor."""
		used = set()
		code = all_code()
		for function in ('control.setting', 'control.set_setting',
						 'control.get_bool', 'control.get_int',
						 'setting', 'set_setting', 'get_bool', 'get_int'):
			for args in call_arguments(code, function):
				if args and re.fullmatch(r"'[a-z0-9_.]+'", args[0]):
					used.add(args[0].strip("'"))
		return used

	def _mentioned(self):
		"""Setting ids appearing anywhere as a string literal.

		Some ids never reach an accessor directly: the per-tier playback
		budgets sit in a table that is walked with the key in a variable,
		and the watched threshold picks its key with a conditional. Those
		are used, just not at a literal call site.
		"""
		code = all_code()
		return {key for key in self.declared
				if re.search(r"'%s'" % re.escape(key), code)}

	def test_every_setting_the_code_touches_is_declared(self):
		used = self._used_at_a_call_site()
		self.assertTrue(used, 'the scan itself has stopped working')
		missing = sorted(used - self.declared - {'no.such.setting'})
		self.assertEqual(missing, [],
						 'these settings are read or written but never '
						 'declared, so they silently do nothing: %s' % missing)

	def test_every_declared_setting_is_used(self):
		# A setting nobody reads is a control that does nothing.
		unused = sorted(self.declared - self._mentioned())
		self.assertEqual(unused, [], 'declared but never read: %s' % unused)

	def test_every_setting_declares_a_label(self):
		# Kodi does not reject a setting with no label - it invents one, a
		# single space - so the setting still exists and the only sign is a
		# nameless row in the dialog. Nothing else in this file was missing
		# one, which is the point: the odd one out is the mistake.
		nameless = [element for element
					in re.findall(r'<setting\s+id="[^"]*"[^>]*>', self.schema)
					if 'label=' not in element]
		self.assertEqual(nameless, [],
						 'settings with an id but no label: %s' % nameless)

	def test_no_setting_id_is_declared_twice(self):
		ids = re.findall(r'<setting\s+id="([^"]+)"', self.schema)
		duplicates = sorted({i for i in ids if ids.count(i) > 1})
		self.assertEqual(duplicates, [])

	def test_credentials_are_hidden_in_the_ui(self):
		for element in re.findall(r'<setting\s+id="[^"]*(?:secret|api_key|token)"'
								  r'[^>]*>', self.schema):
			self.assertTrue('option="hidden"' in element
							or 'visible="false"' in element, element)

	def test_the_updater_targets_a_real_branch(self):
		# If this branch is ever deleted, every install stops updating
		# silently - so at least keep the default honest.
		self.assertIn('default="bmoorewiz/et"', self.schema)
		self.assertIn('id="updates.branch"', self.schema)


class Compilation(AddonTestCase):
	def test_every_module_compiles(self):
		for path, text in sources().items():
			try:
				compile(text, path, 'exec')
			except SyntaxError as error:
				self.fail('%s: %s' % (path, error))
