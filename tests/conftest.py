import asyncio
import os.path

import jinja2
import pytest
import random
import string
import uuid
import yaml
import aiohttp_apispec
import warnings

from datetime import datetime, timezone
from base64 import b64encode
from unittest import mock
from unittest.mock import AsyncMock
from aiohttp_apispec import validation_middleware
from aiohttp import web
import aiohttp_jinja2
from pathlib import Path
from app.api.v2 import make_app
from app.objects.c_obfuscator import Obfuscator
from app.objects.c_objective import Objective
from app.objects.c_planner import PlannerSchema
from app.objects.c_source import SourceSchema, Source
from app.service.app_svc import AppService
from app.service.auth_svc import AuthService
from app.service.data_svc import DataService
from app.service.contact_svc import ContactService
from app.service.event_svc import EventService
from app.service.file_svc import FileSvc
from app.service.learning_svc import LearningService
from app.service.planning_svc import PlanningService
from app.service.rest_svc import RestService
from app.service.knowledge_svc import KnowledgeService
from app.objects.c_adversary import Adversary, AdversarySchema
from app.objects.c_ability import Ability, AbilitySchema
from app.objects.c_operation import Operation, OperationSchema
from app.objects.c_plugin import Plugin
from app.objects.c_agent import Agent
from app.objects.secondclass.c_executor import Executor, ExecutorSchema
from app.objects.secondclass.c_link import Link
from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_relationship import Relationship
from app.objects.secondclass.c_rule import Rule
from app.utility.base_object import BaseObject
from app.utility.base_service import BaseService
from app.utility.base_world import BaseWorld
from app.api.v2.responses import apispec_request_validation_middleware
from app.api.rest_api import RestApi

from app import version

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(DIR, '..', 'conf')


@pytest.fixture(scope='session')
def init_base_world():
    with open(os.path.join(CONFIG_DIR, 'default.yml')) as c:
        BaseWorld.apply_config('main', yaml.load(c, Loader=yaml.FullLoader), apply_hash=True)
    BaseWorld.apply_config('agents', BaseWorld.strip_yml(os.path.join(CONFIG_DIR, 'agents.yml'))[0])
    BaseWorld.apply_config('payloads', BaseWorld.strip_yml(os.path.join(CONFIG_DIR, 'payloads.yml'))[0])


@pytest.fixture
async def app_svc():
    # async def _init_app_svc():
    #     return AppService(None)

    # def _app_svc(event_loop):
    #     return event_loop.run_until_complete(_init_app_svc())
    # return _app_svc
    return AppService(None)


@pytest.fixture(scope='class')
def data_svc():
    return DataService()


@pytest.fixture(scope='class')
def knowledge_svc():
    return KnowledgeService()


@pytest.fixture(scope='class')
def file_svc():
    return FileSvc()


@pytest.fixture
async def contact_svc():
    contact_svc = ContactService()
    yield contact_svc
    await contact_svc.deregister_contacts()


@pytest.fixture
def event_svc(contact_svc, init_base_world):
    return EventService()


@pytest.fixture
async def rest_svc():
    """
    The REST service requires the test's loop in order to be initialized in the same Thread
    as the test. This mitigates the issue where the service's calls to `asyncio.get_event_loop`
    would result in a RuntimeError indicating that there is no currentevent loop in the main
    thread.
    """
    async def _init_rest_svc():
        return RestService()

    def _rest_svc(event_loop):
        return event_loop.run_until_complete(_init_rest_svc())
    return _rest_svc
    # return RestService()


@pytest.fixture(scope='class')
def planning_svc():
    return PlanningService()


@pytest.fixture(scope='class')
def learning_svc():
    return LearningService()


@pytest.fixture(scope='class')
def services(app_svc):
    return app_svc.get_services()


@pytest.fixture(scope='class')
def mocker():
    return mock


@pytest.fixture
def adversary():
    def _generate_adversary(adversary_id=None, name=None, description=None, phases=None):
        if not adversary_id:
            adversary_id = uuid.uuid4()
        if not name:
            name = ''.join(random.choice(string.ascii_uppercase) for _ in range(10))
        if not description:
            description = "description"
        if not phases:
            phases = dict()
        return Adversary(adversary_id=adversary_id, name=name, description=description, atomic_ordering=phases)

    return _generate_adversary


