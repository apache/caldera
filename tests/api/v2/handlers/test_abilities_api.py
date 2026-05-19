import os
import pytest

from http import HTTPStatus

from app.objects.c_ability import Ability
from app.objects.secondclass.c_executor import Executor, ExecutorSchema
from app.objects.secondclass.c_requirement import Requirement, RequirementSchema
from app.utility.base_service import BaseService


@pytest.fixture
def new_ability_payload():
    test_executor_linux = Executor(name='sh', platform='linux', command='whoami')
    yield {
        'name': 'new test ability',
        'ability_id': '456',
        'tactic': 'collection',
        'technique_name': 'collection',
        'technique_id': '1',
        'executors': [ExecutorSchema().dump(test_executor_linux)],
        'access': {},
        'additional_info': {},
        'buckets': ['collection'],
        'description': '',
        'privilege': '',
        'repeatable': False,
        'requirements': [],
        'singleton': False,
        'plugin': '',
        'delete_payload': True,
    }

    # Ability cleanup
    if os.path.exists('data/abilities/collection/456.yml'):
        try:
            os.remove('data/abilities/collection/456.yml')
        except OSError:
            pass


@pytest.fixture
def updated_ability_payload(test_ability):
    ability_data = test_ability.schema.dump(test_ability)
    ability_data.update(dict(name='an updated test ability', tactic='defense-evasion', plugin=''))
    return ability_data


@pytest.fixture
def replaced_ability_payload(test_ability):
    ability_data = test_ability.schema.dump(test_ability)
    test_executor_linux = Executor(name='sh', platform='linux', command='whoami')
    test_requirement = Requirement(module='plugins.stockpile.app.requirements.paw_provenance',
                                   relationship_match=[{'source': 'host.user.name'}])
    ability_data.update(dict(name='replaced test ability', tactic='collection', technique_name='discovery',
                             technique_id='2', executors=[ExecutorSchema().dump(test_executor_linux)], plugin='',
                             requirements=[RequirementSchema().dump(test_requirement)]))
    return ability_data


@pytest.fixture
def test_ability(event_loop, api_v2_client, executor):
    executor_linux = executor(name='sh', platform='linux')
    ability = Ability(ability_id='123', name='Test Ability', executors=[executor_linux],
                      technique_name='collection', technique_id='1', description='', privilege='', tactic='discovery',
                      plugin='testplugin')
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(ability))
    yield ability

    # cleanup
    if os.path.exists('data/abilities/collection/123.yml'):
        try:
            os.remove('data/abilities/collection/123.yml')
        except OSError:
            pass


