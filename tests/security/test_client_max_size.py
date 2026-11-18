from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from aiohttp import FormData, web

import app.api.v2 as v2_module
from app.api.v2.security import pass_option_middleware
from app.utility.base_world import BaseWorld

MIB = 1024 * 1024
DEFAULT_REQUEST_LIMIT = 25 * MIB  # the limit every release before 5.4.0 hardcoded


def _make_app(upload_max_size_mb):
    """Call the real make_app with mock services and return the resulting app."""
    services = MagicMock()
    return v2_module.make_app(services, upload_max_size_mb=upload_max_size_mb)


@pytest.mark.parametrize('mb,expected_bytes', [
    (1, 1 * 1024 * 1024),
    (100, 100 * 1024 * 1024),
    (50, 50 * 1024 * 1024),
])
def test_valid_integer_config(mb, expected_bytes):
    # _client_max_size is the only accessible attribute for client_max_size in aiohttp
    assert _make_app(mb)._client_max_size == expected_bytes


@pytest.mark.parametrize('bad_val', [None, '', 'abc', '1MB', -1, 0])
def test_invalid_config_falls_back_to_default(bad_val):
    """None, strings, and non-positive values must fall back to the 25MB default."""
    assert _make_app(bad_val)._client_max_size == 25 * MIB
    assert _make_app(bad_val)[v2_module.CLIENT_MAX_SIZE] == 25 * MIB


def test_string_integer_coerces_correctly():
    """Config may return strings; '50' must coerce to 50MB, not repeat a string."""
    assert _make_app('50')._client_max_size == 50 * 1024 * 1024


def test_make_app_records_upload_limit():
    """The v2 app records its limit so the root app can apply it to v2 routes."""
    assert _make_app(100)[v2_module.CLIENT_MAX_SIZE] == 100 * MIB


def test_default_limits_are_25_mib():
    """Agent output and uploads up to 25 MiB must still be accepted by default."""
    conf = yaml.safe_load((Path(__file__).parents[2] / 'conf' / 'default.yml').read_text())
    assert conf['client_max_size_mb'] * MIB == DEFAULT_REQUEST_LIMIT
    assert conf['api_upload_max_size_mb'] * MIB == DEFAULT_REQUEST_LIMIT
    assert v2_module.DEFAULT_UPLOAD_MAX_SIZE_MB * MIB == DEFAULT_REQUEST_LIMIT


def test_root_and_subapp_limits():
    """Root app has 1MB limit; v2 subapp has 100MB limit; subapp limit exceeds root."""
    BaseWorld.apply_config('main', {'client_max_size_mb': 1, 'api_upload_max_size_mb': 100})
    root_app = web.Application(
        client_max_size=1 * 1024 * 1024,
        middlewares=[pass_option_middleware]
    )
    v2_app = v2_module.make_app(MagicMock(), upload_max_size_mb=100)
    root_app.add_subapp('/api/v2', v2_app)
    assert root_app._client_max_size == 1 * 1024 * 1024
    assert v2_app._client_max_size == 100 * 1024 * 1024
    assert v2_app._client_max_size > root_app._client_max_size


async def _body_size(request):
    return web.json_response({'size': len(await request.read())})


async def _upload_size(request):
    form = await request.post()
    return web.json_response({'size': len(form['file'].file.read())})


@web.middleware
async def _read_form_early(request, handler):
    """Stands in for root middleware that parses the body before the handler runs."""
    if request.path == '/api/v2/payloads':
        await request.post()
    return await handler(request)


def _limited_app(root_limit, api_limit):
    root = web.Application(client_max_size=root_limit,
                           middlewares=[pass_option_middleware, v2_module.request_size_middleware, _read_form_early])
    root.router.add_post('/report', _body_size)
    api = web.Application(client_max_size=api_limit)
    api[v2_module.CLIENT_MAX_SIZE] = api_limit
    api.router.add_post('/payloads', _upload_size)
    api.router.add_post('/echo', _body_size)
    root.add_subapp('/api/v2', api)
    return root


def _upload(size):
    data = FormData()
    data.add_field('file', b'x' * size, filename='payload.bin', content_type='application/octet-stream')
    return data


async def test_subapp_limit_applies_to_subapp_routes(aiohttp_client):
    client = await aiohttp_client(_limited_app(root_limit=1024, api_limit=16 * 1024))

    resp = await client.post('/api/v2/payloads', data=_upload(8 * 1024))
    assert resp.status == 200
    assert (await resp.json())['size'] == 8 * 1024

    resp = await client.post('/api/v2/echo', data=b'x' * 8 * 1024)
    assert resp.status == 200

    assert (await client.post('/api/v2/payloads', data=_upload(32 * 1024))).status == 413
    assert (await client.post('/api/v2/echo', data=b'x' * 32 * 1024)).status == 413


async def test_root_limit_still_applies_to_root_routes(aiohttp_client):
    client = await aiohttp_client(_limited_app(root_limit=1024, api_limit=16 * 1024))

    assert (await client.post('/report', data=b'x' * 512)).status == 200
    assert (await client.post('/report', data=b'x' * 8 * 1024)).status == 413


async def test_subapp_limit_can_be_lower_than_root(aiohttp_client):
    client = await aiohttp_client(_limited_app(root_limit=16 * 1024, api_limit=1024))

    assert (await client.post('/report', data=b'x' * 8 * 1024)).status == 200
    assert (await client.post('/api/v2/echo', data=b'x' * 8 * 1024)).status == 413
    assert (await client.post('/api/v2/payloads', data=_upload(8 * 1024))).status == 413
