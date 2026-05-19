import base64
import os
from http import HTTPStatus
from pathlib import Path
import tempfile
from unittest import mock

import pytest
import yaml
from aiohttp import web, FormData

from app.api.rest_api import RestApi
from app.objects.c_agent import Agent
from app.service.app_svc import AppService
from app.service.auth_svc import AuthService
from app.service.data_svc import DataService
from app.service.file_svc import FileSvc
from app.service.rest_svc import RestService
from app.service.interfaces.i_login_handler import LoginHandlerInterface
from app.utility.base_service import BaseService
from app.utility.base_world import BaseWorld


@pytest.fixture
async def aiohttp_client(aiohttp_client):

    async def initialize():
        with open(Path(__file__).parents[2] / 'conf' / 'default.yml', 'r') as fle:
            BaseWorld.apply_config('main', yaml.safe_load(fle), apply_hash=True)
        with open(Path(__file__).parents[2] / 'conf' / 'payloads.yml', 'r') as fle:
            BaseWorld.apply_config('payloads', yaml.safe_load(fle))
        with open(Path(__file__).parents[2] / 'conf' / 'agents.yml', 'r') as fle:
            BaseWorld.apply_config('agents', yaml.safe_load(fle))

        app_svc = AppService(web.Application())
        _ = DataService()
        file_svc = FileSvc()
        file_svc.encrypt_output = False
        _ = RestService()
        auth_svc = AuthService()
        services = app_svc.get_services()
        os.chdir(str(Path(__file__).parents[2]))

        await app_svc.load_plugins(['sandcat', 'ssl'])
        _ = await RestApi(services).enable()
        await auth_svc.apply(app_svc.application, auth_svc.get_config('users'))
        await auth_svc.set_login_handlers(services)
        return app_svc.application

    app = await initialize()
    yield await aiohttp_client(app)


@pytest.fixture
async def authorized_cookies(aiohttp_client):
    r = await aiohttp_client.post('/enter', allow_redirects=False, data=dict(username='admin', password='admin'))
    return r.cookies


@pytest.fixture
async def sample_agent(aiohttp_client):
    kwargs = dict(architecture='amd64', exe_name='sandcat.go', executors=['shellcode_amd64', 'sh'],
                  group='red', host='testsystem.localdomain', location='./sandcat.go', pid=125266,
                  platform='linux', ppid=124042, privilege='User', server='http://127.0.0.1:8888',
                  username='testuser', paw=None, contact='http')

    agent = await BaseService.get_service('data_svc').store(Agent(sleep_min=0, sleep_max=60, watchdog=0, **kwargs))
    yield agent

    await BaseService.get_service('data_svc').remove('agent', dict(paw=agent.paw))


async def test_home(aiohttp_client):
    if not (len(os.listdir("plugins/magma/dist/assets")) > 0):
        pytest.xfail("Magma plugin not present, expecting failure")
    resp = await aiohttp_client.get('/')
    assert resp.status == HTTPStatus.OK
    assert resp.content_type == 'text/html'


async def test_login(aiohttp_client):
    resp = await aiohttp_client.post('/enter', allow_redirects=False, data=dict(username='admin', password='admin'))
    assert resp.status == HTTPStatus.FOUND
    assert resp.headers.get('Location') == '/'
    assert 'API_SESSION' in resp.cookies


async def test_core(aiohttp_client, authorized_cookies):
    resp = await aiohttp_client.post('/api/rest', json=dict(index='agents'), cookies=authorized_cookies)
    assert resp.status == HTTPStatus.OK


async def test_read_agent(aiohttp_client, authorized_cookies, sample_agent):
    resp = await aiohttp_client.post('/api/rest', json=dict(index='agents'), cookies=authorized_cookies)
    assert resp.status == HTTPStatus.OK
    agent_list = await resp.json()
    assert len(list(filter(lambda x: x['paw'] == sample_agent.paw, agent_list)))


async def test_modify_agent(aiohttp_client, authorized_cookies, sample_agent):
    resp = await aiohttp_client.put('/api/rest', json=dict(index='agents', paw=sample_agent.paw,
                                                           sleep_min=1, sleep_max=5), cookies=authorized_cookies)
    assert resp.status == HTTPStatus.OK
    agent_dict = await resp.json()
    assert agent_dict['sleep_max'] == 5
    assert sample_agent.sleep_min == 1
    assert sample_agent.sleep_max == 5