@pytest.fixture
def executor():
    def _generate_executor(name='psh', platform='windows', *args, **kwargs):
        return Executor(name, platform, *args, **kwargs)

    return _generate_executor


@pytest.fixture
def ability():
    def _generate_ability(ability_id=None, *args, **kwargs):
        if not ability_id:
            ability_id = random.randint(0, 999999)
        return Ability(*args, ability_id=ability_id, **kwargs)

    return _generate_ability


@pytest.fixture
def operation():
    def _generate_operation(name, agents, adversary, *args, **kwargs):
        return Operation(*args, name=name, agents=agents, adversary=adversary, **kwargs)

    return _generate_operation


@pytest.fixture
def demo_operation(event_loop, data_svc, operation, adversary):
    tadversary = event_loop.run_until_complete(data_svc.store(adversary()))
    return operation(name='my first op', agents=[], adversary=tadversary)


@pytest.fixture
def obfuscator(event_loop, data_svc):
    event_loop.run_until_complete(data_svc.store(
        Obfuscator(name='plain-text',
                   description='Does no obfuscation to any command, instead running it in plain text',
                   module='app.obfuscators.plain_text')
        )
    )


@pytest.fixture
def agent():
    def _generate_agent(sleep_min, sleep_max, watchdog, *args, **kwargs):
        return Agent(*args, sleep_min=sleep_min, sleep_max=sleep_max, watchdog=watchdog, **kwargs)

    return _generate_agent


@pytest.fixture
def link():
    def _generate_link(command, paw, ability, executor, *args, **kwargs):
        return Link.load(dict(*args, ability=ability, executor=executor, command=command, paw=paw, **kwargs))

    return _generate_link


@pytest.fixture
def fact():
    def _generate_fact(trait, *args, **kwargs):
        return Fact(*args, trait=trait, **kwargs)

    return _generate_fact


@pytest.fixture
def rule():
    def _generate_rule(action, trait, *args, **kwargs):
        return Rule(*args, action=action, trait=trait, **kwargs)

    return _generate_rule


@pytest.fixture
def relationship():
    def _generate_relationship(source, edge, target, *args, **kwargs):
        return Relationship(*args, source=source, edge=edge, target=target, **kwargs)

    return _generate_relationship


@pytest.fixture
def demo_plugin():
    def _generate_plugin(enabled=False, gui=False, data_dir=None, access=None):
        name = ''.join(random.choice(string.ascii_lowercase) for _ in range(10))
        desc = 'this is a good description'
        address = '/plugin/%s/gui' % name if gui else None
        return Plugin(name=name, description=desc, address=address, enabled=enabled, data_dir=data_dir, access=access)

    return _generate_plugin


@pytest.fixture
def agent_profile():
    def _agent_profile(paw=None, group='red', platform='linux', executors=None, privilege='Elevated'):
        if not executors:
            executors = ['sh']
        return dict(
            server='http://127.0.0.1:8888',
            username='username',
            group=group,
            host='hostname',
            platform=platform,
            architecture='x86_64',
            location='/path/to/agent',
            pid=random.randint(2, 32768),
            ppid=random.randint(2, 32768),
            executors=executors,
            privilege=privilege,
            exe_name='agent-exe-name',
            paw=paw
        )

    return _agent_profile


@pytest.fixture
def app_config():
    return {
        'app.contact.dns.domain': 'mycaldera.caldera',
        'app.contact.dns.socket': '0.0.0.0:8853',
        'app.contact.html': '/weather',
        'app.contact.http': '0.0.0.0:8888',
        'app.contact.tcp': '0.0.0.0:7010',
        'app.contact.tunnel.ssh.socket': '0.0.0.0:8022',
        'app.contact.udp': '0.0.0.0:7013',
        'app.contact.websocket': '0.0.0.0:7012',
        'plugins': [
            'stockpile',
            'atomic'
        ],
        'host': '0.0.0.0',
        'auth.login.handler.module': 'default',
        'users': {
            'red': {
                'red': 'password-foo'
            },
            'blue': {
                'blue': 'password-bar'
            }
        }
    }


@pytest.fixture
def agent_config():
    return {
        'sleep_min': '30',
        'sleep_max': '60',
        'untrusted_timer': '90',
        'watchdog': '0',
        'implant_name': 'splunkd',
        'deadman_abilities': [
            'this-is-a-fake-ability'
        ],
        'bootstrap_abilities': [
            'this-is-another-fake-ability'
        ]
    }


