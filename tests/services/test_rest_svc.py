import aiohttp
import base64
import pytest

from unittest.mock import AsyncMock, MagicMock

from app.api.rest_api import RestApi
from app.objects.c_ability import Ability
from app.objects.c_agent import Agent
from app.objects.c_adversary import Adversary
from app.objects.c_obfuscator import Obfuscator
from app.objects.c_operation import Operation
from app.objects.c_objective import Objective
from app.objects.secondclass.c_executor import Executor
from app.objects.secondclass.c_goal import Goal
from app.objects.c_planner import Planner
from app.objects.c_source import Source
from app.utility.base_world import BaseWorld


@pytest.fixture
async def setup_rest_svc_test(data_svc):
    BaseWorld.apply_config(name='main', config={'app.contact.http': '0.0.0.0',
                                                'plugins': ['sandcat', 'stockpile'],
                                                'crypt_salt': 'BLAH',
                                                'api_key': 'ADMIN123',
                                                'encryption_key': 'ADMIN123',
                                                'exfil_dir': '/tmp'}, apply_hash=True)
    await data_svc.store(
        Ability(ability_id='123', name='testA', executors=[
            Executor(name='psh', platform='windows', command='curl #{app.contact.http}')
        ])
    )
    await data_svc.store(
        Ability(ability_id='456', name='testB', executors=[
            Executor(name='sh', platform='linux', command='whoami')
        ])
    )
    await data_svc.store(
        Ability(ability_id='789', name='testC', executors=[
            Executor(name='sh', platform='linux', command='hostname')
        ])
    )
    adversary = Adversary(adversary_id='123', name='test', description='test', atomic_ordering=[])
    await data_svc.store(adversary)

    agent = Agent(paw='123', sleep_min=2, sleep_max=8, watchdog=0, executors=['pwsh', 'psh'], platform='windows')
    await data_svc.store(agent)

    await data_svc.store(
        Objective(id='495a9828-cab1-44dd-a0ca-66e58177d8cc', name='default', goals=[Goal()])
    )

    await data_svc.store(
        Planner(planner_id='123', name='test', module='test', params=dict())
    )

    source = Source(id='123', name='test', facts=[], adjustments=[])
    await data_svc.store(source)

    await data_svc.store(
        Operation(name='test', agents=[agent], adversary=adversary, id='123', source=source)
    )

    await data_svc.store(
        Obfuscator(name='plain-text',
                   description='Does no obfuscation to any command, instead running it in plain text',
                   module='app.obfuscators.plain_text')
    )