class TestAbilitiesApi:
    async def test_get_abilities(self, api_v2_client, api_cookies, test_ability):
        resp = await api_v2_client.get('/api/v2/abilities', cookies=api_cookies)
        abilities_list = await resp.json()
        assert len(abilities_list) == 1
        ability_dict = abilities_list[0]
        assert ability_dict == test_ability.schema.dump(test_ability)

    async def test_unauthorized_get_abilities(self, api_v2_client, test_ability):
        resp = await api_v2_client.get('/api/v2/abilities')
        assert resp.status == HTTPStatus.UNAUTHORIZED

    async def test_get_ability_by_id(self, api_v2_client, api_cookies, test_ability):
        resp = await api_v2_client.get('/api/v2/abilities/123', cookies=api_cookies)
        ability_dict = await resp.json()
        assert ability_dict == test_ability.schema.dump(test_ability)

    async def test_unauthorized_get_ability_by_id(self, api_v2_client, test_ability):
        resp = await api_v2_client.get('/api/v2/abilities/123')
        assert resp.status == HTTPStatus.UNAUTHORIZED

    async def test_get_nonexistent_ability_by_id(self, api_v2_client, api_cookies):
        resp = await api_v2_client.get('/api/v2/abilities/999', cookies=api_cookies)
        assert resp.status == HTTPStatus.NOT_FOUND

    async def test_create_and_delete_ability(self, api_v2_client, api_cookies, mocker, async_return, new_ability_payload):
        # Test creation
        target_ability_id = new_ability_payload.get('ability_id')
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=new_ability_payload)
        assert resp.status == HTTPStatus.OK
        ability_data = await resp.json()
        assert ability_data == new_ability_payload
        ability_exists = await BaseService.get_service('data_svc').locate('abilities', {'ability_id': '456'})
        assert ability_exists
        assert os.path.exists(f'data/abilities/collection/{target_ability_id}.yml')

        # Test deletion
        resp = await api_v2_client.delete(f'/api/v2/abilities/{target_ability_id}', cookies=api_cookies)
        assert resp.status in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)
        ability_exists = await BaseService.get_service('data_svc').locate('abilities', {'ability_id': '456'})
        assert not ability_exists
        assert not os.path.exists(f'data/abilities/collection/{target_ability_id}.yml')

    async def test_unauthorized_create_ability(self, api_v2_client, new_ability_payload):
        resp = await api_v2_client.post('/api/v2/abilities', json=new_ability_payload)
        assert resp.status == HTTPStatus.UNAUTHORIZED

    async def test_create_duplicate_ability(self, api_v2_client, api_cookies, mocker, async_return, test_ability):
        payload = test_ability.schema.dump(test_ability)
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)
        assert resp.status == HTTPStatus.BAD_REQUEST

    async def test_create_invalid_ability(self, api_v2_client, api_cookies, mocker, async_return, test_ability):
        payload = dict(name='new test ability', ability_id='123', technique_name='collection',
                       technique_id='1', executors=[])
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)
        assert resp.status == HTTPStatus.BAD_REQUEST

    async def test_update_ability(self, api_v2_client, api_cookies, test_ability, updated_ability_payload):
        resp = await api_v2_client.patch('/api/v2/abilities/123', cookies=api_cookies, json=updated_ability_payload)
        assert resp.status == HTTPStatus.OK
        ability = (await BaseService.get_service('data_svc').locate('abilities', {'ability_id': '123'}))[0]
        assert ability.schema.dump(ability) == updated_ability_payload

    async def test_unauthorized_update_ability(self, api_v2_client, test_ability, updated_ability_payload):
        resp = await api_v2_client.patch('/api/v2/abilities/123', json=updated_ability_payload)
        assert resp.status == HTTPStatus.UNAUTHORIZED

    async def test_update_nonexistent_ability(self, api_v2_client, api_cookies, updated_ability_payload):
        resp = await api_v2_client.patch('/api/v2/abilities/999', cookies=api_cookies, json=updated_ability_payload)
        assert resp.status == HTTPStatus.NOT_FOUND

    async def test_replace_ability(self, api_v2_client, api_cookies, test_ability, replaced_ability_payload):
        resp = await api_v2_client.put('/api/v2/abilities/123', cookies=api_cookies, json=replaced_ability_payload)
        assert resp.status == HTTPStatus.OK
        ability = await resp.json()
        assert ability == replaced_ability_payload

    async def test_unauthorized_replace_ability(self, api_v2_client, test_ability, replaced_ability_payload):
        resp = await api_v2_client.put('/api/v2/abilities/123', json=replaced_ability_payload)
        assert resp.status == HTTPStatus.UNAUTHORIZED

    async def test_replace_nonexistent_ability(self, api_v2_client, api_cookies, new_ability_payload):
        resp = await api_v2_client.put('/api/v2/abilities/456', cookies=api_cookies, json=new_ability_payload)
        assert resp.status == HTTPStatus.OK
        ability = await resp.json()
        assert ability == new_ability_payload

    async def test_invalid_replace_ability(self, api_v2_client, api_cookies, test_ability):
        payload = dict(name='replaced test ability', tactic='collection', technique_name='discovery', technique_id='2',
                       executors=[])
        resp = await api_v2_client.put('/api/v2/abilities/123', cookies=api_cookies, json=payload)
        assert resp.status == HTTPStatus.BAD_REQUEST
