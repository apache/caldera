"""Installation diagnostics, including execution without site-packages.

These tests also run with ``python -m unittest discover -s tests/utility
-p test_doctor.py``, independently of the server-dependent pytest conftest.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utility import doctor


class TestDoctor(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ('conf', 'data/results', 'data/payloads', 'plugins/magma/dist', 'plugins/stockpile'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write('requirements.txt', 'example==1.0\n')
        self.write('conf/default.yml', 'plugins: [stockpile]\n')
        self.write('conf/local.yml', 'plugins: [stockpile]\napi_key_red: SECRET_MUST_NOT_APPEAR\n')
        self.write('plugins/magma/hook.py', 'raise AssertionError("doctor must not import plugins")\n')
        self.write('plugins/stockpile/hook.py', '')
        self.write('plugins/magma/dist/index.html', '<html></html>')
        self.enterContext(patch.object(doctor.platform, 'system', return_value='Linux'))
        self.enterContext(patch.object(doctor.metadata, 'version', return_value='1.0'))
        self.enterContext(patch.object(doctor, 'check_tool', side_effect=lambda name, *args: doctor.Check(name, 'PASS', 'Available.')))

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def failures(self, checks):
        return [c for c in checks if c.status == 'FAIL']

    def run_main(self, *args):
        stream = io.StringIO()
        with patch.object(doctor, 'ROOT', self.root), contextlib.redirect_stdout(stream):
            code = doctor.main(list(args))
        return code, stream.getvalue()

    def test_healthy_installation_without_importing_plugins(self):
        self.assertEqual(self.failures(doctor.diagnose(self.root)), [])
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn('0 failed', output)
        self.assertNotIn('SECRET_MUST_NOT_APPEAR', output)

    def test_json_failure_has_actionable_remedy_and_exit_code(self):
        (self.root / 'plugins/stockpile/hook.py').unlink()
        code, output = self.run_main('--json')
        report = json.loads(output)
        failed = self.failures([doctor.Check(**c) for c in report['checks']])
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['FAIL'], 1)
        self.assertEqual(failed[0].name, 'plugin:stockpile')
        self.assertIn('git submodule update', failed[0].remedy)

    def test_default_fallback_does_not_create_files(self):
        (self.root / 'conf/local.yml').unlink()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        checks = doctor.diagnose(self.root)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.failures(checks), [])
        self.assertTrue(any(c.name == 'configuration' and c.status == 'WARN' for c in checks))

    def test_custom_environment_does_not_fall_back(self):
        checks = doctor.diagnose(self.root, 'missing')
        self.assertTrue(any(c.name == 'configuration' for c in self.failures(checks)))

    def test_explicit_environment_is_used(self):
        self.write('conf/exercise.yml', 'plugins: [missing_plugin]\n')
        checks = doctor.diagnose(self.root, 'exercise')
        self.assertTrue(any(c.name == 'plugin:missing_plugin' for c in self.failures(checks)))

    def test_yaml_errors_do_not_disclose_secrets(self):
        self.write('conf/local.yml', 'api_key_red: [SECRET_MUST_NOT_APPEAR\n')
        for args in ((), ('--json',)):
            with self.subTest(args=args):
                code, output = self.run_main(*args)
                self.assertEqual(code, 1)
                self.assertNotIn('SECRET_MUST_NOT_APPEAR', output)
                self.assertIn('configuration', output)

    def test_invalid_plugin_lists_are_reported(self):
        for value in ('null', 'stockpile', '[null]', '[true]', '[../outside]', '{}'):
            with self.subTest(value=value):
                self.write('conf/local.yml', f'plugins: {value}\n')
                self.assertTrue(any(c.name == 'configuration' for c in self.failures(doctor.diagnose(self.root))))

    def test_non_mapping_config_is_reported(self):
        self.write('conf/local.yml', '- stockpile\n')
        self.assertTrue(any(c.name == 'configuration' for c in self.failures(doctor.diagnose(self.root))))

    def test_missing_package_is_reported(self):
        doctor.metadata.version.side_effect = doctor.metadata.PackageNotFoundError('example')
        checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual(checks[0].status, 'FAIL')
        self.assertIn('example is not installed', checks[0].message)

    def test_incompatible_package_is_reported(self):
        doctor.metadata.version.return_value = '2.0'
        checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual(checks[0].status, 'FAIL')
        self.assertIn('does not satisfy ==1.0', checks[0].message)

    def test_comments_markers_and_compatible_versions(self):
        self.write('requirements.txt', '# comment\nexample~=1.0  # inline comment\nmissing; python_version < "0"\n')
        checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual([c.status for c in checks], ['PASS'])
        doctor.metadata.version.assert_called_once_with('example')

    def test_installed_prerelease_is_checked_against_version_bounds(self):
        self.write('requirements.txt', 'example>=1.0\n')
        doctor.metadata.version.return_value = '2.0a1'
        checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual(checks[0].status, 'PASS')

    def test_unreadable_or_invalid_package_metadata_is_reported(self):
        for value in (None, 'invalid-version', OSError('metadata unreadable')):
            with self.subTest(value=value):
                doctor.metadata.version.side_effect = value if isinstance(value, Exception) else None
                doctor.metadata.version.return_value = value
                checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
                self.assertEqual(checks[0].status, 'FAIL')

    def test_warnings_do_not_change_success_exit_code(self):
        (self.root / 'conf/local.yml').unlink()
        code, output = self.run_main('--json')
        self.assertEqual(code, 0)
        self.assertGreater(json.loads(output)['summary']['WARN'], 0)

    def test_pip_options_urls_and_extras_are_not_silently_accepted(self):
        self.write('requirements.txt', '-r other.txt\nexample @ https://example.invalid/package\nexample[extra]==1.0\n')
        checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual([c.status for c in checks], ['WARN', 'WARN', 'WARN'])
        doctor.metadata.version.assert_not_called()

    def test_missing_requirements_file_is_reported(self):
        checks = doctor.check_requirements(self.root / 'missing.txt', self.root)
        self.assertEqual(checks[0].status, 'FAIL')

    def test_missing_packaging_does_not_crash(self):
        with patch.dict(sys.modules, {'packaging.requirements': None}):
            checks = doctor.check_requirements(self.root / 'requirements.txt', self.root)
        self.assertEqual(checks[0].status, 'FAIL')
        self.assertIn('packaging is not installed', checks[0].message)

    def test_missing_yaml_does_not_crash(self):
        with patch.dict(sys.modules, {'yaml': None}):
            config, checks = doctor.load_config(self.root, 'local')
        self.assertIsNone(config)
        self.assertEqual(checks[-1].status, 'FAIL')
        self.assertIn('PyYAML is not installed', checks[-1].message)

    def test_enabled_plugin_requirements_are_required(self):
        self.write('plugins/stockpile/requirements.txt', 'plugin_dependency==2.0\n')
        checks = doctor.diagnose(self.root)
        self.assertTrue(any(c.name.startswith('plugins/stockpile/requirements.txt') for c in self.failures(checks)))

    def test_disabled_plugin_requirements_are_optional(self):
        self.write('plugins/custom/hook.py', '')
        self.write('plugins/custom/requirements.txt', 'plugin_dependency==2.0\n')
        checks = doctor.diagnose(self.root)
        self.assertEqual(self.failures(checks), [])
        self.assertTrue(any(c.name.startswith('plugins/custom/requirements.txt') and c.status == 'WARN' for c in checks))

    def test_uninitialized_disabled_plugin_is_still_reported(self):
        (self.root / 'plugins/disabled').mkdir()
        self.assertTrue(any(c.name == 'plugin:disabled' for c in self.failures(doctor.diagnose(self.root))))

    def test_optional_debrief_and_ldap_requirements_are_checked_when_selected(self):
        self.write('conf/local.yml', 'plugins: [debrief]\nldap: {server: ldap.example.invalid}\n')
        self.write('plugins/debrief/hook.py', '')
        self.write('requirements-debrief.txt', 'svglib==2.0\n')
        self.write('requirements-ldap.txt', 'ldap3==2.0\n')
        checks = doctor.diagnose(self.root)
        self.assertEqual(self.failures(checks), [])
        for filename in ('requirements-debrief.txt', 'requirements-ldap.txt'):
            self.assertTrue(any(c.name.startswith(filename) and c.status == 'WARN' for c in checks))

    def test_missing_ui_requires_build_tools_and_returns_failure(self):
        (self.root / 'plugins/magma/dist/index.html').unlink()
        checks = doctor.diagnose(self.root)
        self.assertTrue(any(c.name == 'web-ui' for c in self.failures(checks)))
        self.assertIn('Node.js', [c.name for c in checks])
        self.assertIn('npm', [c.name for c in checks])

    def test_prebuilt_ui_does_not_require_node_or_npm(self):
        checks = doctor.diagnose(self.root)
        self.assertNotIn('Node.js', [c.name for c in checks])
        self.assertNotIn('npm', [c.name for c in checks])

    def test_unwritable_directory_is_reported_without_write_probe(self):
        with patch.object(doctor.os, 'access', return_value=False):
            checks = doctor.diagnose(self.root)
        self.assertTrue(any(c.name == 'data/results' for c in self.failures(checks)))

    def test_unsupported_platform_and_python_are_reported(self):
        with patch.object(doctor.platform, 'system', return_value='Windows'), patch.object(doctor.sys, 'version_info', (3, 10)):
            names = [c.name for c in self.failures(doctor.diagnose(self.root))]
        self.assertIn('platform', names)
        self.assertIn('python', names)

    def test_environment_path_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            doctor.main(['-E', '../outside'])
        self.assertEqual(raised.exception.code, 2)

    def test_standalone_execution_without_dependencies_or_server_imports(self):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(doctor.__file__)), '--json'],
                                cwd=self.root, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertGreater(report['summary']['FAIL'], 0)
        self.assertTrue(any('packaging is not installed' in c['message'] for c in report['checks']))


class TestToolProbe(unittest.TestCase):
    def test_missing_tool(self):
        with patch.object(doctor.shutil, 'which', return_value=None):
            check = doctor.check_tool('Go', ['go', 'version'], 'Install Go.')
        self.assertEqual(check.status, 'WARN')

    def test_timeout_os_error_and_invalid_output(self):
        failures = (subprocess.TimeoutExpired('go', 5), OSError('unavailable'),
                    subprocess.CompletedProcess([], 1, 'go1.24.0', ''),
                    subprocess.CompletedProcess([], 0, 'unexpected output', ''))
        for failure in failures:
            with self.subTest(failure=failure), patch.object(doctor.shutil, 'which', return_value='/usr/bin/go'):
                kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch.object(doctor.subprocess, 'run', **kwargs):
                    check = doctor.check_tool('Go', ['go', 'version'], 'Install Go.')
                self.assertEqual(check.status, 'WARN')

    def test_version_probe_has_timeout_and_does_not_print_raw_output(self):
        result = subprocess.CompletedProcess([], 0, 'go version go1.24.0 linux/amd64\nSECRET_MUST_NOT_APPEAR', '')
        with patch.object(doctor.shutil, 'which', return_value='/usr/bin/go'), patch.object(doctor.subprocess, 'run', return_value=result) as run:
            check = doctor.check_tool('Go', ['go', 'version'], 'Install Go.')
        self.assertEqual(check.status, 'PASS')
        self.assertIn('1.24.0', check.message)
        self.assertNotIn('SECRET_MUST_NOT_APPEAR', check.message)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/go', 'version'])
        self.assertEqual(run.call_args.kwargs['timeout'], 5)
        self.assertNotIn('shell', run.call_args.kwargs)
