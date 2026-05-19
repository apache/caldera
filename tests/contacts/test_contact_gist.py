import pytest
import aiohttp
import base64
import contextlib
import json
import logging
import uuid
from unittest import mock

from app.objects.c_agent import Agent
from app.objects.secondclass.c_instruction import Instruction
from app.contacts.contact_gist import Contact as GistContact
from app.service.contact_svc import ContactService
from app.service.file_svc import FileSvc

MOCK_GISTS = '''
[
  {
    "url": "https://api.github.com/gists/123",
    "forks_url": "https://api.github.com/gists/123/forks",
    "commits_url": "https://api.github.com/gists/123/commits",
    "id": "123",
    "node_id": "testnodeid",
    "git_pull_url": "https://gist.github.com/123.git",
    "git_push_url": "https://gist.github.com/123.git",
    "html_url": "https://gist.github.com/testuser/123",
    "files": {
      "beacon-566631377468464325": {
        "filename": "beacon-566631377468464325",
        "type": "text/plain",
        "language": null,
        "raw_url": "https://gist.githubusercontent.com/testuser/123/raw/260cc70502fbb5ba3c64c8d943ac4cd2927d9a17/beacon-566631377468464325",
        "size": 704
      }
    },
    "public": false,
    "description": "beacon-566631377468464325",
    "comments_url": "https://api.github.com/gists/123/comments",
    "truncated": false
  },
  {
    "url": "https://api.github.com/gists/456",
    "forks_url": "https://api.github.com/gists/456/forks",
    "commits_url": "https://api.github.com/gists/456/commits",
    "id": "456",
    "node_id": "testnodeid",
    "git_pull_url": "https://gist.github.com/456.git",
    "git_push_url": "https://gist.github.com/456.git",
    "html_url": "https://gist.github.com/testuser/456",
    "files": {
      "results-566631377468464325": {
        "filename": "results-566631377468464325",
        "type": "text/plain",
        "language": null,
        "raw_url": "https://gist.githubusercontent.com/testuser/456/raw/260cc70502fbb5ba3c64c8d943ac4cd2927d9a17/results-566631377468464325",
        "size": 704
      }
    },
    "public": false,
    "description": "results-566631377468464325",
    "comments_url": "https://api.github.com/gists/456/comments",
    "truncated": false
  },
  {
    "url": "https://api.github.com/gists/789",
    "forks_url": "https://api.github.com/gists/789/forks",
    "commits_url": "https://api.github.com/gists/789/commits",
    "id": "789",
    "node_id": "testnodeid",
    "git_pull_url": "https://gist.github.com/789.git",
    "git_push_url": "https://gist.github.com/789.git",
    "html_url": "https://gist.github.com/testuser/789",
    "files": {
      "upload-566631377468464325": {
        "filename": "upload-566631377468464325",
        "type": "text/plain",
        "language": null,
        "raw_url": "https://gist.githubusercontent.com/testuser/789/raw/260cc70502fbb5ba3c64c8d943ac4cd2927d9a17/upload-566631377468464325",
        "size": 704
      }
    },
    "public": false,
    "description": "upload:123:dGVzdHVwbG9hZA==:1:1",
    "comments_url": "https://api.github.com/gists/789/comments",
    "truncated": false
  }
]
'''


@pytest.fixture
def gist_c2(app_svc, contact_svc, data_svc, file_svc, obfuscator):
    services = app_svc.get_services()
    return GistContact(services)


def _mock_get(url, *, allow_redirects=True, **kwargs):
    dummy_beacon_data = {
        'architecture': 'arm64',
        'available_contacts': ['HTTP'],
        'contact': 'HTTP',
        'deadman_enabled': True,
        'exe_name': 'splunkd',
        'executors': ['proc', 'sh'],
        'group': 'red',
        'host': 'myhost',
        'host_ip_addrs': ['10.0.2.15'],
        'location': '/home/testuser/splunkd',
        'origin_link_id': '',
        'paw': 'testpaw',
        'pid': 63025,
        'platform': 'linux',
        'ppid': 4357,
        'privilege': 'User',
        'proxy_receivers': None,
        'server': 'http://0.0.0.0:8888',
        'upstream_dest': 'http://0.0.0.0:8888',
        'username': 'testuser'
    }
    if url == 'https://api.github.com/gists':
        mock_data = MOCK_GISTS
    elif url.startswith('https://gist.githubusercontent.com'):
        if url.endswith('/beacon-566631377468464325'):
            mock_data = base64.b64encode(json.dumps(dummy_beacon_data).encode('ascii'))
        elif url.endswith('/results-566631377468464325'):
            dummy_beacon_data['results'] = 'mockresult'
            mock_data = base64.b64encode(json.dumps(dummy_beacon_data).encode('ascii'))
        elif url.endswith('/upload-566631377468464325'):
            mock_data = base64.b64encode(b'testuploaddata')
    else:
        mock_data = ''

    mock_resp = mock.Mock(
        spec=aiohttp.ClientResponse,
        **{'text.return_value': mock_data}
    )
    return contextlib.nullcontext(mock_resp)


