import copy
import os
import pytest

from http import HTTPStatus

from app.utility.base_service import BaseService


def ability_file_cleanup(tactic, ability_id):
    file_path = f'data/abilities/{tactic}/{ability_id}.yml'
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass


def basic_platform_ability(identifier_key, identifier, name, description, tactic):
    return {
        identifier_key: identifier,
        'name': name,
        'description': description,
        'tactic': tactic,
        'technique': {
            'attack_id': 'T1083',
            'name': 'File and Directory Discovery'
        },
        'platforms': {
            'darwin': {
                'sh': {
                    'command': 'ls #{host.system.path}'
                }
            },
            'linux': {
                'sh': {
                    'command': 'ls #{host.system.path}'
                }
            },
            'windows': {
                'psh': {
                    'command': 'dir #{host.system.path}'
                }
            }
        },
        'requirements': [
            {
                'plugins.stockpile.app.requirements.paw_provenance': [
                    {
                        'source': 'host.system.path'
                    }
                ]
            }
        ]
    }


def cleanup_payload_ability(payload):
    ability_id = payload.get('id') or payload.get('ability_id')
    tactic = payload.get('tactic')
    if ability_id and tactic:
        ability_file_cleanup(tactic, ability_id)


@pytest.fixture
def valid_ability_payload():
    yield basic_platform_ability(
        'id',
        'upload-test-001',
        'Uploaded Test Ability',
        'An ability uploaded via YAML file',
        'discovery'
    )

    ability_file_cleanup('discovery', 'upload-test-001')


@pytest.fixture
def valid_ability_payload_with_ability_id():
    yield basic_platform_ability(
        'ability_id',
        'upload-test-002',
        'Uploaded Test Ability 2',
        'An ability using ability_id key',
        'collection'
    )

    ability_file_cleanup('collection', 'upload-test-002')


@pytest.fixture
def new_executors_ability_payload():
    ability_id = 'upload-test-new-executors'
    tactic = 'discovery'
    ability_file_cleanup(tactic, ability_id)
    yield {
        'id': ability_id,
        'repeatable': False,
        'name': 'New executors ability',
        'additional_info': {
            'cleanup': ''
        },
        'technique_name': 'File and Directory Discovery',
        'executors': [
            {
                'name': 'sh',
                'additional_info': {},
                'variations': [],
                'platform': 'linux',
                'command': 'ls',
                'code': None,
                'language': None,
                'payloads': [],
                'timeout': 60,
                'parsers': [],
                'cleanup': [],
                'uploads': [],
                'build_target': None
            },
            {
                'name': 'psh',
                'additional_info': {},
                'variations': [],
                'platform': 'windows',
                'command': 'dir',
                'code': None,
                'language': None,
                'payloads': [],
                'timeout': 60,
                'parsers': [],
                'cleanup': [],
                'uploads': [],
                'build_target': None
            }
        ],
        'buckets': [],
        'technique_id': 'T1083',
        'delete_payload': True,
        'tactic': tactic,
        'description': 'Simple new-style ability payload.',
        'singleton': False,
        'plugin': '',
        'requirements': [],
        'privilege': '',
        'access': {}
    }
    ability_file_cleanup(tactic, ability_id)


