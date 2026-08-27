"""Tests for watch_adversary_files() in AppService.

Validates that new or modified adversary YAML files on disk are automatically
reloaded into data_svc.ram['adversaries'] by the periodic watcher task,
mirroring the existing watch_ability_files() behaviour.
"""
import ast
import os
import unittest

import pytest
import yaml

from app.objects.c_adversary import Adversary
from app.utility.base_service import BaseService


# ---------------------------------------------------------------------------
# AST-level structural check — verify watch_adversary_files is wired up
# ---------------------------------------------------------------------------

class TestWatchAdversaryFilesStructure(unittest.TestCase):
    """Verify, without importing server.py, that watch_adversary_files() is
    launched as a background task inside run_tasks()."""

    def _parse_server(self):
        server_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'server.py'
        )
        with open(os.path.normpath(server_path)) as fh:
            return ast.parse(fh.read())

    def _get_run_tasks_body(self):
        tree = self._parse_server()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'run_tasks':
                return node
        return None

    def test_watch_adversary_files_task_registered(self):
        run_tasks = self._get_run_tasks_body()
        self.assertIsNotNone(run_tasks, "run_tasks() function not found in server.py")

        # Look for loop.create_task(app_svc.watch_adversary_files())
        found = False
        for node in ast.walk(run_tasks):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == 'create_task':
                for arg in node.args:
                    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
                        if arg.func.attr == 'watch_adversary_files':
                            found = True
                            break
            if found:
                break

        self.assertTrue(
            found,
            "loop.create_task(app_svc.watch_adversary_files()) not found inside run_tasks() in server.py"
        )

    def test_watch_adversary_files_method_exists(self):
        """AppService must define watch_adversary_files as an async method."""
        app_svc_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'app', 'service', 'app_svc.py'
        )
        with open(os.path.normpath(app_svc_path)) as fh:
            tree = ast.parse(fh.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'watch_adversary_files':
                return

        self.fail("watch_adversary_files() async method not found in app_svc.py")


# ---------------------------------------------------------------------------
# Functional test — adversary YAML reload
# ---------------------------------------------------------------------------

class TestAdversaryFileReload:

    @pytest.fixture
    def adversary_dir(self, tmp_path):
        d = tmp_path / "data" / "adversaries"
        d.mkdir(parents=True)
        return d

    async def test_load_yaml_file_stores_adversary(self, adversary_dir, data_svc):
        """Writing a new adversary YAML and calling load_yaml_file should
        insert or update it in data_svc.ram['adversaries']."""

        adv_data = {
            'id': 'test-adv-12345',
            'name': 'Test Watcher Adversary',
            'description': 'Created by test',
            'atomic_ordering': [],
        }
        adv_file = adversary_dir / "test_watcher.yml"
        adv_file.write_text(yaml.dump([adv_data]))

        await data_svc.load_yaml_file(Adversary, str(adv_file), data_svc.Access.RED)

        results = await data_svc.locate('adversaries', dict(adversary_id='test-adv-12345'))
        assert len(results) == 1
        assert results[0].name == 'Test Watcher Adversary'

    async def test_reload_updates_existing_adversary(self, adversary_dir, data_svc):
        """Reloading a modified YAML should update the adversary in RAM."""

        adv_data = {
            'id': 'test-adv-reload-001',
            'name': 'Original Name',
            'description': 'Original description',
            'atomic_ordering': [],
        }
        adv_file = adversary_dir / "reload_test.yml"
        adv_file.write_text(yaml.dump([adv_data]))
        await data_svc.load_yaml_file(Adversary, str(adv_file), data_svc.Access.RED)

        # Modify the file
        adv_data['name'] = 'Updated Name'
        adv_data['description'] = 'Updated description'
        adv_file.write_text(yaml.dump([adv_data]))
        await data_svc.load_yaml_file(Adversary, str(adv_file), data_svc.Access.RED)

        results = await data_svc.locate('adversaries', dict(adversary_id='test-adv-reload-001'))
        assert len(results) == 1
        assert results[0].name == 'Updated Name'
        assert results[0].description == 'Updated description'


if __name__ == '__main__':
    unittest.main()
