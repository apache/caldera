import importlib
import logging
import sys
from unittest import mock

import pytest
from aiohttp import web

import app.service.login_handlers as login_handlers
from app.service.auth_svc import AuthService
from app.service.login_handlers.default import DefaultLoginHandler, LDAP_PACKAGE_MISSING
from app.utility.base_world import BaseWorld

# argon2 hash of 'testpassword'
PASSWORD_HASH = '$argon2id$v=19$m=65536,t=3,p=4$87lgOXDGx/9JUHuCsxlaZw$bcJp3dQcqMiYdZOCm8LLJ8ncaEwjoS1xVcPHUGs/ajU'
LDAP_CONFIG = {'server': 'ldap://ldap.example.test', 'dn': 'dc=example,dc=test'}


@pytest.fixture
def without_ldap3(monkeypatch):
    """Make ``import ldap3`` fail, as it does when requirements-ldap.txt is not installed."""
    for name in [m for m in sys.modules if m == 'ldap3' or m.startswith('ldap3.')]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, 'ldap3', None)


@pytest.fixture
def local_config(monkeypatch):
    monkeypatch.setitem(BaseWorld._app_configuration, 'main', {})


@pytest.fixture
def ldap_config(monkeypatch):
    monkeypatch.setitem(BaseWorld._app_configuration, 'main', {'ldap': dict(LDAP_CONFIG)})


@pytest.fixture
def auth_svc():
    svc = mock.Mock()
    svc.user_map = {}
    svc.create_user = mock.AsyncMock()
    svc.handle_successful_login = mock.AsyncMock()
    return svc


def make_request(username, password):
    request = mock.Mock()
    request.post = mock.AsyncMock(return_value={'username': username, 'password': password})
    request.app.user_map = {'red': AuthService.User('red', PASSWORD_HASH, ('red', 'app'))}
    return request


def test_module_imports_without_ldap3(without_ldap3, monkeypatch):
    monkeypatch.setattr(login_handlers, 'default', login_handlers.default)
    monkeypatch.delitem(sys.modules, 'app.service.login_handlers.default')

    module = importlib.import_module('app.service.login_handlers.default')

    assert module.DefaultLoginHandler.__name__ == 'DefaultLoginHandler'


@pytest.mark.parametrize('password, accepted', [('testpassword', True), ('wrongpassword', False)])
async def test_local_login_without_ldap3(without_ldap3, local_config, auth_svc, password, accepted, caplog):
    handler = DefaultLoginHandler({'auth_svc': auth_svc})
    request = make_request('red', password)

    with pytest.raises(web.HTTPFound):
        await handler.handle_login(request)

    if accepted:
        auth_svc.handle_successful_login.assert_awaited_once_with(request, 'red')
    else:
        auth_svc.handle_successful_login.assert_not_awaited()
    assert LDAP_PACKAGE_MISSING not in caplog.text


def test_ldap_config_without_ldap3_logs_error_at_startup(without_ldap3, ldap_config, auth_svc, caplog):
    with caplog.at_level(logging.ERROR, logger='default_login_handler'):
        DefaultLoginHandler({'auth_svc': auth_svc})

    assert LDAP_PACKAGE_MISSING in caplog.text
    assert 'requirements-ldap.txt' in LDAP_PACKAGE_MISSING


async def test_ldap_login_without_ldap3_is_refused(without_ldap3, ldap_config, auth_svc, caplog):
    handler = DefaultLoginHandler({'auth_svc': auth_svc})
    caplog.clear()
    # Valid local credentials must not be used in place of the configured LDAP login.
    request = make_request('red', 'testpassword')

    with caplog.at_level(logging.ERROR, logger='default_login_handler'):
        with pytest.raises(web.HTTPFound):
            await handler.handle_login(request)

    auth_svc.handle_successful_login.assert_not_awaited()
    auth_svc.create_user.assert_not_awaited()
    assert LDAP_PACKAGE_MISSING in caplog.text


async def test_ldap_login_with_ldap3_installed(ldap_config, auth_svc, monkeypatch):
    ldap3 = pytest.importorskip('ldap3')
    connection = mock.MagicMock()
    connection.__enter__.return_value = connection
    connection.bind.return_value = True
    connection.entries = [{'objectClass': mock.Mock(value=['top', 'red'])}]
    connection_class = mock.Mock(return_value=connection)
    monkeypatch.setattr(ldap3, 'Server', mock.Mock())
    monkeypatch.setattr(ldap3, 'Connection', connection_class)
    handler = DefaultLoginHandler({'auth_svc': auth_svc})
    request = make_request('alice', 'ldappassword')

    with pytest.raises(web.HTTPFound):
        await handler.handle_login(request)

    connection_class.assert_called_once_with(mock.ANY, user='uid=alice,dc=example,dc=test', password='ldappassword')
    auth_svc.create_user.assert_awaited_once_with('alice', None, 'red')
    auth_svc.handle_successful_login.assert_awaited_once_with(request, 'alice')