class TestAbilityUploadApi:

    async def test_create_ability_from_old_platforms_yaml_style_payload(self, api_v2_client, api_cookies,
                                                                        valid_ability_payload):
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=valid_ability_payload)

        assert resp.status == HTTPStatus.OK
        result = await resp.json()
        assert result['ability_id'] == 'upload-test-001'
        assert result['name'] == 'Uploaded Test Ability'
        assert result['tactic'] == 'discovery'
        assert result['technique_id'] == 'T1083'
        assert result['technique_name'] == 'File and Directory Discovery'
        assert [
            (executor['platform'], executor['name'], executor['command'])
            for executor in result['executors']
        ] == [
            ('darwin', 'sh', 'ls #{host.system.path}'),
            ('linux', 'sh', 'ls #{host.system.path}'),
            ('windows', 'psh', 'dir #{host.system.path}')
        ]
        assert os.path.exists('data/abilities/discovery/upload-test-001.yml')

        ability = (await BaseService.get_service('data_svc').locate(
            'abilities', {'ability_id': 'upload-test-001'}
        ))[0]
        assert ability.display == result
        assert ability.requirements[0].module == 'plugins.stockpile.app.requirements.paw_provenance'

    async def test_create_ability_from_yaml_style_payload_with_ability_id(
            self, api_v2_client, api_cookies, valid_ability_payload_with_ability_id
    ):
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies,
                                        json=valid_ability_payload_with_ability_id)

        assert resp.status == HTTPStatus.OK
        result = await resp.json()
        assert result['ability_id'] == 'upload-test-002'
        assert result['name'] == 'Uploaded Test Ability 2'
        assert result['tactic'] == 'collection'
        assert os.path.exists('data/abilities/collection/upload-test-002.yml')

    async def test_create_ability_from_new_executors_yaml_style(self, api_v2_client, api_cookies,
                                                                new_executors_ability_payload):
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies,
                                        json=new_executors_ability_payload)

        assert resp.status == HTTPStatus.OK
        ability_data = await resp.json()
        assert ability_data['ability_id'] == new_executors_ability_payload['id']
        assert ability_data['technique_id'] == new_executors_ability_payload['technique_id']
        assert ability_data['technique_name'] == new_executors_ability_payload['technique_name']
        assert [
            (executor['platform'], executor['name'], executor['command'])
            for executor in ability_data['executors']
        ] == [('linux', 'sh', 'ls'), ('windows', 'psh', 'dir')]

        stored_ability = (await BaseService.get_service('data_svc').locate(
            'abilities', {'ability_id': new_executors_ability_payload['id']}
        ))[0]
        assert stored_ability.display == ability_data

    async def test_create_ability_sanitizes_upload_id_before_save(
            self, api_v2_client, api_cookies, new_executors_ability_payload
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = '../upload test/sanitized-id!'
        expected_id = 'uploadtestsanitized-id'

        try:
            resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

            assert resp.status == HTTPStatus.OK
            ability_data = await resp.json()
            assert ability_data['ability_id'] == expected_id
            assert os.path.exists(f'data/abilities/{payload["tactic"]}/{expected_id}.yml')
        finally:
            ability_file_cleanup(payload['tactic'], expected_id)

    async def test_create_ability_without_id_generates_id(
            self, api_v2_client, api_cookies, new_executors_ability_payload
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload.pop('id')
        ability_id = None

        try:
            resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

            assert resp.status == HTTPStatus.OK
            ability_data = await resp.json()
            ability_id = ability_data['ability_id']
            assert ability_id
            assert os.path.exists(f'data/abilities/{payload["tactic"]}/{ability_id}.yml')
        finally:
            if ability_id:
                ability_file_cleanup(payload['tactic'], ability_id)

    async def test_create_ability_accepts_safe_payload_and_plugin_style_executor_names(
            self, api_v2_client, api_cookies, new_executors_ability_payload
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = 'upload-test-plugin-executor-labels'
        payload['executors'][0]['name'] = 'plugin.exec-1'
        payload['executors'][0]['payloads'] = [
            'safe-payload.ps1',
            '766be199-7316-4b26-b3db-e272aaf7e0d4'
        ]

        try:
            resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

            assert resp.status == HTTPStatus.OK
            ability_data = await resp.json()
            assert ability_data['executors'][0]['name'] == 'plugin.exec-1'
            assert ability_data['executors'][0]['payloads'] == payload['executors'][0]['payloads']
        finally:
            cleanup_payload_ability(payload)

    @pytest.mark.parametrize(
        ('payload_name', 'suffix'),
        [
            ('../evil.ps1', 'parent'),
            ('payloads/evil.ps1', 'nested'),
            ('/tmp/evil.ps1', 'absolute'),
            ('evil\x00.ps1', 'null-byte'),
            ('evil;rm.ps1', 'unsafe'),
        ]
    )
    async def test_create_ability_rejects_unsafe_payload_paths(
            self, api_v2_client, api_cookies, new_executors_ability_payload, payload_name, suffix
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = f'upload-test-invalid-payload-{suffix}'
        payload['executors'][0]['payloads'] = [payload_name]

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.BAD_REQUEST
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    @pytest.mark.parametrize(
        ('executors', 'suffix'),
        [
            (['sh'], 'non-dict'),
            ([{'platform': 'linux', 'command': 'ls'}], 'missing-name'),
            ([{'name': 'sh', 'command': 'ls'}], 'missing-platform'),
            ([{'name': 'sh', 'platform': 'linux', 'command': 'ls', 'payloads': 'payload.ps1'}], 'bad-payloads'),
        ]
    )
    async def test_create_ability_rejects_schema_invalid_new_style_executors(
            self, api_v2_client, api_cookies, new_executors_ability_payload, executors, suffix
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = f'upload-test-schema-invalid-executor-{suffix}'
        payload['executors'] = executors

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    @pytest.mark.parametrize(
        ('executors', 'suffix'),
        [
            ([{'name': 'sh/evil', 'platform': 'linux', 'command': 'ls'}], 'unsafe-name'),
            ([{'name': 'sh', 'platform': 'lin ux', 'command': 'ls'}], 'unsafe-platform'),
        ]
    )
    async def test_create_ability_rejects_policy_invalid_new_style_executors(
            self, api_v2_client, api_cookies, new_executors_ability_payload, executors, suffix
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = f'upload-test-policy-invalid-executor-{suffix}'
        payload['executors'] = executors

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.BAD_REQUEST
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    @pytest.mark.parametrize(
        ('platforms', 'suffix'),
        [
            ({'linux': ['sh']}, 'platform-not-dict'),
            ({'linux': {'sh': 'ls'}}, 'executor-not-dict'),
        ]
    )
    async def test_create_ability_rejects_schema_invalid_legacy_platform_executors(
            self, api_v2_client, api_cookies, valid_ability_payload, platforms, suffix
    ):
        payload = copy.deepcopy(valid_ability_payload)
        payload['id'] = f'upload-test-schema-invalid-platform-{suffix}'
        payload['platforms'] = platforms

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    @pytest.mark.parametrize(
        ('platforms', 'suffix'),
        [
            ({'lin/ux': {'sh': {'command': 'ls'}}}, 'unsafe-platform'),
            ({'linux': {'sh/evil': {'command': 'ls'}}}, 'unsafe-executor'),
        ]
    )
    async def test_create_ability_rejects_policy_invalid_legacy_platform_executors(
            self, api_v2_client, api_cookies, valid_ability_payload, platforms, suffix
    ):
        payload = copy.deepcopy(valid_ability_payload)
        payload['id'] = f'upload-test-policy-invalid-platform-{suffix}'
        payload['platforms'] = platforms

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.BAD_REQUEST
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    @pytest.mark.parametrize(
        ('privilege', 'suffix'),
        [
            (None, 'none'),
            ('', 'empty'),
            ('User', 'user'),
            ('Elevated', 'elevated'),
        ]
    )
    async def test_create_ability_accepts_valid_privileges(
            self, api_v2_client, api_cookies, new_executors_ability_payload, privilege, suffix
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = f'upload-test-valid-privilege-{suffix}'
        payload['privilege'] = privilege

        try:
            resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

            assert resp.status == HTTPStatus.OK
        finally:
            cleanup_payload_ability(payload)

    @pytest.mark.parametrize(
        ('privilege', 'suffix'),
        [
            ('Admin', 'admin'),
            ('root', 'root'),
            ('elevated', 'lowercase-elevated'),
        ]
    )
    async def test_create_ability_rejects_policy_invalid_privileges(
            self, api_v2_client, api_cookies, new_executors_ability_payload, privilege, suffix
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = f'upload-test-invalid-privilege-{suffix}'
        payload['privilege'] = privilege

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.BAD_REQUEST
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    async def test_create_ability_rejects_schema_invalid_privilege_type(
            self, api_v2_client, api_cookies, new_executors_ability_payload
    ):
        payload = copy.deepcopy(new_executors_ability_payload)
        payload['id'] = 'upload-test-schema-invalid-privilege-non-string'
        payload['privilege'] = 7

        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies, json=payload)

        assert resp.status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert not os.path.exists(f'data/abilities/{payload["tactic"]}/{payload["id"]}.yml')

    async def test_create_ability_from_yaml_style_payload_missing_required_fields(self, api_v2_client, api_cookies):
        resp = await api_v2_client.post('/api/v2/abilities', cookies=api_cookies,
                                        json={'description': 'no name, tactic, or executor configuration'})

        assert resp.status == HTTPStatus.BAD_REQUEST

    async def test_unauthorized_create_ability_from_yaml_style_payload(self, api_v2_client, valid_ability_payload):
        resp = await api_v2_client.post('/api/v2/abilities', json=valid_ability_payload)

        assert resp.status == HTTPStatus.UNAUTHORIZED
