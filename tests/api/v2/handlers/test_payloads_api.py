import io
import os
import pathlib
import tempfile
from aiohttp import FormData
from http import HTTPStatus
from unittest import mock

from app.api.v2.handlers.payload_api import PayloadApi
from app.utility.base_service import BaseService

import pytest


@pytest.fixture
def expected_payload_file_paths():
    """
    Generates (and deletes) real dummy files because the payload API looks for payload files in
    "data/payloads" and/or in "plugins/<plugin-name>/payloads".
    :return: A set of relative paths of dummy payloads.
    """
    directory = "data/payloads"
    os.makedirs(directory, exist_ok=True)

    file_paths = set()
    current_working_dir = os.getcwd()

    try:
        for _ in range(3):
            fd, file_path = tempfile.mkstemp(prefix="payload_", dir=directory)
            os.close(fd)
            relative_path = os.path.relpath(file_path, start=current_working_dir)
            file_paths.add(relative_path)
        yield file_paths
    finally:
        for file_path in file_paths:
            os.remove(file_path)


@pytest.fixture
def expected_payload_file_names(expected_payload_file_paths):
    return {os.path.basename(path) for path in expected_payload_file_paths}


# Return n 0x01 bytes
def _mock_urandom(n):
    return b'\x01' * n


class TestPayloadsApi:
    async def test_get_payloads(self, api_v2_client, api_cookies, expected_payload_file_names):
        resp = await api_v2_client.get('/api/v2/payloads', cookies=api_cookies)
        payload_file_names = await resp.json()
        assert len(payload_file_names) >= len(expected_payload_file_names)

        filtered_payload_file_names = {  # Excluding any other real files in data/payloads...
            file_name for file_name in payload_file_names
            if file_name in expected_payload_file_names
        }

        assert filtered_payload_file_names == expected_payload_file_names

    @pytest.mark.parametrize('query_name', ['payload_', 'PAYLOAD_'])
    async def test_get_payloads_name_filter(self, api_v2_client, api_cookies, expected_payload_file_names, query_name):
        resp = await api_v2_client.get(f'/api/v2/payloads?name={query_name}', cookies=api_cookies)
        assert resp.status == HTTPStatus.OK
        payload_file_names = await resp.json()

        # All expected payloads should be present
        assert expected_payload_file_names <= set(payload_file_names)
        # Every returned payload must match the filter (no false positives)
        assert all('payload_' in pathlib.PurePath(p).name.lower() for p in payload_file_names)

    async def test_get_payloads_name_filter_no_match(self, api_v2_client, api_cookies):
        resp = await api_v2_client.get('/api/v2/payloads?name=__no_match_xyzzy__', cookies=api_cookies)
        assert resp.status == HTTPStatus.OK
        assert await resp.json() == []

    async def test_get_payloads_name_filter_with_sort_and_add_path(
            self, api_v2_client, api_cookies, expected_payload_file_names):
        resp = await api_v2_client.get('/api/v2/payloads?name=payload_&sort=true&add_path=true', cookies=api_cookies)
        assert resp.status == HTTPStatus.OK
        payload_paths = await resp.json()

        # Results should be sorted
        assert payload_paths == sorted(payload_paths)
        # Every returned path's filename must match the filter
        assert all('payload_' in pathlib.PurePath(p).name.lower() for p in payload_paths)
        # Results should contain paths (not bare filenames)
        assert all(os.sep in p or '/' in p for p in payload_paths)

    async def test_unauthorized_get_payloads(self, api_v2_client):
        resp = await api_v2_client.get('/api/v2/payloads')
        assert resp.status == HTTPStatus.UNAUTHORIZED

    @mock.patch.object(pathlib.Path, 'rename')
    async def test_post_payloads(self, mock_rename, api_v2_client, api_cookies):
        file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])
        header_data = b'%userencryptedpayload%' + (b'\x01' * 48)  # 32-byte key + 16-byte IV
        ciphertext = bytes([0x9d, 0x8f, 0xd1, 0xa1, 0x3d, 0x2e, 0xac, 0x17])
        with tempfile.TemporaryFile(mode='w+b') as tmp_file:
            tmp_file.write(file_data)
            tmp_file.flush()
            tmp_file.seek(0)

            m = mock.mock_open()
            with mock.patch.object(os, 'urandom', wraps=_mock_urandom):
                with mock.patch('builtins.open', m):
                    upload_data = FormData()
                    upload_data.add_field('file', tmp_file, filename='testpostpayload')
                    resp = await api_v2_client.post('/api/v2/payloads',
                                                    data=upload_data)
            assert resp.status == HTTPStatus.OK
            assert await resp.json() == dict(payloads=['testpostpayload'])
            mock_rename.assert_called_with('data/payloads/testpostpayload')
            m.assert_called_with('data/payloads/temp_testpostpayload', 'wb')
            m().write.assert_any_call(header_data)
            m().write.assert_any_call(ciphertext)
            m().write.assert_called_with(b'')

    def test_save_file(self):
        original_data = os.urandom(24*1024)
        with tempfile.NamedTemporaryFile() as fp:
            PayloadApi._save_file(fp.name, io.BytesIO(original_data))
            decrypted = BaseService.get_service('file_svc')._read(fp.name)
            assert decrypted == original_data

    async def test_delete_payloads(self, api_v2_client, api_cookies):
        want_path = pathlib.Path('data/payloads/testtodelete').resolve()
        with mock.patch.object(os, 'remove') as mock_remove:
            resp = await api_v2_client.delete('/api/v2/payloads/testtodelete')
            mock_remove.assert_called_once_with(want_path)
            assert resp.status == 204

        # Test ValueError
        with mock.patch.object(os, 'remove', side_effect=ValueError('testvalueerror')) as mock_remove:
            resp = await api_v2_client.delete('/api/v2/payloads/testtodelete')
            assert resp.status == 404
            assert resp.reason == 'testvalueerror'

        # Test FileNotFoundError
        with mock.patch.object(os, 'remove', side_effect=FileNotFoundError()) as mock_remove:
            resp = await api_v2_client.delete('/api/v2/payloads/testtodelete')
            assert resp.status == 404
            assert resp.reason == 'Not Found'

        # Test PermissionError
        with mock.patch.object(os, 'remove', side_effect=PermissionError()) as mock_remove:
            resp = await api_v2_client.delete('/api/v2/payloads/testtodelete')
            assert resp.status == 403
            assert resp.reason == 'Permission denied.'
