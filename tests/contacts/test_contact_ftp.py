import pytest
import aioftp
import json
import os
import shutil

from unittest import mock

from app.contacts import contact_ftp
from app.objects.c_agent import Agent
from app.service.contact_svc import ContactService
from app.service.file_svc import FileSvc
from app.utility.base_world import BaseWorld

beacon_profile = {'architecture': 'amd64',
                  'contact': 'ftp',
                  'paw': '8924',
                  'exe_name': 'sandcat.exe',
                  'executors': ['cmd', 'psh'],
                  'group': 'red',
                  'host': 'testhost',
                  'location': 'C:\\sandcat.exe',
                  'pid': 1234,
                  'platform': 'windows',
                  'ppid': 123,
                  'privilege': 'User',
                  'username': 'testuser'
                  }


DUMMY_EXFIL_DIR = '/tmp/testexfildir'


@pytest.fixture(scope='session')
def base_world():
    BaseWorld.clear_config()
    BaseWorld.apply_config(name='main', config={'app.contact.ftp.host': '127.0.0.1',
                                                'app.contact.ftp.port': '62221',
                                                'app.contact.ftp.pword': 'caldera',
                                                'app.contact.ftp.server.dir': 'test_dummy_ftp_dir',
                                                'app.contact.ftp.user': 'caldera_user',
                                                'plugins': ['sandcat', 'stockpile'],
                                                'crypt_salt': 'BLAH',
                                                'api_key': 'ADMIN123',
                                                'encryption_key': 'ADMIN123',
                                                'encrypt_files': False,
                                                'exfil_dir': DUMMY_EXFIL_DIR})
    BaseWorld.apply_config(name='agents', config={'sleep_max': 5,
                                                  'sleep_min': 5,
                                                  'untrusted_timer': 90,
                                                  'watchdog': 0,
                                                  'implant_name': 'splunkd',
                                                  'bootstrap_abilities': [
                                                      '43b3754c-def4-4699-a673-1d85648fda6a'
                                                  ]})
    yield BaseWorld
    BaseWorld.clear_config()
    if os.path.exists(DUMMY_EXFIL_DIR):
        shutil.rmtree(DUMMY_EXFIL_DIR)


@pytest.fixture
async def ftp_c2(app_svc, base_world, contact_svc, data_svc, file_svc, obfuscator):
    services = app_svc.get_services()
    ftp_c2 = contact_ftp.Contact(services)
    return ftp_c2


@pytest.fixture
async def ftp_c2_handler_server(ftp_c2):
    ftp_c2.set_up_server()
    await ftp_c2.server.start(host=ftp_c2.server.host, port=ftp_c2.server.port)
    yield ftp_c2.server
    await ftp_c2.server.close()
    if os.path.exists(ftp_c2.server.ftp_server_dir):
        shutil.rmtree(ftp_c2.server.ftp_server_dir)


@pytest.fixture
def ftp_dummy_agent():
    return Agent(paw=TestFtpServer.dummy_beacon_data.get('paw'), sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'])


@pytest.fixture
async def ftp_client(ftp_c2_handler_server, ftp_dummy_agent):
    client = aioftp.Client()
    await client.connect(ftp_c2_handler_server.host, port=int(ftp_c2_handler_server.port))
    await client.login(user=ftp_c2_handler_server.login, password=ftp_c2_handler_server.pword)
    await client.make_directory(ftp_dummy_agent.paw)
    await client.change_directory(ftp_dummy_agent.paw)
    yield client

    await client.quit()


class TestFtpServer:
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

    @staticmethod
    def test_server_setup(ftp_c2):
        assert ftp_c2.name == 'ftp'
        assert ftp_c2.description == 'Accept agent beacons through ftp'
        assert ftp_c2.host == '127.0.0.1'
        assert ftp_c2.port == '62221'
        assert ftp_c2.directory == 'test_dummy_ftp_dir'
        assert ftp_c2.user == 'caldera_user'
        assert ftp_c2.pword == 'caldera'
        assert ftp_c2.server is None

    @staticmethod
    def test_set_up_server(ftp_c2):
        ftp_c2.set_up_server()
        assert ftp_c2.server is not None

    @staticmethod
    async def test_my_server_setup(ftp_c2_handler_server):
        assert ftp_c2_handler_server.host == '127.0.0.1'
        assert ftp_c2_handler_server.port == '62221'
        assert ftp_c2_handler_server.login == 'caldera_user'
        assert ftp_c2_handler_server.pword == 'caldera'
        assert ftp_c2_handler_server.ftp_server_dir == os.path.join(os.getcwd(), 'test_dummy_ftp_dir')
        assert os.path.exists(ftp_c2_handler_server.ftp_server_dir)

    async def test_beacon(self, ftp_c2_handler_server, ftp_dummy_agent, ftp_client):
        beacon_file_data = bytes(json.dumps(self.dummy_beacon_data).encode('ascii'))

        ftp_dummy_agent.pending_contact = 'newcontact'
        with mock.patch.object(ContactService, 'handle_heartbeat', return_value=(ftp_dummy_agent, [])):
            async with ftp_client.upload_stream('Alive.txt') as upload_stream:
                await upload_stream.write(beacon_file_data)

        resp_path = os.path.join(ftp_c2_handler_server.ftp_server_dir, 'testpaw', 'Response.txt')
        assert os.path.exists(resp_path)
        want_response_dict = dict(
            paw='testpaw',
            sleep=5,
            watchdog=0,
            instructions='[]',
            new_contact='newcontact',
        )
        with open(resp_path, 'rb') as resp_file:
            response = resp_file.read()
        resp_dict = json.loads(response)
        assert want_response_dict == resp_dict

    async def test_download_payload(self, ftp_c2_handler_server, ftp_dummy_agent, ftp_client):
        payload_req = dict(file='testdownload', platform='linux', paw='testpaw')
        payload_req_data = bytes(json.dumps(payload_req).encode('ascii'))
        dummy_payload_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])

        with mock.patch.object(FileSvc, 'get_file', return_value=('testplugin/payloads/testdownload', dummy_payload_data, 'testdownload')):
            async with ftp_client.upload_stream('Payload.txt') as upload_stream:
                await upload_stream.write(payload_req_data)

        resp_path = os.path.join(ftp_c2_handler_server.ftp_server_dir, 'testpaw', 'testdownload')
        assert os.path.exists(resp_path)
        with open(resp_path, 'rb') as resp_file:
            assert dummy_payload_data == resp_file.read()

    async def test_upload_file(self, ftp_c2_handler_server, ftp_dummy_agent, ftp_client):
        dummy_file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])

        async with ftp_client.upload_stream('testupload') as upload_stream:
            await upload_stream.write(dummy_file_data)

        assert os.path.exists(DUMMY_EXFIL_DIR)
        upload_path = os.path.join(DUMMY_EXFIL_DIR, 'testpaw', 'testupload')
        assert os.path.exists(upload_path)
        with open(upload_path, 'rb') as upload_file:
            assert dummy_file_data == upload_file.read()
