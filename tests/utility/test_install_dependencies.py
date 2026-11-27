"""Selection, bootstrap previews and explicit pip execution for plugin installs."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utility import install_dependencies as installer


class TestSelectiveDependencies(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.write('requirements-core.txt', 'example-core==1.0\n')
        self.write('requirements-training.txt', 'example-training==1.0\n')
        self.write('requirements-fieldmanual.txt', 'example-docs==1.0\n')
        for plugin in ('magma', 'training', 'fieldmanual', 'custom', 'disabled'):
            self.write(f'plugins/{plugin}/hook.py', 'raise AssertionError("Hooks must not run during installation planning")\n')
        self.write('plugins/custom/requirements.txt', 'example-custom==1.0\n')
        self.write('plugins/disabled/requirements.txt', 'example-disabled==1.0\n')
        self.enterContext(patch.object(installer, 'ROOT', self.root))

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def main(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = installer.main(args)
        return code, output.getvalue(), errors.getvalue()

    def test_selection_excludes_disabled_plugin(self):
        plan = installer.make_plan(self.root, ['custom'])
        self.assertEqual(plan.plugins, ('custom', 'magma'))
        self.assertEqual(plan.files, ('requirements-core.txt', 'plugins/custom/requirements.txt'))
        self.assertEqual(plan.without_manifest, ('magma',))

    def test_automatic_magma_and_duplicate_selections(self):
        self.assertEqual(installer.make_plan(self.root, []).plugins, ('magma',))
        plan = installer.make_plan(self.root, ['custom', 'custom', 'magma'])
        self.assertEqual(len(plan.files), 2)

    def test_compatibility_manifests_for_current_plugin_pins(self):
        plan = installer.make_plan(self.root, ['training', 'fieldmanual'])
        self.assertEqual(plan.files, ('requirements-core.txt', 'requirements-fieldmanual.txt', 'requirements-training.txt'))

    def test_plugin_manifest_takes_precedence_over_compatibility_file(self):
        self.write('plugins/training/requirements.txt', 'example-new==1.0\n')
        plan = installer.make_plan(self.root, ['training'])
        self.assertIn('plugins/training/requirements.txt', plan.files)
        self.assertNotIn('requirements-training.txt', plan.files)

    def test_invalid_names_and_types(self):
        for value in (None, 'training', [False], ['../outside'], ['/absolute'], [''], ['--option'], ['a/b'], ['ș'], ['aș']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.make_plan(self.root, value)

    def test_missing_selected_plugin_fails_before_pip(self):
        with patch.object(installer.subprocess, 'run') as run:
            code, _, error = self.main(['--plugins', 'missing', '--install'])
        self.assertEqual(code, 1)
        self.assertIn('not checked out', error)
        run.assert_not_called()

    def test_missing_disabled_plugin_is_not_required(self):
        (self.root / 'plugins/disabled/hook.py').unlink()
        self.assertEqual(installer.make_plan(self.root, []).plugins, ('magma',))

    def test_missing_core_or_compatibility_manifest(self):
        for name in ('requirements-core.txt', 'requirements-training.txt'):
            with self.subTest(name=name):
                path = self.root / name
                contents = path.read_text()
                path.unlink()
                with self.assertRaises(ValueError):
                    installer.make_plan(self.root, ['training'])
                path.write_text(contents)

    def test_configuration_selection(self):
        config = self.write('conf/exercise.yml', 'plugins: [custom]\napi_key_red: SECRET_MUST_NOT_APPEAR\n')
        code, output, error = self.main(['--config', str(config), '--json'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(json.loads(output)['plugins'], ['custom', 'magma'])
        self.assertNotIn('SECRET_MUST_NOT_APPEAR', output)

    def test_default_config_is_used(self):
        self.write('conf/default.yml', 'plugins: [training]\n')
        code, output, _ = self.main(['--json'])
        self.assertEqual(code, 0)
        self.assertIn('requirements-training.txt', json.loads(output)['files'])

    def test_missing_config_does_not_fall_back(self):
        self.write('conf/default.yml', 'plugins: []\n')
        code, _, _ = self.main(['--config', str(self.root / 'missing.yml')])
        self.assertEqual(code, 1)

    def test_invalid_yaml_and_plugin_lists_hide_values(self):
        for content in ('secret: [SECRET_MUST_NOT_APPEAR', '{}', 'plugins: null', 'plugins: [false]', 'plugins: training'):
            with self.subTest(content=content):
                path = self.write('bad.yml', content)
                code, output, error = self.main(['--config', str(path)])
                self.assertEqual(code, 1)
                self.assertNotIn('SECRET_MUST_NOT_APPEAR', output + error)

    def test_missing_yaml_allows_explicit_selection_bootstrap(self):
        path = self.write('conf/default.yml', 'plugins: []\n')
        with patch.dict(sys.modules, {'yaml': None}):
            with self.assertRaisesRegex(ValueError, 'PyYAML'):
                installer.configured_plugins(path)
            code, _, _ = self.main(['--plugins', ''])
            self.assertEqual(code, 0)

    def test_preview_never_invokes_pip(self):
        with patch.object(installer.subprocess, 'run') as run:
            code, output, _ = self.main(['--plugins', 'custom'])
        self.assertEqual(code, 0)
        self.assertIn('Preview only', output)
        run.assert_not_called()

    def test_install_refuses_global_interpreter(self):
        plan = installer.make_plan(self.root, [])
        with patch.object(installer.sys, 'prefix', sys.base_prefix), patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'virtual environment'):
                installer.install(plan, self.root)
        run.assert_not_called()

    def test_single_pip_call_uses_current_interpreter_and_propagates_failure(self):
        plan = installer.make_plan(self.root, ['custom'])
        with patch.object(installer.sys, 'prefix', 'test-venv'), patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as run:
            self.assertEqual(installer.install(plan, self.root), 7)
        self.assertEqual(run.call_args.args[0], [
            sys.executable, '-m', 'pip', 'install', '-r', 'requirements-core.txt', '-r', 'plugins/custom/requirements.txt',
        ])
        self.assertEqual(run.call_args.kwargs, {'cwd': self.root, 'check': False})

    def test_explicit_install_executes_previewed_plan(self):
        with patch.object(installer, 'install', return_value=0) as install:
            code, output, _ = self.main(['--plugins', 'training', '--install'])
        self.assertEqual(code, 0)
        self.assertIn('requirements-training.txt', output)
        self.assertEqual(install.call_args.args[0].plugins, ('magma', 'training'))

    def test_json_install_and_conflicting_selection_options_are_rejected(self):
        for args in (['--json', '--install'], ['--plugins', '', '--config', 'conf/default.yml']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                installer.main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_real_bootstrap_preview_without_site_packages(self):
        script = self.write('app/utility/install_dependencies.py', Path(installer.__file__).read_text(encoding='utf-8'))
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), '--plugins', '', '--json'],
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['files'], ['requirements-core.txt'])
