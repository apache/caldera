"""Tests that all plugins have a requirements.txt file."""
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = str(_REPO_ROOT / 'plugins')


def _discover_plugins():
    """Return sorted list of plugin names that have a hook.py (i.e., are real caldera plugins)."""
    assert os.path.isdir(PLUGINS_DIR), (
        'plugins directory not found at %s — ensure plugins are checked out' % PLUGINS_DIR
    )
    plugins = sorted(
        p for p in os.listdir(PLUGINS_DIR)
        if os.path.isfile(os.path.join(PLUGINS_DIR, p, 'hook.py'))
    )
    assert plugins, (
        'No plugins with hook.py found in %s — ensure submodules are initialised' % PLUGINS_DIR
    )
    return plugins


@pytest.mark.parametrize('plugin', _discover_plugins())
def test_plugin_has_requirements_txt(plugin):
    req_path = os.path.join(PLUGINS_DIR, plugin, 'requirements.txt')
    assert os.path.isfile(req_path), (
        'Plugin %s is missing requirements.txt at %s' % (plugin, req_path)
    )