async def test_invalid_request(aiohttp_client, authorized_cookies, sample_agent):
    resp = await aiohttp_client.put('/api/rest', json=dict(index='agents', paw=sample_agent.paw,
                                                           sleep_min='notaninteger', sleep_max=5),
                                    cookies=authorized_cookies)
    assert resp.status == HTTPStatus.BAD_REQUEST
    messages = await resp.json()
    assert messages == dict(sleep_min=['Not a valid integer.'])


async def test_command_overwrite_failure(aiohttp_client, authorized_cookies):
    resp = await aiohttp_client.post('/api/rest',
                                     cookies=authorized_cookies,
                                     json=dict(index='configuration',
                                               prop='requirements',
                                               value=dict(go=dict(command='this should not get written',
                                                                  type='installed program',
                                                                  version='1.11',),
                                                          python=dict(attr='version',
                                                                      module='sys',
                                                                      type='python_module',
                                                                      version='3.11.0'))))

    assert resp.status == HTTPStatus.BAD_REQUEST


async def test_upload_file(aiohttp_client):
    file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])
    with tempfile.TemporaryFile(mode='w+b') as tmp_file:
        tmp_file.write(file_data)
        tmp_file.flush()
        tmp_file.seek(0)

        m = mock.mock_open(read_data=file_data)
        with mock.patch('builtins.open', m):
            upload_data = FormData()
            upload_data.add_field('file', tmp_file, filename='testupload')
            resp = await aiohttp_client.post('/file/upload',
                                             headers=dict(Directory='testdir'),
                                             data=upload_data)
            assert resp.status == HTTPStatus.OK
            m.assert_called_with('data/payloads/testupload', 'wb')
            m().write.assert_called_once_with(file_data)

    BaseWorld.set_config('main', 'exfil_dir', 'dummyexfildir')
    with tempfile.TemporaryFile(mode='w+b') as tmp_file:
        tmp_file.write(file_data)
        tmp_file.flush()
        tmp_file.seek(0)

        m = mock.mock_open(read_data=file_data)
        with mock.patch.object(os, 'makedirs', return_value=None) as mock_makedirs:
            with mock.patch('builtins.open', m):
                upload_data = FormData()
                upload_data.add_field('file', tmp_file, filename='testupload')
                resp = await aiohttp_client.post('/file/upload',
                                                 headers={'X-Request-ID': 'testid'},
                                                 data=upload_data)
                assert resp.status == HTTPStatus.OK
                mock_makedirs.assert_any_call('dummyexfildir/testid')
                mock_makedirs.assert_called_with('dummyexfildir/testid')
                m.assert_called_with('dummyexfildir/testid/testupload', 'wb')
                m().write.assert_called_once_with(file_data)


async def test_download_file(aiohttp_client):
    file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])
    m = mock.mock_open(read_data=file_data)
    with mock.patch.object(FileSvc, 'find_file_path', return_value=('testplugin', 'testplugin/payloads/testdownload')) as mock_find_file_path:
        with mock.patch('builtins.open', m):
            resp = await aiohttp_client.post('/file/download',
                                             headers=dict(file='testdownload'))
            assert resp.status == HTTPStatus.OK
            m.assert_called_with('testplugin/payloads/testdownload', 'rb')
            m().read.assert_called_once_with()
            mock_find_file_path.assert_called_with('testdownload', location='payloads')
            assert await resp.content.read() == file_data
            assert resp.headers.get('CONTENT-DISPOSITION') == 'attachment; filename="testdownload"'
            assert resp.headers.get('FILENAME') == 'testdownload'

    # Test FileNotFound
    with mock.patch.object(FileSvc, 'get_file') as mock_get_file:
        mock_get_file.side_effect = FileNotFoundError()
        resp = await aiohttp_client.post('/file/download',
                                         headers=dict(file='testdownload'))
        assert resp.status == HTTPStatus.NOT_FOUND
        assert await resp.content.read() == b'File not found'

    # Test generic exception
    with mock.patch.object(FileSvc, 'get_file') as mock_get_file:
        mock_get_file.side_effect = Exception('Test exception')
        resp = await aiohttp_client.post('/file/download',
                                         headers=dict(file='testdownload'))
        assert resp.status == HTTPStatus.NOT_FOUND
        assert await resp.content.read() == b'Test exception'


