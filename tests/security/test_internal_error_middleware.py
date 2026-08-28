"""Tests for internal_error_middleware in app.api.v2.responses."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web

from app.api.v2.responses import internal_error_middleware


class TestInternalErrorMiddleware:
    @pytest.mark.asyncio
    async def test_generic_500_on_unhandled_exception(self):
        """Unhandled exceptions must produce a sanitised 500 with no internal detail."""
        request = MagicMock()
        handler = AsyncMock(side_effect=RuntimeError('secret db error'))

        resp = await internal_error_middleware(request, handler)
        assert resp.status == 500
        assert 'internal server error' in resp.text
        assert 'secret db error' not in resp.text

    @pytest.mark.asyncio
    async def test_http_4xx_passes_through_unchanged(self):
        """4xx HTTPExceptions should pass through the middleware unmodified."""
        request = MagicMock()
        original = web.HTTPNotFound()
        handler = AsyncMock(side_effect=original)

        with pytest.raises(web.HTTPNotFound):
            await internal_error_middleware(request, handler)

    @pytest.mark.asyncio
    async def test_normal_response_passes_through(self):
        """Non-error responses should pass through untouched."""
        request = MagicMock()
        handler = AsyncMock(return_value=web.Response(text='ok'))

        resp = await internal_error_middleware(request, handler)
        assert resp.text == 'ok'

    @pytest.mark.asyncio
    async def test_http_500_with_internal_detail_is_sanitised(self):
        """HTTPInternalServerError with detail must be replaced with generic body."""
        request = MagicMock()
        handler = AsyncMock(
            side_effect=web.HTTPInternalServerError(text='secret stack trace: db=prod-host')
        )

        resp = await internal_error_middleware(request, handler)
        assert resp.status == 500
        assert 'internal server error' in resp.text
        assert 'secret stack trace' not in resp.text
        assert 'db=prod-host' not in resp.text

    @pytest.mark.asyncio
    async def test_http_403_passes_through(self):
        """HTTPForbidden (4xx) should pass through completely unchanged."""
        request = MagicMock()
        original = web.HTTPForbidden(reason='access denied')
        handler = AsyncMock(side_effect=original)

        with pytest.raises(web.HTTPForbidden) as exc_info:
            await internal_error_middleware(request, handler)
        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_http_503_preserves_status_code(self):
        """Non-500 5xx exceptions (e.g. 503) must preserve their status code,
        not be rewritten to 500."""
        request = MagicMock()
        handler = AsyncMock(
            side_effect=web.HTTPServiceUnavailable(
                text='backend overloaded',
                headers={'Retry-After': '30'},
            )
        )

        resp = await internal_error_middleware(request, handler)
        assert resp.status == 503, f'Expected 503, got {resp.status}'
        assert 'internal server error' in resp.text
        assert 'backend overloaded' not in resp.text
        # Retry-After header from the original exception should be preserved
        assert resp.headers.get('Retry-After') == '30'

    @pytest.mark.asyncio
    async def test_http_502_preserves_status_code(self):
        """502 Bad Gateway should keep its status code."""
        request = MagicMock()
        handler = AsyncMock(
            side_effect=web.HTTPBadGateway(text='upstream failed')
        )

        resp = await internal_error_middleware(request, handler)
        assert resp.status == 502
        assert 'upstream failed' not in resp.text
