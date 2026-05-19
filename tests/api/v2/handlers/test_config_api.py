import pytest

from unittest import mock

from app.api.v2.managers.config_api_manager import ConfigApiManager, SENSITIVE_CONFIG_PROPS
from app.utility.base_world import BaseWorld


@pytest.fixture
def base_world(app_config, agent_config):
    BaseWorld.clear_config()
    BaseWorld.apply_config('main', app_config)
    BaseWorld.apply_config('agents', agent_config)

    yield BaseWorld

    BaseWorld.clear_config()


@pytest.fixture
def filter_config():
    def _filter_config(config):
        to_filter = config
        for sensitive_prop in SENSITIVE_CONFIG_PROPS:
            to_filter.pop(sensitive_prop, None)
        return to_filter
    return _filter_config


class TestConfigApi:
    async def test_get_config_with_name(self, api_v2_client, api_cookies, base_world, filter_config, app_config, agent_config):
        resp = await api_v2_client.get('/api/v2/config/main', cookies=api_cookies)
        config_json = await resp.json()
        want = filter_config(app_config)
        assert config_json == want

        resp = await api_v2_client.get('/api/v2/config/agents', cookies=api_cookies)
        config_json = await resp.json()
        assert config_json == agent_config

        # Test nonexistent config
        resp = await api_v2_client.get('/api/v2/config/doesnotexist', cookies=api_cookies)
        resp_dict = await resp.json()
        want = dict(error='Config not found: doesnotexist')
        assert resp_dict == want

    async def test_get_update_main_config(self, api_v2_client, api_cookies, base_world, filter_config, app_config):
        data = dict(prop='app.contact.html', value='/newhtmlcontact')
        resp = await api_v2_client.patch('/api/v2/config/main', json=data, cookies=api_cookies)
        config_json = await resp.json()
        want = filter_config(app_config)
        want['app.contact.html'] = '/newhtmlcontact'
        assert config_json == want

        # Test sensitive field
        data = dict(prop='host', value='127.0.0.3')
        resp = await api_v2_client.patch('/api/v2/config/main', json=data, cookies=api_cookies)
        resp_dict = await resp.json()
        want = dict(
            error='Update not allowed',
            details=dict(property='host')
        )
        assert resp_dict == want

    async def test_get_update_agents_config(self, api_v2_client, api_cookies, base_world, agent_config):
        data = dict(watchdog='1', implant_name='newname', bootstrap_abilities=['abil1', '', 'DNE', 'abil2'])
        with mock.patch.object(ConfigApiManager, '_get_loaded_ability_ids', return_value={'abil1', 'abil2'}):
            resp = await api_v2_client.patch('/api/v2/config/agents', json=data, cookies=api_cookies)
            config_json = await resp.json()
            want = {
                'sleep_min': 30,
                'sleep_max': 60,
                'untrusted_timer': 90,
                'watchdog': 1,
                'implant_name': 'newname',
                'deadman_abilities': [
                    'this-is-a-fake-ability'
                ],
                'bootstrap_abilities': [
                    'abil1', 'abil2'
                ]
            }
            assert config_json == want