async def test_download_exfil_file(aiohttp_client, authorized_cookies):
    file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])
    encoded_file_path = base64.b64encode('dummyexfildir/path/testdownload'.encode('ascii')).decode('ascii')
    bad_encoded_file_path = base64.b64encode('notexfildir/path/testdownload'.encode('ascii')).decode('ascii')

    BaseWorld.set_config('main', 'exfil_dir', 'dummyexfildir')
    m = mock.mock_open(read_data=file_data)
    with mock.patch('builtins.open', m):
        with mock.patch.object(FileSvc, 'find_file_path', return_value=(None, 'dummyexfildir/path/testdownload')):
            resp = await aiohttp_client.get(f'/file/download_exfil?file={encoded_file_path}', cookies=authorized_cookies)
            assert resp.status == HTTPStatus.OK
            m.assert_called_with('dummyexfildir/path/testdownload', 'rb')
            m().read.assert_called_once_with()
            assert await resp.content.read() == file_data
            assert resp.headers.get('CONTENT-DISPOSITION') == 'attachment; filename="testdownload"'
            assert resp.headers.get('FILENAME') == 'testdownload'

    # Test FileNotFound
    with mock.patch.object(FileSvc, 'read_file') as mock_get_file:
        mock_get_file.side_effect = FileNotFoundError()
        resp = await aiohttp_client.get(f'/file/download_exfil?file={encoded_file_path}', cookies=authorized_cookies)
        assert resp.status == HTTPStatus.NOT_FOUND
        assert await resp.content.read() == b'File not found'

    # Test generic exception
    with mock.patch.object(FileSvc, 'read_file') as mock_get_file:
        mock_get_file.side_effect = Exception('Test exception')
        resp = await aiohttp_client.get(f'/file/download_exfil?file={encoded_file_path}', cookies=authorized_cookies)
        assert resp.status == HTTPStatus.NOT_FOUND
        assert await resp.content.read() == b'Test exception'

    # Test payload not in exfil dir
    resp = await aiohttp_client.get(f'/file/download_exfil?file={bad_encoded_file_path}', cookies=authorized_cookies)
    assert resp.status == HTTPStatus.NOT_FOUND
    assert await resp.content.read() == b'File not found in exfil dir'

    # Test missing URL parameter
    resp = await aiohttp_client.get('/file/download_exfil', cookies=authorized_cookies)
    assert resp.status == HTTPStatus.BAD_REQUEST
    assert await resp.content.read() == b'A file needs to be specified for download'


async def test_custom_rejecting_login_handler(aiohttp_client):
    class RejectAllLoginHandler(LoginHandlerInterface):
        def __init__(self, services):
            super().__init__(services, 'Reject All Login Handler')

        async def handle_login(self, request, **kwargs):
            # Always reject login and return 401
            raise web.HTTPUnauthorized(text='Automatic rejection')

        async def handle_login_redirect(self, request, **kwargs):
            # Always reject and return 401
            raise web.HTTPUnauthorized(text='Automatic rejection')

    login_handler = RejectAllLoginHandler(BaseService.get_services())
    await BaseService.get_service('auth_svc').set_login_handlers(
        BaseService.get_services(),
        login_handler
    )

    resp = await aiohttp_client.post('/enter', allow_redirects=False, data=dict(username='admin', password='admin'))
    assert resp.status == HTTPStatus.UNAUTHORIZED
    assert await resp.text() == 'Automatic rejection'

    resp = await aiohttp_client.get('/api/v2', allow_redirects=False)
    assert resp.status == HTTPStatus.UNAUTHORIZED
    assert await resp.text() == 'Automatic rejection'


async def test_custom_accepting_login_handler(aiohttp_client):
    class AcceptAllLoginHandler(LoginHandlerInterface):
        def __init__(self, services):
            super().__init__(services, 'Accept All Login Handler')

        async def handle_login(self, request, **kwargs):
            # Always accept login
            data = await request.post()
            username = data.get('username', 'default username')
            auth_svc = self.services.get('auth_svc')
            if not auth_svc:
                raise Exception('Auth service not available.')
            await auth_svc.handle_successful_login(request, username)

        async def handle_login_redirect(self, request, **kwargs):
            await self.handle_login(request, **kwargs)

    login_handler = AcceptAllLoginHandler(BaseService.get_services())
    await BaseService.get_service('auth_svc').set_login_handlers(
        BaseService.get_services(),
        login_handler
    )
    resp = await aiohttp_client.post('/enter', allow_redirects=False, data=dict(username='invalid', password='invalid'))
    assert resp.status == HTTPStatus.FOUND
    assert resp.headers.get('Location') == '/'
    assert 'API_SESSION' in resp.cookies
