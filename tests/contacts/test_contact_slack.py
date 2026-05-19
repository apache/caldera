import pytest
import aiohttp
import base64
import contextlib
import json
from unittest import mock

from app.objects.c_agent import Agent
from app.objects.secondclass.c_instruction import Instruction
from app.contacts.contact_slack import Contact as SlackContact
from app.service.contact_svc import ContactService
from app.service.file_svc import FileSvc

MOCK_SLACK = '''{
    "messages": [
        {
            "text": "beacon | %s",
            "ts": "dummytimestamp"
        }
    ]
}
'''


@pytest.fixture
def slack_c2(app_svc, contact_svc, data_svc, file_svc, obfuscator):
    services = app_svc.get_services()
    slack_contact = SlackContact(services)
    slack_contact.channelid = 'mockchannelid'
    yield slack_contact


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
    if url.startswith('https://slack.com/api/conversations.history?channel=mockchannelid&oldest='):
        encoded_beacon = str(base64.b64encode(json.dumps(dummy_beacon_data).encode('utf-8')), 'utf-8')
        mock_data = MOCK_SLACK % encoded_beacon
    else:
        mock_data = ''

    mock_resp = mock.Mock(
        spec=aiohttp.ClientResponse,
        **{'text.return_value': mock_data}
    )
    return contextlib.nullcontext(mock_resp)


class TestContactSlack:
    @mock.patch.object(FileSvc, 'read_file', return_value=('testpayload', bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])))
    @mock.patch.object(aiohttp.ClientSession, 'post')
    @mock.patch.object(aiohttp.ClientSession, 'get', side_effect=_mock_get)
    async def test_handle_beacons(self, mock_get, mock_post, mock_read_file, slack_c2):
        dummy_agent = Agent(paw='testpaw', sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'])
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
            await slack_c2.handle_beacons(await slack_c2.get_beacons())
            want_payload_post_body = dict(
                channels='mockchannelid',
                initial_comment='payloads-testpaw-testpayload',
                content='ASNFZ4mrze8='
            )
            want_resp = dict(
                paw='testpaw',
                sleep=5,
                watchdog=0,
                instructions=json.dumps([json.dumps(dummy_instruction.display)]),
                new_contact='newcontact'
            )
            want_resp_encoded = str(base64.b64encode(json.dumps(want_resp).encode('utf-8')), 'utf-8')
            want_instruction_post_body = dict(
                channel='mockchannelid',
                text=f'instructions-testpaw | {want_resp_encoded}'
            )
            want_delete_dict = {'channel': 'mockchannelid', 'ts': 'dummytimestamp'}
            mock_post.assert_any_call('https://slack.com/api/chat.delete', data=want_delete_dict)
            mock_post.assert_any_call('https://slack.com/api/files.upload', data=want_payload_post_body)
            mock_post.assert_any_call('https://slack.com/api/chat.postMessage', json=want_instruction_post_body)