class TestContactGist:
    @mock.patch.object(uuid, 'uuid4', return_value='mockuuid')
    @mock.patch.object(FileSvc, 'read_file', return_value=('testpayload', bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])))
    @mock.patch.object(aiohttp.ClientSession, 'delete')
    @mock.patch.object(aiohttp.ClientSession, 'post')
    @mock.patch.object(aiohttp.ClientSession, 'get', side_effect=_mock_get)
    async def test_handle_beacons(self, mock_get, mock_post, mock_delete, mock_read_file, mock_uuid, gist_c2):
        dummy_agent = Agent(paw='testpaw', sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'])
        dummy_agent.set_pending_executor_removal('sh')
        dummy_agent.pending_contact = 'newcontact'
        dummy_instruction = Instruction(
            id='123',
            sleep=5,
            command='whoami',
            executor='sh',
            timeout=60,
            payloads=['testpayload'],
            uploads=[],
            deadman=False,
            delete_payload=True
        )
        with mock.patch.object(ContactService, 'handle_heartbeat', return_value=(dummy_agent, [dummy_instruction])):
            await gist_c2.handle_beacons(await gist_c2.get_beacons())
            mock_get.assert_any_call('https://api.github.com/gists')
            mock_get.assert_any_call('https://gist.githubusercontent.com/testuser/123/raw/260cc70502fbb5ba3c64c8d943ac4cd2927d9a17/beacon-566631377468464325')
            want_payload_post_body = dict(
                description='payloads-testpaw-testpayload',
                public=False,
                files=dict(testpayload=dict(content='ASNFZ4mrze8='))
            )
            want_resp = dict(
                paw='testpaw',
                sleep=5,
                watchdog=0,
                instructions=json.dumps([json.dumps(dummy_instruction.display)]),
                new_contact='newcontact',
                executor_change=dict(
                    action='remove',
                    executor='sh',
                )
            )
            want_resp_encoded = str(base64.b64encode(json.dumps(want_resp).encode('utf-8')), 'utf-8')
            want_instruction_post_body = dict(
                description='instructions-testpaw',
                public=False,
                files=dict(mockuuid=dict(content=want_resp_encoded))
            )
            mock_post.assert_any_call('https://api.github.com/gists', json=want_payload_post_body)
            mock_post.assert_any_call('https://api.github.com/gists', json=want_instruction_post_body)

    @mock.patch.object(uuid, 'uuid4', return_value='mockuuid')
    @mock.patch.object(FileSvc, 'read_file', return_value=('testpayload', bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])))
    @mock.patch.object(aiohttp.ClientSession, 'delete')
    @mock.patch.object(aiohttp.ClientSession, 'post')
    @mock.patch.object(aiohttp.ClientSession, 'get', side_effect=_mock_get)
    async def test_handle_results(self, mock_get, mock_post, mock_delete, mock_read_file, mock_uuid, gist_c2):
        dummy_agent = Agent(paw='testpaw', sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'])
        with mock.patch.object(ContactService, 'handle_heartbeat', return_value=(dummy_agent, [])):
            await gist_c2.handle_beacons(await gist_c2.get_results())
            mock_get.assert_any_call('https://api.github.com/gists')
            mock_get.assert_any_call('https://gist.githubusercontent.com/testuser/456/raw/260cc70502fbb5ba3c64c8d943ac4cd2927d9a17/results-566631377468464325')
            want_resp = dict(
                paw='testpaw',
                sleep=5,
                watchdog=0,
                instructions=json.dumps([])
            )
            want_resp_encoded = str(base64.b64encode(json.dumps(want_resp).encode('utf-8')), 'utf-8')
            want_instruction_post_body = dict(
                description='instructions-testpaw',
                public=False,
                files=dict(mockuuid=dict(content=want_resp_encoded))
            )
            mock_post.assert_called_once_with('https://api.github.com/gists', json=want_instruction_post_body)

    @mock.patch.object(FileSvc, 'save_file')
    @mock.patch.object(FileSvc, 'create_exfil_sub_directory', return_value='/tmp/mockcalderaexfil/566631377468464325')
    @mock.patch.object(logging.Logger, 'debug')
    @mock.patch.object(aiohttp.ClientSession, 'delete')
    @mock.patch.object(aiohttp.ClientSession, 'get', side_effect=_mock_get)
    async def test_handle_uploads(self, mock_get, mock_delete, mock_logger, mock_create_exfil_dir, mock_save_file, gist_c2):
        await gist_c2.handle_uploads(await gist_c2.get_uploads())
        mock_logger.assert_any_call(
            'Received uploaded file chunk 1 out of 1 for paw 566631377468464325, upload ID 123, filename testupload '
        )
        mock_logger.assert_any_call('Upload 123 complete for paw 566631377468464325, filename testupload')
        mock_logger.assert_any_call('Uploaded file /tmp/mockcalderaexfil/566631377468464325/testupload')
        mock_create_exfil_dir.assert_called_once_with(dir_name='566631377468464325')
        mock_save_file.assert_called_once_with('testupload-123', b'testuploaddata', '/tmp/mockcalderaexfil/566631377468464325')
