"""Verify that V1 file endpoints enforce authentication via the @check_authorization decorator."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from aiohttp.test_utils import make_mocked_request


class TestV1FileEndpointAuth:
    """Behavioral tests that call the actual handler methods (through the
    @check_authorization decorator) and verify that unauthenticated requests
    are rejected while authenticated requests proceed."""

    def _build_api(self, *, auth_raises=False):
        """Construct a minimal RestApi instance with mocked services.

        When *auth_raises* is True, ``auth_svc.check_permissions`` raises
        ``web.HTTPUnauthorized``, simulating an unauthenticated caller.
        """
        from app.api.rest_api import RestApi

        api = RestApi.__new__(RestApi)
        api.log = MagicMock()

        auth_svc = MagicMock()
        if auth_raises:
            auth_svc.check_permissions = AsyncMock(
                side_effect=web.HTTPUnauthorized()
            )
        else:
            auth_svc.check_permissions = AsyncMock(return_value=True)
        api.auth_svc = auth_svc

        file_svc = MagicMock()
        file_svc.get_file = AsyncMock(return_value=(b'payload', b'content', 'test.txt'))
        file_svc.save_multipart_file_upload = AsyncMock(return_value=web.Response(text='ok'))
        file_svc.create_exfil_sub_directory = AsyncMock(return_value='/tmp/exfil')
        file_svc.create_exfil_operation_directory = AsyncMock(return_value='/tmp/exfil/op')
        api.file_svc = file_svc

        return api

    # -- upload_file ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_upload_rejects_unauthenticated(self):
        """upload_file must raise HTTPUnauthorized for unauthenticated callers."""
        api = self._build_api(auth_raises=True)
        request = make_mocked_request('POST', '/file/upload', headers={'Directory': 'payloads'})
        with pytest.raises(web.HTTPUnauthorized):
            await api.upload_file(request)

    @pytest.mark.asyncio
    async def test_upload_allows_authenticated(self):
        """upload_file must proceed when auth_svc approves the caller."""
        api = self._build_api(auth_raises=False)
        request = make_mocked_request('POST', '/file/upload', headers={'Directory': 'payloads'})
        resp = await api.upload_file(request)
        assert resp.status == 200

    # -- download_file -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_download_rejects_unauthenticated(self):
        """download_file must raise HTTPUnauthorized for unauthenticated callers."""
        api = self._build_api(auth_raises=True)
        request = make_mocked_request('GET', '/file/download')
        with pytest.raises(web.HTTPUnauthorized):
            await api.download_file(request)

    @pytest.mark.asyncio
    async def test_download_allows_authenticated(self):
        """download_file must return file content when auth_svc approves the caller."""
        api = self._build_api(auth_raises=False)
        request = make_mocked_request('GET', '/file/download')
        resp = await api.download_file(request)
        assert resp.status == 200
        assert resp.headers.get('FILENAME') == 'test.txt'

    # -- decorator presence --------------------------------------------------

    def test_upload_has_check_authorization_decorator(self):
        """Structural: upload_file should be wrapped by check_authorization."""
        from app.api.rest_api import RestApi
        # The decorator replaces the method; the wrapper name is 'helper'
        assert RestApi.upload_file.__name__ == 'helper', (
            'upload_file does not appear to be wrapped by @check_authorization'
        )

    def test_download_has_check_authorization_decorator(self):
        """Structural: download_file should be wrapped by check_authorization."""
        from app.api.rest_api import RestApi
        assert RestApi.download_file.__name__ == 'helper', (
            'download_file does not appear to be wrapped by @check_authorization'
        )
