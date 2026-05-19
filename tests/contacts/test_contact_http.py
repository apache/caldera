import pytest
import base64
import json
from http import HTTPStatus
from unittest import mock

from app.objects.c_agent import Agent
from app.objects.secondclass.c_instruction import Instruction
from app.contacts.contact_http import Contact as HTTPContact
from app.service.contact_svc import ContactService
from app.utility.base_world import BaseWorld


@pytest.fixture(scope='session')
def http_contact_base_world():
    BaseWorld.apply_config(name='main', config={'app.contact.http': 'http://0.0.0.0:8888',
                                                'host': '0.0.0.0',
                                                'port': '8888',
                                                'crypt_salt': 'BLAH',
                                                'api_key': 'ADMIN123',
                                                'encryption_key': 'ADMIN123',
                                                'exfil_dir': '/tmp'})
    BaseWorld.apply_config(name='agents', config={'sleep_max': 5,
                                                  'sleep_min': 5,
                                                  'untrusted_timer': 90,
                                                  'watchdog': 0,
                                                  'implant_name': 'splunkd',
                                                  'bootstrap_abilities': [
                                                      '43b3754c-def4-4699-a673-1d85648fda6a'
                                                  ]})


@pytest.fixture
def http_c2(app_svc, contact_svc, data_svc, file_svc, obfuscator):
    services = app_svc.get_services()
    http_c2 = HTTPContact(services)
    return http_c2


@pytest.fixture
def encode_beacon_dict():
    def _encode_beacon_dict(input):
        return bytes(base64.b64encode(json.dumps(input).encode('ascii')))
    return _encode_beacon_dict


class _MockRequest():
    def __init__(self, data):
        self._data = data

    async def read(self):
        return self._data


@pytest.mark.usefixtures(
    'http_contact_base_world'
)
class TestContactHTTP:
    async def test_handler_beacon(self, http_c2, encode_beacon_dict):
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
        encoded = encode_beacon_dict(dummy_beacon_data)
        dummy_agent = Agent(paw=dummy_beacon_data.get('paw'), sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'])
        dummy_agent.set_pending_executor_removal('sh')
        dummy_agent.pending_contact = 'newcontact'
        dummy_instruction = Instruction(
            id='123',
            sleep=5,
            command='whoami',
            executor='sh',
            timeout=60,
            payloads=[],
            uploads=[],
            deadman=False,
            delete_payload=True
        )
        mock_request = _MockRequest(encoded)
        with mock.patch.object(ContactService, 'handle_heartbeat', return_value=(dummy_agent, [dummy_instruction])):
            want_response_dict = dict(
                paw='testpaw',
                sleep=5,
                watchdog=0,
                instructions=[dict(
                    id='123',
                    sleep=5,
                    command='whoami',
                    executor='sh',
                    timeout=60,
                    payloads=[],
                    uploads=[],
                    deadman=False,
                    delete_payload=True
                )],
                new_contact='newcontact',
                executor_change=dict(
                    action='remove',
                    executor='sh',
                )
            )
            resp = await http_c2._beacon(mock_request)
            assert resp.status == HTTPStatus.OK
            decoded = base64.b64decode(resp.text)
            resp_dict = json.loads(decoded)
            resp_dict['instructions'] = [json.loads(a) for a in json.loads(resp_dict['instructions'])]
            assert resp_dict == want_response_dict