@pytest.mark.usefixtures(
    "setup_rest_svc_test"
)
class TestRestSvc:

    def test_delete_operation(self, event_loop, rest_svc, data_svc):
        # PART A: Create an operation
        expected_operation = {'name': 'My Test Operation',
                              'adversary': {'description': 'an empty adversary profile', 'name': 'ad-hoc',
                                            'adversary_id': 'ad-hoc', 'atomic_ordering': [],
                                            'objective': '495a9828-cab1-44dd-a0ca-66e58177d8cc',
                                            'tags': [], 'has_repeatable_abilities': False, 'plugin': None}, 'state': 'finished',
                              'planner': {'name': 'test', 'description': None, 'module': 'test',
                                          'stopping_conditions': [], 'params': {}, 'allow_repeatable_abilities': False,
                                          'ignore_enforcement_modules': [], 'id': '123', 'plugin': ''}, 'jitter': '2/8',
                              'host_group': [{'trusted': True, 'architecture': 'unknown', 'watchdog': 0,
                                              'contact': 'unknown', 'username': 'unknown', 'links': [], 'sleep_max': 8,
                                              'exe_name': 'unknown', 'executors': ['pwsh', 'psh'], 'ppid': 0,
                                              'sleep_min': 2, 'server': '://None:None', 'platform': 'windows',
                                              'host': 'unknown', 'paw': '123', 'pid': 0,
                                              'display_name': 'unknown$unknown', 'group': 'red', 'location': 'unknown',
                                              'privilege': 'User', 'proxy_receivers': {}, 'proxy_chain': [],
                                              'origin_link_id': '', 'deadman_enabled': False,
                                              'available_contacts': ['unknown'], 'pending_contact': 'unknown',
                                              'host_ip_addrs': [], 'upstream_dest': '://None:None', 'status': 'alive'}],
                              'visibility': 50, 'autonomous': 1, 'chain': [], 'auto_close': False,
                              'obfuscator': 'plain-text', 'use_learning_parsers': False,
                              'group': '',
                              'source': '',
                              'objective': {'goals': [{'value': 'complete',
                                                                'operator': '==',
                                                                'target': 'exhaustion',
                                                                'achieved': False,
                                                                'count': 1048576}],
                                            'percentage': 0.0, 'description': '',
                                            'id': '495a9828-cab1-44dd-a0ca-66e58177d8cc',
                                            'name': 'default'}}
        internal_rest_svc = rest_svc(event_loop)
        operation = event_loop.run_until_complete(internal_rest_svc.create_operation(access=dict(
            access=(internal_rest_svc.Access.RED, internal_rest_svc.Access.APP)),
            data=dict(name='My Test Operation', planner='test', source='123', state='finished')))
        operation_id = operation[0]["id"]
        expected_operation['id'] = operation_id
        found_operation = event_loop.run_until_complete(data_svc.locate('operations', match=dict(id=operation_id)))[0].display
        found_operation['host_group'][0].pop('last_seen')
        found_operation.pop('start')
        found_operation["host_group"][0].pop("created")
        assert found_operation == expected_operation

        # PART B: Delete the operation (that was created in Part A) from the data service
        delete_criteria = {'id': operation_id, 'finish': None, 'base_timeout': 180, 'link_timeout': 30,
                           'name': 'My Test Operation', 'jitter': '2/8', 'state': 'finished', 'autonomous': True,
                           'last_ran': None, 'obfuscator': 'plain-text', 'auto_close': False, 'visibility': 50,
                           'chain': [], 'potential_links': []}
        event_loop.run_until_complete(internal_rest_svc.delete_operation(delete_criteria))
        assert event_loop.run_until_complete(data_svc.locate('operations', match=dict(id=operation_id))) == []

    def test_create_operation(self, event_loop, rest_svc, data_svc):
        want = {'name': 'Test',
                'adversary': {'description': 'an empty adversary profile', 'name': 'ad-hoc', 'adversary_id': 'ad-hoc',
                              'atomic_ordering': [], 'objective': '495a9828-cab1-44dd-a0ca-66e58177d8cc', 'tags': [],
                              'has_repeatable_abilities': False, 'plugin': None},
                'state': 'finished',
                'planner': {'name': 'test', 'description': None, 'module': 'test', 'stopping_conditions': [],
                            'params': {},
                            'ignore_enforcement_modules': [], 'id': '123', 'allow_repeatable_abilities': False, 'plugin': ''},
                'jitter': '2/8',
                'group': '',
                'source': '',
                'host_group': [
                    {'trusted': True, 'architecture': 'unknown', 'watchdog': 0, 'contact': 'unknown',
                     'username': 'unknown', 'links': [], 'sleep_max': 8, 'exe_name': 'unknown',
                     'executors': ['pwsh', 'psh'], 'ppid': 0, 'sleep_min': 2, 'server': '://None:None',
                     'platform': 'windows', 'host': 'unknown', 'paw': '123', 'pid': 0,
                     'display_name': 'unknown$unknown', 'group': 'red', 'location': 'unknown', 'privilege': 'User',
                     'proxy_receivers': {}, 'proxy_chain': [], 'origin_link_id': '',
                     'deadman_enabled': False, 'available_contacts': ['unknown'], 'pending_contact': 'unknown',
                     'host_ip_addrs': [], 'upstream_dest': '://None:None', 'status': 'alive'}],
                'visibility': 50, 'autonomous': 1, 'chain': [], 'auto_close': False, 'objective': '',
                'obfuscator': 'plain-text', 'use_learning_parsers': False}
        internal_rest_svc = rest_svc(event_loop)
        operation = event_loop.run_until_complete(internal_rest_svc.create_operation(access=dict(
            access=(internal_rest_svc.Access.RED, internal_rest_svc.Access.APP)),
            data=dict(name='Test', planner='test', source='123', state='finished')))
        operation[0].pop('id')
        operation[0]['host_group'][0].pop('last_seen')
        operation[0].pop('start')
        operation[0]['host_group'][0].pop('created')
        assert want == operation[0]

    def test_delete_ability(self, event_loop, rest_svc, file_svc):
        internal_rest_svc = rest_svc(event_loop)
        response = event_loop.run_until_complete(internal_rest_svc.delete_ability(data=dict(ability_id='123')))
        assert 'Delete action completed' == response

    def test_persist_objective_single_new(self, event_loop, rest_svc, file_svc):
        internal_rest_svc = rest_svc(event_loop)
        req = {
                'name': 'new objective',
                'description': 'test new objective',
                'goals': [
                    {
                        'count': 1,
                        'operator': '*',
                        'target': 'host.user.name',
                        'value': 'NA'
                    }
                ]
            }
        objs = event_loop.run_until_complete(internal_rest_svc.persist_objective({'access': [BaseWorld.Access.RED]}, req))
        # clear out subobject difference
        objs[0]['goals'][0].pop('achieved')
        assert req.items() <= objs[0].items()

    def test_persist_objective_single_existing(self, event_loop, rest_svc, file_svc):
        internal_rest_svc = rest_svc(event_loop)
        req = {
                'name': 'new objective',
                'description': 'test new objective',
                'goals': [
                    {
                        'count': 1,
                        'operator': '*',
                        'target': 'host.user.name',
                        'value': 'NA'
                    }
                ]
            }
        objs = event_loop.run_until_complete(internal_rest_svc.persist_objective({'access': [BaseWorld.Access.RED]}, req))
        # clear out subobject difference
        objs[0]['goals'][0].pop('achieved')
        assert req.items() <= objs[0].items()
        # modify
        modified_req = {'id': objs[0]['id'], 'description': 'modified objective'}
        modified_objs = event_loop.run_until_complete(internal_rest_svc.persist_objective({'access': [BaseWorld.Access.RED]}, modified_req))
        assert modified_req.items() <= modified_objs[0].items()

    def test_delete_adversary(self, event_loop, rest_svc, file_svc):
        internal_rest_svc = rest_svc(event_loop)
        data = """
---
- id: 123
  name: test
  description: test
  atomic_ordering:
        """
        with open('data/adversaries/123.yml', 'w') as f:
            f.write(data)
        response = event_loop.run_until_complete(internal_rest_svc.delete_adversary(data=dict(adversary_id='123')))
        assert 'Delete action completed' == response

    def test_delete_agent(self, event_loop, rest_svc, file_svc):
        internal_rest_svc = rest_svc(event_loop)
        response = event_loop.run_until_complete(internal_rest_svc.delete_agent(data=dict(paw='123')))
        assert 'Delete action completed' == response

    def test_get_potential_links(self, event_loop, rest_svc, planning_svc, data_svc):
        internal_rest_svc = rest_svc(event_loop)
        internal_rest_svc.add_service('planning_svc', planning_svc)
        internal_rest_svc.add_service('data_svc', data_svc)
        links = event_loop.run_until_complete(internal_rest_svc.get_potential_links('123', '123'))
        assert 1 == len(links['links'])

    def test_apply_potential_link(self, event_loop, rest_svc, planning_svc, data_svc, app_svc):
        internal_rest_svc = rest_svc(event_loop)
        internal_rest_svc.add_service('planning_svc', planning_svc)
        internal_rest_svc.add_service('data_svc', data_svc)
        internal_rest_svc.add_service('app_svc', app_svc)
        event_loop.run_until_complete(internal_rest_svc.get_potential_links('123', '123'))
        operation = event_loop.run_until_complete(data_svc.locate('operations', match=dict(id='123'))).pop()
        link = operation.potential_links[0]
        event_loop.run_until_complete(internal_rest_svc.apply_potential_link(link))
        assert 1 == len(operation.chain)

    def test_set_single_bootstrap_ability(self, event_loop, rest_svc):
        update_data = {
            'sleep_min': 5,
            'sleep_max': 5,
            'watchdog': 0,
            'untrusted': 90,
            'implant_name': 'splunkd',
            'bootstrap_abilities': '123',
            'deadman_abilities': ''
        }
        want = ['123']
        internal_rest_svc = rest_svc(event_loop)
        agent_config = event_loop.run_until_complete(internal_rest_svc.update_agent_data(update_data))
        assert agent_config.get('bootstrap_abilities') == want

    def test_set_multiple_bootstrap_ability(self, event_loop, rest_svc):
        update_data = {
            'sleep_min': 5,
            'sleep_max': 5,
            'watchdog': 0,
            'untrusted': 90,
            'implant_name': 'splunkd',
            'bootstrap_abilities': '123, 456, 789',
            'deadman_abilities': ''
        }
        want = ['123', '456', '789']
        internal_rest_svc = rest_svc(event_loop)
        agent_config = event_loop.run_until_complete(internal_rest_svc.update_agent_data(update_data))
        assert agent_config.get('bootstrap_abilities') == want

    def test_clear_bootstrap_deadman_ability(self, event_loop, rest_svc):
        update_data = {
            'sleep_min': 5,
            'sleep_max': 5,
            'watchdog': 0,
            'untrusted': 90,
            'implant_name': 'splunkd',
            'bootstrap_abilities': '',
            'deadman_abilities': '',
        }
        want = []
        internal_rest_svc = rest_svc(event_loop)
        agent_config = event_loop.run_until_complete(internal_rest_svc.update_agent_data(update_data))
        assert agent_config.get('bootstrap_abilities') == want
        assert agent_config.get('deadman_abilities') == want

    def test_set_single_deadman_ability(self, event_loop, rest_svc):
        update_data = {
            'sleep_min': 5,
            'sleep_max': 5,
            'watchdog': 0,
            'untrusted': 90,
            'implant_name': 'splunkd',
            'bootstrap_abilities': '',
            'deadman_abilities': '123'
        }
        want = ['123']
        internal_rest_svc = rest_svc(event_loop)
        agent_config = event_loop.run_until_complete(internal_rest_svc.update_agent_data(update_data))
        assert agent_config.get('deadman_abilities') == want

    def test_set_multiple_deadman_ability(self, event_loop, rest_svc):
        update_data = {
            'sleep_min': 5,
            'sleep_max': 5,
            'watchdog': 0,
            'untrusted': 90,
            'implant_name': 'splunkd',
            'bootstrap_abilities': '',
            'deadman_abilities': '123, 456, 789'
        }
        want = ['123', '456', '789']
        internal_rest_svc = rest_svc(event_loop)
        agent_config = event_loop.run_until_complete(internal_rest_svc.update_agent_data(update_data))
        assert agent_config.get('deadman_abilities') == want

    def test_list_exfil_files_with_operation_id_returns_correct_files(self, event_loop, rest_svc, data_svc):
        """Regression test for #3155: _get_operation_exfil_folders must return paw-only keys so
        list_exfil_files does not filter out every file when an operation_id is supplied.

        The operation stored in setup_rest_svc_test has id='123' and one agent with paw='123'.
        We mock file_svc.list_exfilled_files to return a dict keyed by that paw and verify that
        list_exfil_files does NOT filter it out.
        """
        agent_paw = '123'
        # Fake exfil data keyed by paw (the directory naming convention used at upload time)
        fake_files = {agent_paw: {'subdir': {'exfil.txt': '/tmp/caldera/%s/subdir/exfil.txt' % agent_paw}}}

        mock_file_svc = MagicMock()
        mock_file_svc.list_exfilled_files.return_value = fake_files

        internal_rest_svc = rest_svc(event_loop)
        internal_rest_svc.add_service('file_svc', mock_file_svc)
        internal_rest_svc.add_service('data_svc', data_svc)

        result = event_loop.run_until_complete(internal_rest_svc.list_exfil_files({'operation_id': '123'}))
        # The agent paw '123' should appear as a key — the file must not be filtered out
        assert agent_paw in result, (
            "list_exfil_files filtered out all files; paw key missing from result: %s" % result
        )
        assert 'subdir' in result[agent_paw]
        assert 'exfil.txt' in result[agent_paw]['subdir']

    def test_is_in_exfil_dir_rejects_sibling_directory(self, event_loop, rest_svc):
        """Regression test for #3155: RestApi.download_exfil_file must return HTTPNotFound
        for a path that starts with exfil_dir as a prefix but is actually a sibling directory
        (e.g. /tmp/caldera2/evil when exfil_dir is /tmp/caldera).

        This test exercises the production is_in_exfil_dir nested function inside
        RestApi.download_exfil_file rather than re-implementing the logic locally, so the
        test will fail if that production check is removed or broken.
        """
        BaseWorld.apply_config(name='main', config={
            'app.contact.http': '0.0.0.0',
            'plugins': [],
            'crypt_salt': 'BLAH',
            'api_key': 'ADMIN123',
            'encryption_key': 'ADMIN123',
            'exfil_dir': '/tmp/caldera',
        })

        # Build a minimal RestApi instance with mocked services
        mock_auth_svc = MagicMock()
        mock_auth_svc.check_permissions = AsyncMock(return_value=None)
        mock_file_svc = MagicMock()
        mock_file_svc.read_file = AsyncMock(return_value=('somefile', b'data'))

        services = {
            'data_svc': MagicMock(),
            'app_svc': MagicMock(),
            'auth_svc': mock_auth_svc,
            'file_svc': mock_file_svc,
            'rest_svc': MagicMock(),
        }
        rest_api = RestApi.__new__(RestApi)
        rest_api.log = MagicMock()
        rest_api.data_svc = services['data_svc']
        rest_api.app_svc = services['app_svc']
        rest_api.auth_svc = mock_auth_svc
        rest_api.file_svc = mock_file_svc
        rest_api.rest_svc = services['rest_svc']

        def make_request(file_path):
            """Return a mock aiohttp Request with the given file path base64-encoded."""
            encoded = base64.b64encode(file_path.encode()).decode()
            req = MagicMock()
            req.query = {'file': encoded}
            # Simulate a real aiohttp Request so check_authorization passes the type check
            req.__class__ = aiohttp.web_request.Request
            return req

        # Sibling directory /tmp/caldera2/evil must be rejected (HTTPNotFound)
        sibling_request = make_request('/tmp/caldera2/evil')
        with pytest.raises(aiohttp.web_exceptions.HTTPNotFound) as exc_info:
            sibling_response = event_loop.run_until_complete(
                rest_api.download_exfil_file(sibling_request)
            )
        assert exc_info.value.args[0] == "Not Found", (
            "Sibling directory /tmp/caldera2/evil must be rejected with 404, got %s" % sibling_response.status
        )

        # Path /tmp/calderaevil must also be rejected (starts-with prefix match must require sep)
        prefix_request = make_request('/tmp/calderaevil')
        with pytest.raises(aiohttp.web_exceptions.HTTPNotFound) as exc_info:
            prefix_response = event_loop.run_until_complete(
                rest_api.download_exfil_file(prefix_request)
            )
        assert exc_info.value.args[0] == "Not Found", (
            "Sibling directory /tmp/caldera2/evil must be rejected with 404, got %s" % prefix_response.status
        )

        # A legitimate path inside exfil_dir must be accepted (file_svc.read_file is called)
        good_request = make_request('/tmp/caldera/somefile')
        event_loop.run_until_complete(rest_api.download_exfil_file(good_request))
        mock_file_svc.read_file.assert_called_once()