@pytest.fixture
async def api_v2_client(aiohttp_client, contact_svc):
    warnings.filterwarnings(
        "ignore",
        message="Multiple schemas resolved to the name"
    )

    async def initialize():
        with open(Path(__file__).parents[1] / 'conf' / 'default.yml', 'r') as fle:
            BaseWorld.apply_config('main', yaml.safe_load(fle), apply_hash=True)
        with open(Path(__file__).parents[1] / 'conf' / 'payloads.yml', 'r') as fle:
            BaseWorld.apply_config('payloads', yaml.safe_load(fle))
        with open(Path(__file__).parents[1] / 'conf' / 'agents.yml', 'r') as fle:
            BaseWorld.apply_config('agents', yaml.safe_load(fle))

        app_svc = AppService(web.Application(client_max_size=5120 ** 2))
        _ = DataService()
        _ = RestService()
        _ = PlanningService()
        _ = KnowledgeService()
        _ = LearningService()
        auth_svc = AuthService()
        _ = FileSvc()
        _ = EventService()
        services = app_svc.get_services()
        os.chdir(str(Path(__file__).parents[1]))

        _ = await RestApi(services).enable()
        await app_svc.register_contacts()
        await auth_svc.apply(app_svc.application, auth_svc.get_config('users'))
        await auth_svc.set_login_handlers(services)

        app_svc.register_subapp('/api/v2', make_app(services))
        aiohttp_apispec.setup_aiohttp_apispec(
            app=app_svc.application,
            title='Caldera',
            version=version.get_version(),
            swagger_path='/api/docs',
            url='/api/docs/swagger.json',
            static_path='/static/swagger'
        )
        app_svc.application.middlewares.append(apispec_request_validation_middleware)
        app_svc.application.middlewares.append(validation_middleware)
        templates = ['plugins/%s/templates' % p.lower() for p in app_svc.get_config('plugins')]
        magma_dist = 'plugins/magma/dist'
        if os.path.exists(magma_dist):
            templates.append(magma_dist)
        templates.append("templates")
        aiohttp_jinja2.setup(app_svc.application, loader=jinja2.FileSystemLoader(templates))
        return app_svc

    app_svc = await initialize()
    app = app_svc.application
    yield await aiohttp_client(app)
    await app_svc._destroy_plugins()


@pytest.fixture
def api_cookies(event_loop, api_v2_client):
    async def get_cookie():
        r = await api_v2_client.post('/enter', allow_redirects=False, data=dict(username='admin', password='admin'))
        return r.cookies
    return event_loop.run_until_complete(get_cookie())


@pytest.fixture
def async_return():
    def _async_return(return_param):
        f = asyncio.Future()
        f.set_result(return_param)
        return f
    return _async_return


@pytest.fixture
def mock_time():
    return datetime(2021, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def parse_datestring():
    def _parse_datestring(datestring):
        return datetime.strptime(datestring, BaseObject.TIME_FORMAT)
    return _parse_datestring


@pytest.fixture
def test_adversary(event_loop):
    expected_adversary = {'name': 'ad-hoc',
                          'description': 'an empty adversary profile',
                          'adversary_id': 'ad-hoc',
                          'objective': '495a9828-cab1-44dd-a0ca-66e58177d8cc',
                          'tags': [],
                          'has_repeatable_abilities': False}
    test_adversary = AdversarySchema().load(expected_adversary)
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_adversary))
    return test_adversary


@pytest.fixture
def test_planner(event_loop):
    expected_planner = {'name': 'test planner',
                        'description': 'test planner',
                        'module': 'test',
                        'stopping_conditions': [],
                        'params': {},
                        'allow_repeatable_abilities': False,
                        'ignore_enforcement_modules': [],
                        'id': '123'}
    test_planner = PlannerSchema().load(expected_planner)
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_planner))
    return test_planner


@pytest.fixture
def test_source(event_loop):
    test_fact = Fact(trait='remote.host.fqdn', value='dc')
    test_source = Source(id='123', name='test', facts=[test_fact], adjustments=[])
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_source))
    return test_source


