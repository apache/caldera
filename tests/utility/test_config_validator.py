"""Main configuration schema and confidential source-location diagnostics."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utility import config_validator as validator


VALID = 'host: 0.0.0.0\nport: 8888\nplugins: [stockpile]\nusers: {red: {red: password}}\n'


class TestConfigValidator(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temporary) / 'config.yml'

    def check(self, contents):
        self.path.write_text(contents, encoding='utf-8')
        before = self.path.read_bytes()
        problems = validator.validate(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        return problems

    def test_valid_and_extensions(self):
        self.assertEqual(self.check(VALID), [])
        self.assertEqual(self.check(VALID + 'custom.plugin.option: {anything: true}\n'), [])

    def test_shipped_default(self):
        path = Path(__file__).resolve().parents[2] / 'conf/default.yml'
        self.assertEqual(validator.validate(path), [])

    def test_port_boundaries_and_types(self):
        for value in ('0', '65536', 'true', 'null', '8.5', '"8888"'):
            with self.subTest(value=value):
                problems = self.check(VALID.replace('port: 8888', f'port: {value}'))
                self.assertEqual(len(problems), 1)
                self.assertEqual((problems[0].field, problems[0].line), ('port', 2))
        for value in ('1', '65535'):
            self.assertEqual(self.check(VALID.replace('port: 8888', f'port: {value}')), [])

    def test_positive_integer_settings(self):
        for field in ('ability_refresh', 'session_expiration_days', 'client_max_size_mb', 'api_upload_max_size_mb'):
            for value in ('0', '-1', 'false', '1.2'):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.check(VALID + f'{field}: {value}\n')[0].field, field)

    def test_plugin_validation(self):
        for value in ('null', '[../outside]', '[true]', '[stockpile, stockpile]', 'stockpile'):
            with self.subTest(value=value):
                self.assertEqual(self.check(VALID.replace('[stockpile]', value))[0].field, 'plugins')

    def test_users_and_strings(self):
        for value in ('null', '{}', '{red: []}', '{red: {red: 123}}', '{red: {red: ""}}'):
            with self.subTest(value=value):
                self.assertEqual(self.check(VALID.replace('{red: {red: password}}', value))[0].field, 'users')
        self.assertEqual(self.check(VALID + 'api_key_red: false\n')[0].field, 'api_key_red')

    def test_missing_fields_and_non_mappings(self):
        self.assertEqual(len(self.check('{}')), 4)
        for value in ('', 'null', '[one, two]', 'plain text'):
            with self.subTest(value=value):
                self.assertTrue(self.check(value))

    def test_duplicate_keys_nested_and_locations(self):
        problems = self.check(VALID + 'port: 9999\n')
        self.assertEqual(problems[0].line, 5)
        self.assertIn('Duplicate', problems[0].message)
        self.assertTrue(self.check(VALID + 'custom: {a: 1, a: 2}\n'))
        self.assertTrue(self.check(VALID + 'custom: {true: 1, True: 2}\n'))

    def test_aliases_and_merges(self):
        self.assertEqual(self.check('base: &base {port: 9000}\n<<: *base\n' + VALID), [])
        self.assertEqual(self.check(VALID + 'custom: &loop {next: *loop}\n'), [])

    def test_yaml_errors_and_secret_suppression(self):
        for contents in (VALID + 'secret: [SECRET_MUST_NOT_APPEAR\n',
                         VALID + 'secret: !!python/object:SECRET_MUST_NOT_APPEAR {}\n'):
            with self.subTest(contents=contents):
                self.path.write_text(contents)
                for flags in ([], ['--json']):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = validator.main([str(self.path), *flags])
                    self.assertEqual(code, 1)
                    self.assertNotIn('SECRET_MUST_NOT_APPEAR', output.getvalue())
                    self.assertIn(str(self.path).replace('\\', '\\\\') if flags else str(self.path), output.getvalue())

    def test_json_success(self):
        self.path.write_text(VALID)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(validator.main([str(self.path), '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['problems'], [])

    def test_missing_file_parser_and_size_limit(self):
        self.assertTrue(validator.validate(self.path))
        with patch.dict(sys.modules, {'yaml': None}):
            self.assertIn('PyYAML', validator.validate(self.path)[0].message)
        self.assertIn('limit', self.check('#' * (1024 * 1024 + 1))[0].message)
