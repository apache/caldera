import asyncio
import os
import tempfile
import time

import pytest
import yaml

from unittest.mock import AsyncMock, MagicMock, patch

from app.objects.c_adversary import Adversary
from app.objects.c_objective import Objective
from app.objects.c_plugin import Plugin
from app.objects.c_source import Source
from app.service.app_svc import AppService


def _make_app_svc():
    """Create an AppService with mocked data_svc and learning_svc for watcher tests."""
    svc = AppService(None)

    mock_data_svc = AsyncMock()
    mock_data_svc.locate = AsyncMock(return_value=[])
    mock_data_svc.load_yaml_file = AsyncMock()
    mock_data_svc.load_ability_file = AsyncMock()

    mock_learning_svc = AsyncMock()
    mock_learning_svc.build_model = AsyncMock()

    svc.get_service = MagicMock(side_effect=lambda name: {
        'data_svc': mock_data_svc,
        'learning_svc': mock_learning_svc,
    }[name])
    svc.get_config = MagicMock(return_value='1')

    return svc, mock_data_svc, mock_learning_svc


@pytest.fixture
def patched_app_svc():
    return _make_app_svc()


def _limited_sleep_factory():
    """Return an async sleep replacement that skips the initial delay then cancels."""
    call_count = 0

    async def limited_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return  # skip the initial delay
        raise asyncio.CancelledError()  # stop after one scan iteration

    return limited_sleep


class TestFileWatchers:

    async def test_watch_adversary_detects_new_file(self, patched_app_svc):
        """File watcher should detect a new adversary YAML file and load it."""
        app_svc, mock_data_svc, _ = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            adv_dir = os.path.join(tmpdir, 'adversaries')
            os.makedirs(adv_dir)

            adv_file = os.path.join(adv_dir, 'test_adversary.yml')
            with open(adv_file, 'w') as f:
                yaml.dump({'id': '1234', 'name': 'test', 'description': 'test adv'}, f)

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_adversary_files()

            mock_data_svc.load_yaml_file.assert_called()
            call_args = mock_data_svc.load_yaml_file.call_args
            assert call_args[0][0] == Adversary

    async def test_watch_source_detects_new_file(self, patched_app_svc):
        """File watcher should detect a new source YAML file and load it."""
        app_svc, mock_data_svc, _ = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, 'sources')
            os.makedirs(src_dir)

            src_file = os.path.join(src_dir, 'test_source.yml')
            with open(src_file, 'w') as f:
                yaml.dump({'id': '5678', 'name': 'test source'}, f)

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_source_files()

            mock_data_svc.load_yaml_file.assert_called()
            call_args = mock_data_svc.load_yaml_file.call_args
            assert call_args[0][0] == Source

    async def test_watch_objective_detects_new_file(self, patched_app_svc):
        """File watcher should detect a new objective YAML file and load it."""
        app_svc, mock_data_svc, _ = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            obj_dir = os.path.join(tmpdir, 'objectives')
            os.makedirs(obj_dir)

            obj_file = os.path.join(obj_dir, 'test_objective.yml')
            with open(obj_file, 'w') as f:
                yaml.dump({'id': '9012', 'name': 'test objective'}, f)

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_objective_files()

            mock_data_svc.load_yaml_file.assert_called()
            call_args = mock_data_svc.load_yaml_file.call_args
            assert call_args[0][0] == Objective

    async def test_watch_adversary_detects_modified_file(self, patched_app_svc):
        """File watcher should detect a modified adversary YAML file and reload it."""
        app_svc, mock_data_svc, _ = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            adv_dir = os.path.join(tmpdir, 'adversaries')
            os.makedirs(adv_dir)

            adv_file = os.path.join(adv_dir, 'existing_adversary.yml')
            with open(adv_file, 'w') as f:
                yaml.dump({'id': '1234', 'name': 'original', 'description': 'original'}, f)

            # Touch the file to ensure it has a very recent mtime
            os.utime(adv_file, (time.time(), time.time()))

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_adversary_files()

            mock_data_svc.load_yaml_file.assert_called_once()

    async def test_ability_watcher_rebuilds_learning_model(self, patched_app_svc):
        """After reloading ability files, the learning model should be rebuilt."""
        app_svc, mock_data_svc, mock_learning_svc = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            ability_dir = os.path.join(tmpdir, 'abilities')
            os.makedirs(ability_dir)

            ability_file = os.path.join(ability_dir, 'test_ability.yml')
            with open(ability_file, 'w') as f:
                yaml.dump([{'id': 'ab01', 'name': 'test ability', 'tactic': 'discovery',
                            'technique': {'attack_id': 'T1000', 'name': 'test'},
                            'platforms': {}}], f)

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_ability_files()

            mock_data_svc.load_ability_file.assert_called()
            mock_learning_svc.build_model.assert_called_once()

    async def test_watcher_skips_non_yml_files(self, patched_app_svc):
        """File watcher should skip non-YAML files."""
        app_svc, mock_data_svc, _ = patched_app_svc

        with tempfile.TemporaryDirectory() as tmpdir:
            adv_dir = os.path.join(tmpdir, 'adversaries')
            os.makedirs(adv_dir)

            # Create a non-YAML file
            txt_file = os.path.join(adv_dir, 'readme.txt')
            with open(txt_file, 'w') as f:
                f.write('not yaml')

            plugin = Plugin(data_dir=tmpdir)
            mock_data_svc.locate.return_value = [plugin]

            with patch('asyncio.sleep', side_effect=_limited_sleep_factory()):
                with pytest.raises(asyncio.CancelledError):
                    await app_svc.watch_adversary_files()

            mock_data_svc.load_yaml_file.assert_not_called()
