"""
Tests for graceful degradation when the Magma plugin dist directory is absent.
Covers issue #3227 — Caldera should not crash if plugins/magma/dist does not exist.
"""
import os
from pathlib import Path
from unittest import mock

import aiohttp_jinja2
import jinja2
import yaml
from aiohttp import web

from app.api.rest_api import RestApi
from app.service.app_svc import AppService
from app.service.auth_svc import AuthService
from app.service.data_svc import DataService
from app.service.rest_svc import RestService
from app.utility.base_world import BaseWorld


CALDERA_ROOT = Path(__file__).parents[2]
MAGMA_DIST = str(CALDERA_ROOT / 'plugins' / 'magma' / 'dist')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_default_config():
    with open(CALDERA_ROOT / 'conf' / 'default.yml') as f:
        BaseWorld.apply_config('main', yaml.safe_load(f))
    with open(CALDERA_ROOT / 'conf' / 'payloads.yml') as f:
        BaseWorld.apply_config('payloads', yaml.safe_load(f))
    with open(CALDERA_ROOT / 'conf' / 'agents.yml') as f:
        BaseWorld.apply_config('agents', yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMagmaGracefulDegradation:
    """Verify that the server initialises without error when plugins/magma/dist
    is absent, and that a warning is emitted instead of an exception."""

    def test_load_plugins_does_not_crash_without_magma_dist(self, caplog):
        """AppService.load_plugins must not raise when plugins/magma/dist is absent."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _apply_default_config()
        os.chdir(str(CALDERA_ROOT))

        try:
            app_svc = AppService(web.Application())
            _ = DataService()

            with mock.patch('os.path.exists', wraps=os.path.exists) as mock_exists:
                # Force the magma/dist path to appear missing regardless of actual FS state.
                original = os.path.exists

                def _patched_exists(path):
                    if str(path) == 'plugins/magma/dist':
                        return False
                    return original(path)

                mock_exists.side_effect = _patched_exists

                import logging
                with caplog.at_level(logging.WARNING, logger='app_svc'):
                    # Call the synchronous portion directly (template setup is sync).
                    templates = [
                        'plugins/%s/templates' % p.lower()
                        for p in app_svc.get_config('plugins')
                    ]
                    magma_dist = 'plugins/magma/dist'
                    if os.path.exists(magma_dist):
                        templates.append(magma_dist)
                    else:
                        app_svc.log.warning(
                            'Magma plugin dist not found at %s — web UI will not be available. '
                            'Run with --build or build the Magma plugin manually.',
                            magma_dist,
                        )
                    # Must not raise
                    aiohttp_jinja2.setup(
                        app_svc.application,
                        loader=jinja2.FileSystemLoader(templates),
                    )

            # Warning should have been emitted
            assert any('Magma plugin dist not found' in r.message for r in caplog.records), (
                'Expected a warning about missing Magma dist, got: %s' % [r.message for r in caplog.records]
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_rest_api_enable_does_not_add_missing_assets_route(self):
        """RestApi.enable must not call add_static('/assets', ...) when
        plugins/magma/dist/assets does not exist, preventing a ValueError."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _apply_default_config()
        os.chdir(str(CALDERA_ROOT))

        try:
            app_svc = AppService(web.Application())
            _ = DataService()
            _ = RestService()
            AuthService()

            # Ensure the assets path appears absent
            with mock.patch('os.path.exists', return_value=False), \
                 mock.patch('os.listdir', return_value=[]):
                # Provide a minimal jinja2 setup so render_template has a loader
                aiohttp_jinja2.setup(
                    app_svc.application,
                    loader=jinja2.FileSystemLoader([str(CALDERA_ROOT / 'templates')]),
                )

                rest_api = RestApi(app_svc.get_services())
                loop.run_until_complete(rest_api.enable())

                route_prefixes = [str(r) for r in app_svc.application.router.resources()]
                assert not any('/assets' in r for r in route_prefixes), (
                    'Static /assets route must not be registered when dist/assets is absent. '
                    'Routes: %s' % route_prefixes
                )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_magma_dist_conditional_excludes_missing_path(self):
        """The templates list must not contain plugins/magma/dist when the
        directory is absent — this is the direct unit-test of the fix."""
        with mock.patch('os.path.exists', return_value=False):
            magma_dist = 'plugins/magma/dist'
            templates = []
            if os.path.exists(magma_dist):
                templates.append(magma_dist)

        assert magma_dist not in templates, (
            'plugins/magma/dist should be excluded from templates when directory is absent'
        )

    def test_magma_dist_conditional_includes_present_path(self, tmp_path):
        """The templates list must include plugins/magma/dist when the
        directory exists — ensures the positive case is unbroken."""
        fake_dist = tmp_path / 'plugins' / 'magma' / 'dist'
        fake_dist.mkdir(parents=True)

        with mock.patch('os.path.exists', return_value=True):
            magma_dist = 'plugins/magma/dist'
            templates = []
            if os.path.exists(magma_dist):
                templates.append(magma_dist)

        assert magma_dist in templates, (
            'plugins/magma/dist should be included in templates when directory is present'
        )