@pytest.fixture
def test_source_existing_relationships(event_loop):
    test_fact_1 = Fact(trait='test_1', value='1')
    test_fact_2 = Fact(trait='test_2', value='2')
    test_relationship = Relationship(source=test_fact_1, edge='test_edge', target=test_fact_2)
    test_source = Source(id='123', name='test', facts=[test_fact_1, test_fact_2], adjustments=[],
                         relationships=[test_relationship])
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_source))
    return test_source


@pytest.fixture
def test_operation(test_adversary, test_planner, test_source):
    expected_operation = {'name': '123',
                          'adversary': AdversarySchema().dump(test_adversary),
                          'state': 'paused',
                          'id': '123',
                          'group': 'red',
                          'autonomous': 0,
                          'planner': PlannerSchema().dump(test_planner),
                          'source': SourceSchema().dump(test_source),
                          'jitter': '2/8',
                          'visibility': 50,
                          'auto_close': False,
                          'obfuscator': 'plain-text',
                          'use_learning_parsers': False}
    return expected_operation


@pytest.fixture
def test_agent(event_loop):
    agent = Agent(paw='123', sleep_min=2, sleep_max=8, watchdog=0, executors=['sh'], platform='linux')
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(agent))
    return agent


@pytest.fixture
def test_executor(test_agent):
    return ExecutorSchema().load(dict(timeout=60, platform=test_agent.platform, name='sh', command='ls'))


@pytest.fixture
def test_ability(test_executor, event_loop):
    ability = AbilitySchema().load(dict(ability_id='123',
                                        tactic='discovery',
                                        technique_id='auto-generated',
                                        technique_name='auto-generated',
                                        name='Manual Command',
                                        description='test ability',
                                        executors=[ExecutorSchema().dump(test_executor)]))
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(ability))
    return ability


@pytest.fixture
def expected_link_output():
    return 'test_dir'


@pytest.fixture
def active_link(test_executor, test_agent, test_ability):
    return {
        'command': str(b64encode(test_executor.command.encode()), 'utf-8'),
        'paw': test_agent.paw,
        'ability': test_ability,
        'executor': test_executor,
        'score': 0,
        'jitter': 0,
        'cleanup': 0,
        'pin': 0,
        'host': test_agent.host,
        'deadman': False,
        'used': [],
        'id': '456',
        'relationships': []
    }


@pytest.fixture
def finished_link(test_executor, test_agent, test_ability):
    return {
        'command': str(b64encode(test_executor.command.encode()), 'utf-8'),
        'paw': test_agent.paw,
        'ability': test_ability,
        'executor': test_executor,
        'host': test_agent.host,
        'deadman': False,
        'used': [],
        'id': '789',
        'relationships': [],
        'status': 0,
        'output': 'test_dir'
    }


@pytest.fixture
def finished_operation_payload(test_operation):
    op_id = "00000000-0000-0000-0000-000000000000"
    test_operation['id'] = op_id
    test_operation['name'] = op_id
    test_operation['state'] = 'finished'
    return test_operation


@pytest.fixture
def setup_finished_operation(event_loop, finished_operation_payload):
    finished_operation = OperationSchema().load(finished_operation_payload)
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(finished_operation))


@pytest.fixture
def setup_operations_api_test(event_loop, api_v2_client, test_operation, test_agent, test_ability,
                              active_link, finished_link, expected_link_output):
    test_operation = OperationSchema().load(test_operation)
    test_operation.agents.append(test_agent)
    test_operation.set_start_details()
    test_link = Link.load(active_link)
    test_link.host = test_agent.host
    finished_link = Link.load(finished_link)
    finished_link.output = expected_link_output
    finished_link.host = test_agent.host
    test_operation.chain.append(test_link)
    test_operation.chain.append(finished_link)
    test_objective = Objective(id='123', name='test objective', description='test', goals=[])
    test_operation.objective = test_objective
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_operation))


@pytest.fixture
def setup_empty_operation(event_loop, test_operation):
    test_operation = OperationSchema().load(test_operation)
    test_operation.set_start_details()
    test_objective = Objective(id='123', name='test objective', description='test', goals=[])
    test_operation.objective = test_objective
    event_loop.run_until_complete(BaseService.get_service('data_svc').store(test_operation))


@pytest.fixture()
def fire_event_mock(event_svc):
    """A mock for event_svc.fire_event()

    fire_event()  wont work in tests as underlying Application
    is a stub so mock call here
    """
    event_svc.fire_event = AsyncMock(return_value=None)
