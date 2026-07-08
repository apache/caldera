import pytest
from unittest.mock import MagicMock, AsyncMock
from aiohttp import web
from app.api.v2.security import pass_option_middleware


class TestOptionsAuthMiddleware:
    @pytest.mark.asyncio
    async def test_options_non_api_returns_ok(self):
        """OPTIONS on non-API paths should auto-return 200."""
        request = MagicMock()
        request.method = 'OPTIONS'
        request.path = '/some/ui/path'
        handler = AsyncMock(return_value=web.Response(status=200))

        with pytest.raises(web.HTTPOk):
            await pass_option_middleware(request, handler)

    @pytest.mark.asyncio
    async def test_options_api_v2_passes_through(self):
        """OPTIONS on /api/v2/ paths should NOT auto-return 200."""
        request = MagicMock()
        request.method = 'OPTIONS'
        request.path = '/api/v2/agents'
        expected_response = web.Response(status=200)
        handler = AsyncMock(return_value=expected_response)

        result = await pass_option_middleware(request, handler)
        handler.assert_called_once_with(request)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_get_request_passes_through(self):
        """Non-OPTIONS requests always pass through."""
        request = MagicMock()
        request.method = 'GET'
        request.path = '/any/path'
        expected_response = web.Response(status=200)
        handler = AsyncMock(return_value=expected_response)

        result = await pass_option_middleware(request, handler)
        handler.assert_called_once_with(request)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_options_api_v2_prefix_not_falsely_matched(self):
        """OPTIONS on /api/v20/ should not be treated as an /api/v2/ path."""
        request = MagicMock()
        request.method = 'OPTIONS'
        request.path = '/api/v20/agents'
        handler = AsyncMock(return_value=web.Response(status=200))

        # /api/v20/ does not start with /api/v2/ so OPTIONS gets auto-200
        with pytest.raises(web.HTTPOk):
            await pass_option_middleware(request, handler)

    @pytest.mark.asyncio
    async def test_options_api_v2_exact_path_passes_through(self):
        """OPTIONS on exact /api/v2 (no trailing slash) should NOT auto-return 200."""
        request = MagicMock()
        request.method = 'OPTIONS'
        request.path = '/api/v2'
        expected_response = web.Response(status=200)
        handler = AsyncMock(return_value=expected_response)

        result = await pass_option_middleware(request, handler)
        handler.assert_called_once_with(request)
        assert result.status == 200
