import base64
import json
import os
from base64 import b64encode
from unittest import mock
from unittest.mock import MagicMock

import pytest

from app.objects.c_operation import Operation
from app.objects.secondclass.c_link import Link
from app.service.interfaces.i_event_svc import EventServiceInterface
from app.service.interfaces.i_planning_svc import PlanningServiceInterface
from app.utility.base_service import BaseService
from app.objects.c_source import Source
from app.objects.c_planner import Planner
from app.objects.c_objective import Objective
from app.objects.secondclass.c_result import Result
from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_relationship import Relationship
from app.utility.base_object import BaseObject
from app.utility.base_world import BaseWorld

LINK1_DECIDE_TIME = MOCK_LINK_FINISH_TIME = '2021-01-01T08:00:00Z'
LINK1_COLLECT_TIME = '2021-01-01T08:01:00Z'
LINK1_FINISH_TIME = '2021-01-01T08:02:00Z'

LINK2_DECIDE_TIME = OP_START_TIME = '2021-01-01T09:00:00Z'
LINK2_COLLECT_TIME = '2021-01-01T09:01:00Z'
LINK2_FINISH_TIME = '2021-01-01T09:02:00Z'


@pytest.fixture
def operation_agent(agent):
    return agent(sleep_min=30, sleep_max=60, watchdog=0, platform='windows', host='WORKSTATION',
                 username='testagent', architecture='amd64', group='red', location=r'C:\Users\Public\test.exe',
                 pid=1234, ppid=123, executors=['psh'], privilege='User', exe_name='test.exe', contact='unknown',
                 paw='testpaw')


@pytest.fixture
def untrusted_operation_agent(operation_agent):
    agent = operation_agent
    agent.trusted = False
    return agent


@pytest.fixture
def op_agent_creation_time(operation_agent):
    return operation_agent.created.strftime(BaseObject.TIME_FORMAT)


@pytest.fixture
def operation_adversary(adversary):
    return adversary(adversary_id='123', name='test adversary', description='test adversary desc')


@pytest.fixture
def operation_link():
    def _generate_link(command, plaintext_command, paw, ability, executor, pid=0, decide=None, collect=None, finish=None, **kwargs):
        generated_link = Link(command, plaintext_command, paw, ability, executor, **kwargs)
        generated_link.pid = pid
        if decide:
            generated_link.decide = decide
        if collect:
            generated_link.collect = collect
        if finish:
            generated_link.finish = finish
        return generated_link
    return _generate_link


@pytest.fixture
def encoded_command():
    def _encode_command(command_str):
        return b64encode(command_str.encode('utf-8')).decode()
    return _encode_command


@pytest.fixture
def setup_op_config():
    BaseWorld.apply_config(name='main', config={'exfil_dir': '/tmp/caldera',
                                                'reports_dir': '/tmp'})


@pytest.fixture
def op_for_event_logs(operation_agent, operation_adversary, executor, ability, operation_link, encoded_command,
                      parse_datestring):
    op = Operation(name='test', agents=[operation_agent], adversary=operation_adversary)
    op.set_start_details()
    command_1 = 'whoami'
    command_2 = 'hostname'
    executor_1 = executor(name='psh', platform='windows', command=command_1)
    executor_2 = executor(name='psh', platform='windows', command=command_2)
    ability_1 = ability(ability_id='123', tactic='test tactic', technique_id='T0000', technique_name='test technique',
                        name='test ability', description='test ability desc', executors=[executor_1])
    ability_2 = ability(ability_id='456', tactic='test tactic', technique_id='T0000', technique_name='test technique',
                        name='test ability 2', description='test ability 2 desc', executors=[executor_2])
    link_1 = operation_link(ability=ability_1, paw=operation_agent.paw, executor=executor_1,
                            command=encoded_command(command_1), plaintext_command=encoded_command(command_1), status=0, host=operation_agent.host, pid=789,
                            decide=parse_datestring(LINK1_DECIDE_TIME),
                            collect=parse_datestring(LINK1_COLLECT_TIME),
                            finish=LINK1_FINISH_TIME)
    link_2 = operation_link(ability=ability_2, paw=operation_agent.paw, executor=executor_2,
                            command=encoded_command(command_2), plaintext_command=encoded_command(command_2), status=0, host=operation_agent.host, pid=7890,
                            decide=parse_datestring(LINK2_DECIDE_TIME),
                            collect=parse_datestring(LINK2_COLLECT_TIME),
                            finish=LINK2_FINISH_TIME)
    discarded_link = operation_link(ability=ability_2, paw=operation_agent.paw, executor=executor_2,
                                    command=encoded_command(command_2), plaintext_command=encoded_command(command_2), status=-2, host=operation_agent.host, pid=7891,
                                    decide=parse_datestring('2021-01-01T10:00:00Z'))
    op.chain = [link_1, link_2, discarded_link]
    return op


@pytest.fixture
def event_log_op_start_time(op_for_event_logs):
    return op_for_event_logs.start.strftime(BaseObject.TIME_FORMAT)


@pytest.fixture
def fake_event_svc(event_loop):
    class FakeEventService(BaseService, EventServiceInterface):
        def __init__(self):
            self.fired = {}

        def reset(self):
            self.fired = {}

        async def observe_event(self, callback, exchange=None, queue=None):
            pass

        async def fire_event(self, exchange=None, queue=None, timestamp=True, **callback_kwargs):
            self.fired[exchange, queue] = callback_kwargs

    service = FakeEventService()
    service.add_service('event_svc', service)

    yield service

    BaseService.remove_service('event_svc')


@pytest.fixture
def fake_planning_svc(event_loop, make_test_link, test_agent):
    class FakePlanningService(BaseService, PlanningServiceInterface):
        def __init__(self):
            self.fired = {}

        async def get_cleanup_links(self, operation, agent):
            cleanup_link = make_test_link(135, link_paw=test_agent.paw, link_cleanup=1, link_status=0)
            return [cleanup_link]

        def get_links(self, operation, buckets, agent, trim):
            pass

        def generate_and_trim_links(self, agent, operation, abilities, trim):
            pass

        def sort_links(self, links):
            pass

    service = FakePlanningService()
    service.add_service('planning_svc', service)

    yield service

    BaseService.remove_service('planning_svc')


@pytest.fixture
def test_ability(ability, executor):
    return ability(ability_id='123', executors=[executor(name='psh', platform='windows')])


@pytest.fixture
def make_test_link(test_ability):
    def _make_link(link_id, link_paw='123456', link_status=-3, link_cleanup=0):
        return Link(command='', paw=link_paw, ability=test_ability, id=link_id, executor=next(test_ability.executors),
                    status=link_status, cleanup=link_cleanup)
    return _make_link


@pytest.fixture
def make_test_result():
    def _make_result(link_id):
        result = dict(
            id=link_id,
            output=str(base64.b64encode('10.10.10.10'.encode('utf-8')).decode('utf-8')),
            pid=0,
            status=0
        )
        return Result(**result)
    return _make_result


@pytest.fixture
def op_with_learning_parser(ability, adversary):
    op = Operation(id='12345', name='testA', agents=[], adversary=adversary, use_learning_parsers=True)
    return op


@pytest.fixture
def op_without_learning_parser(ability, adversary):
    op = Operation(id='54321', name='testB', agents=[], adversary=adversary, use_learning_parsers=False)
    return op


@pytest.fixture
def custom_agent(test_agent, test_executor):
    def _make_agent(platform='windows', trusted=True, executor_name='psh'):
        test_executor.name = executor_name
        test_agent.platform = platform
        test_agent.executors = [test_executor.name]
        test_agent.trusted = trusted
        return test_agent
    return _make_agent


@pytest.fixture
def op_with_learning_and_seeded(ability, adversary, operation_agent, parse_datestring):
    sc = Source(id='3124', name='test', facts=[Fact(trait='domain.user.name', value='bob'), Fact(trait='domain.user.name', value='jane', score=0)])
    op = Operation(id='6789', name='testC', agents=[], adversary=adversary, source=sc, use_learning_parsers=True)
    # patch operation to make it 'realistic'
    op.start = parse_datestring(OP_START_TIME)
    op.adversary = op.adversary()
    op.planner = Planner(planner_id='12345', name='test_planner',
                                                  module='not.an.actual.planner', params=None)
    op.objective = Objective(id='6428', name='not_an_objective')
    t_operation_agent = operation_agent
    t_operation_agent.paw = '123456'
    op.agents = [t_operation_agent]
    return op


class TestOperation:
    def test_ran_ability_id(self, ability, adversary):
        op = Operation(name='test', agents=[], adversary=adversary)
        mock_link = MagicMock(spec=Link, ability=ability(ability_id='123'), finish=MOCK_LINK_FINISH_TIME)
        op.chain = [mock_link]
        assert op.ran_ability_id('123')

    def test_event_logs(self, event_loop, op_for_event_logs, operation_agent, file_svc, data_svc, event_log_op_start_time,
                        op_agent_creation_time, fire_event_mock):
        event_loop.run_until_complete(data_svc.remove('agents', match=dict(unique=operation_agent.unique)))
        event_loop.run_until_complete(data_svc.store(operation_agent))
        want_agent_metadata = dict(
            paw='testpaw',
            group='red',
            architecture='amd64',
            username='testagent',
            location=r'C:\Users\Public\test.exe',
            pid=1234,
            ppid=123,
            privilege='User',
            host='WORKSTATION',
            contact='unknown',
            created=op_agent_creation_time,
        )
        want_operation_metadata = dict(
            operation_name='test',
            operation_start=event_log_op_start_time,
            operation_adversary='test adversary',
        )
        want_attack_metadata = dict(
            tactic='test tactic',
            technique_name='test technique',
            technique_id='T0000',
        )
        want = [
            dict(
                command='whoami',
                plaintext_command='whoami',
                delegated_timestamp=LINK1_DECIDE_TIME,
                collected_timestamp=LINK1_COLLECT_TIME,
                finished_timestamp=LINK1_FINISH_TIME,
                status=0,
                platform='windows',
                executor='psh',
                pid=789,
                agent_metadata=want_agent_metadata,
                ability_metadata=dict(
                  ability_id='123',
                  ability_name='test ability',
                  ability_description='test ability desc',
                ),
                operation_metadata=want_operation_metadata,
                attack_metadata=want_attack_metadata,
            ),
            dict(
                command='hostname',
                plaintext_command='hostname',
                delegated_timestamp=LINK2_DECIDE_TIME,
                collected_timestamp=LINK2_COLLECT_TIME,
                finished_timestamp=LINK2_FINISH_TIME,
                status=0,
                platform='windows',
                executor='psh',
                pid=7890,
                agent_metadata=want_agent_metadata,
                ability_metadata=dict(
                    ability_id='456',
                    ability_name='test ability 2',
                    ability_description='test ability 2 desc',
                ),
                operation_metadata=want_operation_metadata,
                attack_metadata=want_attack_metadata,
            ),
        ]
        event_logs = event_loop.run_until_complete(op_for_event_logs.event_logs(file_svc, data_svc))
        assert event_logs == want

    @pytest.mark.usefixtures(
        "setup_op_config"
    )
    def test_writing_event_logs_to_disk(self, event_loop, op_for_event_logs, operation_agent, file_svc, data_svc,
                                        event_log_op_start_time, op_agent_creation_time, fire_event_mock):
        event_loop.run_until_complete(data_svc.remove('agents', match=dict(unique=operation_agent.unique)))
        event_loop.run_until_complete(data_svc.store(operation_agent))

        want_agent_metadata = dict(
            paw='testpaw',
            group='red',
            architecture='amd64',
            username='testagent',
            location=r'C:\Users\Public\test.exe',
            pid=1234,
            ppid=123,
            privilege='User',
            host='WORKSTATION',
            contact='unknown',
            created=op_agent_creation_time,
        )
        want_operation_metadata = dict(
            operation_name='test',
            operation_start=event_log_op_start_time,
            operation_adversary='test adversary',
        )
        want_attack_metadata = dict(
            tactic='test tactic',
            technique_name='test technique',
            technique_id='T0000',
        )
        want = [
            dict(
                command='whoami',
                plaintext_command='whoami',
                delegated_timestamp=LINK1_DECIDE_TIME,
                collected_timestamp=LINK1_COLLECT_TIME,
                finished_timestamp=LINK1_FINISH_TIME,
                status=0,
                platform='windows',
                executor='psh',
                pid=789,
                agent_metadata=want_agent_metadata,
                ability_metadata=dict(
                    ability_id='123',
                    ability_name='test ability',
                    ability_description='test ability desc',
                ),
                operation_metadata=want_operation_metadata,
                attack_metadata=want_attack_metadata,
            ),
            dict(
                command='hostname',
                plaintext_command='hostname',
                delegated_timestamp=LINK2_DECIDE_TIME,
                collected_timestamp=LINK2_COLLECT_TIME,
                finished_timestamp=LINK2_FINISH_TIME,
                status=0,
                platform='windows',
                executor='psh',
                pid=7890,
                agent_metadata=want_agent_metadata,
                ability_metadata=dict(
                    ability_id='456',
                    ability_name='test ability 2',
                    ability_description='test ability 2 desc',
                ),
                operation_metadata=want_operation_metadata,
                attack_metadata=want_attack_metadata,
            ),
        ]
        event_loop.run_until_complete(op_for_event_logs.write_event_logs_to_disk(file_svc, data_svc))
        target_path = f'/tmp/event_logs/operation_{op_for_event_logs.id}.json'
        assert os.path.isfile(target_path)
        try:
            with open(target_path, 'rb') as log_file:
                recorded_log = json.load(log_file)
            assert recorded_log == want
        finally:
            os.remove(target_path)

    @mock.patch.object(Operation, '_emit_state_change_event')
    def test_no_state_change_event_on_instantiation(self, mock_emit_state_change_method, fake_event_svc, adversary):
        Operation(name='test', agents=[], adversary=adversary)
        mock_emit_state_change_method.assert_not_called()

    @mock.patch.object(Operation, '_emit_state_change_event')
    def test_no_state_change_event_fired_when_setting_same_state(self, mock_emit_state_change_method, fake_event_svc,
                                                                 adversary):
        initial_state = 'running'
        op = Operation(name='test', agents=[], adversary=adversary, state=initial_state)
        op.state = initial_state
        mock_emit_state_change_method.assert_not_called()

    @mock.patch.object(Operation, '_emit_state_change_event')
    def test_state_change_event_fired_on_state_change(self, mock_emit_state_change_method, fake_event_svc, adversary):
        op = Operation(name='test', agents=[], adversary=adversary, state='running')
        op.state = 'finished'
        mock_emit_state_change_method.assert_called_with(from_state='running', to_state='finished')

    def test_emit_state_change_event(self, event_loop, fake_event_svc, adversary):
        op = Operation(name='test', agents=[], adversary=adversary, state='running')
        fake_event_svc.reset()

        event_loop.run_until_complete(
            op._emit_state_change_event(
                from_state='running',
                to_state='finished'
            )
        )

        expected_key = (Operation.EVENT_EXCHANGE, Operation.EVENT_QUEUE_STATE_CHANGED)
        assert expected_key in fake_event_svc.fired

        event_kwargs = fake_event_svc.fired[expected_key]
        assert event_kwargs['op'] == op.id
        assert event_kwargs['from_state'] == 'running'
        assert event_kwargs['to_state'] == 'finished'

    def test_with_learning_parser(self, event_loop, app_svc, file_svc, contact_svc, data_svc, learning_svc, event_svc, op_with_learning_parser,
                                  make_test_link, make_test_result, knowledge_svc, fire_event_mock):
        test_link = make_test_link(1234)
        op_with_learning_parser.add_link(test_link)
        test_result = make_test_result(test_link.id)
        event_loop.run_until_complete(data_svc.store(op_with_learning_parser))
        event_loop.run_until_complete(contact_svc._save(test_result))
        assert len(test_link.facts) == 1
        fact = test_link.facts[0]
        assert fact.trait == 'host.ip.address'
        assert fact.value == '10.10.10.10'
        knowledge_data = event_loop.run_until_complete(op_with_learning_parser.all_facts())
        assert len(knowledge_data) == 1
        assert knowledge_data[0].trait == 'host.ip.address'
        assert knowledge_data[0].value == '10.10.10.10'

    def test_without_learning_parser(self, event_loop, app_svc, contact_svc, data_svc, learning_svc, event_svc,
                                     op_without_learning_parser, make_test_link, make_test_result):
        test_link = make_test_link(5678)
        op_without_learning_parser.add_link(test_link)
        test_result = make_test_result(test_link.id)
        event_loop.run_until_complete(data_svc.store(op_without_learning_parser))
        event_loop.run_until_complete(contact_svc._save(test_result))
        assert len(test_link.facts) == 0

    def test_facts(self, event_loop, app_svc, contact_svc, file_svc, data_svc, learning_svc, fire_event_mock,
                   op_with_learning_and_seeded, make_test_link, make_test_result, knowledge_svc):
        event_loop.run_until_complete(data_svc.store(op_with_learning_and_seeded.source))
        test_link = make_test_link(9876)
        op_with_learning_and_seeded.add_link(test_link)

        test_result = make_test_result(test_link.id)
        event_loop.run_until_complete(data_svc.store(op_with_learning_and_seeded))
        event_loop.run_until_complete(op_with_learning_and_seeded._init_source())  # need to call this manually (no 'run')
        event_loop.run_until_complete(contact_svc._save(test_result))
        assert len(test_link.facts) == 1
        fact = test_link.facts[0]
        assert fact.trait == 'host.ip.address'
        assert fact.value == '10.10.10.10'

        knowledge_data = event_loop.run_until_complete(op_with_learning_and_seeded.all_facts())
        assert len(knowledge_data) == 2
        origin_set = [x.source for x in knowledge_data]
        assert op_with_learning_and_seeded.id in origin_set
        assert op_with_learning_and_seeded.source.id in origin_set

        report = event_loop.run_until_complete(op_with_learning_and_seeded.report(file_svc, data_svc))
        assert len(report['facts']) == 2

    async def test_wait_for_links_completion_ignorable_link(self, make_test_link, operation_agent):
        test_agent = operation_agent
        test_link = make_test_link(9876, test_agent.paw, Link().states['DISCARD'])
        op = Operation(name='test', agents=[test_agent], state='running')
        op.add_link(test_link)
        assert not op.ignored_links
        assert test_link in op.chain
        await op.wait_for_links_completion([test_link.id])
        assert test_link.id in op.ignored_links
        assert len(op.ignored_links) == 1
        assert test_link in op.chain

    async def test_wait_for_links_completion_non_ignorable_link(self, make_test_link, untrusted_operation_agent, mocker,
                                                                async_return):
        test_agent = untrusted_operation_agent
        test_link = make_test_link(9876, test_agent.paw)
        op = Operation(name='test', agents=[test_agent], state='running')
        op.add_link(test_link)
        assert not op.ignored_links
        assert test_link in op.chain
        with mocker.patch('asyncio.sleep') as mock_sleep:
            mock_sleep.return_value = async_return(None)
            await op.wait_for_links_completion([test_link.id])
        assert not op.ignored_links
        assert test_link in op.chain

    def test_wait_for_links_completion_link_timeout(self, event_loop, make_test_link, operation_agent, mocker):
        """A link that never finishes and whose agent stays trusted must be marked TIMEOUT
        after the per-link deadline (base_timeout + link_timeout) so the planner can continue
        rather than blocking forever (regression test for issue #3254)."""
        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        from app.utility.base_service import BaseService

        test_agent = operation_agent  # trusted agent
        # Link is in EXECUTE state (-3) and has no finish set -- it will never complete on its own.
        test_link = make_test_link(1111, test_agent.paw, Link().states['EXECUTE'])
        op = Operation(name='test', agents=[test_agent], state='running')
        op.add_link(test_link)

        # Use near-zero timeouts so the real asyncio.wait_for fires quickly,
        # which properly cancels the _wait_for_link coroutine (no resource leak).
        op.base_timeout = 0
        op.link_timeout = 0.01

        # Provide a stub event service so that setting link.status doesn't raise.
        mock_event_svc = MagicMock()
        mock_event_svc.fire_event = AsyncMock()

        async def _block_forever(link, member):
            """Simulate a link that never completes."""
            await asyncio.sleep(3600)

        with mocker.patch.object(BaseService, 'get_service', return_value=mock_event_svc):
            with mocker.patch.object(Operation, '_wait_for_link', side_effect=_block_forever):
                event_loop.run_until_complete(op.wait_for_links_completion([test_link.id]))

        # The link must now be TIMEOUT so downstream planner steps are unblocked.
        assert test_link.status == Link().states['TIMEOUT']

    def test_update_untrusted_agents_with_trusted(self, operation_agent, ability, adversary):
        operation_agent.trusted = True
        op = Operation(name='test', agents=[operation_agent], adversary=adversary)
        op.update_untrusted_agents(operation_agent)
        assert not op.untrusted_agents

    def test_update_untrusted_agents_with_untrusted(self, operation_agent, ability, adversary):
        operation_agent.trusted = False
        op = Operation(name='test', agents=[operation_agent], adversary=adversary)
        op.update_untrusted_agents(operation_agent)
        assert operation_agent.paw in op.untrusted_agents

    def test_update_untrusted_agents_with_trusted_no_operation_agents(self, operation_agent, ability, adversary):
        operation_agent.trusted = True
        op = Operation(name='test', agents=[], adversary=adversary)
        op.update_untrusted_agents(operation_agent)
        assert not op.untrusted_agents

    def test_update_untrusted_agents_with_untrusted_no_operation_agents(self, operation_agent, ability, adversary):
        operation_agent.trusted = False
        op = Operation(name='test', agents=[], adversary=adversary)
        op.update_untrusted_agents(operation_agent)
        assert not op.untrusted_agents

    def test_check_reason_skipped_unknown_platform(self, test_agent, test_ability):
        test_agent.platform = 'unknown'
        op = Operation(name='test', agents=[test_agent], state='running')
        reason = op._check_reason_skipped(agent=test_agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=test_agent.executors, agent_ran={})
        assert reason['reason'] == 'Platform not available'
        assert reason['reason_id'] == Operation.Reason.PLATFORM.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_valid_executor(self, test_agent, test_ability):
        test_agent.platform = 'darwin'
        op = Operation(name='test', agents=[test_agent], state='running')
        reason = op._check_reason_skipped(agent=test_agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=[], agent_ran={})
        assert reason['reason'] == 'Mismatched ability platform and executor'
        assert reason['reason_id'] == Operation.Reason.EXECUTOR.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_privilege(self, custom_agent, test_ability, mocker, test_executor):
        test_executor.name = 'psh'
        agent = custom_agent()
        test_ability.privilege = 'Elevated'
        op = Operation(name='test', agents=[agent], state='running')
        reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Ability privilege not fulfilled'
        assert reason['reason_id'] == Operation.Reason.PRIVILEGE.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_fact_dependency(self, custom_agent, test_ability, mocker, test_executor, fact):
        test_executor.name = 'psh'
        agent = custom_agent()
        op = Operation(name='test', agents=[agent], state='running')
        with mocker.patch('app.objects.c_ability.Ability.find_executors') as mock_find_executors:
            mock_find_executors.return_value = [test_executor]
            with mocker.patch('re.findall') as mock_findall:
                mock_findall.return_value = [fact('test.fact.attribute')]
                reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                                  agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Fact dependency not fulfilled'
        assert reason['reason_id'] == Operation.Reason.FACT_DEPENDENCY.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_link_ignored(self, custom_agent, test_ability, mocker, active_link):
        agent = custom_agent()
        op = Operation(name='test', agents=[agent], state='running')
        test_link = Link.load(active_link)
        op.chain = [test_link]
        op.ignored_links = [test_link.id]
        reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Link ignored - highly visible or discarded link'
        assert reason['reason_id'] == Operation.Reason.LINK_IGNORED.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_untrusted(self, custom_agent, test_ability, mocker):
        agent = custom_agent(trusted=False)
        op = Operation(name='test', agents=[agent], state='running')
        reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Agent not trusted'
        assert reason['reason_id'] == Operation.Reason.UNTRUSTED.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_op_running(self, custom_agent, test_ability, mocker):
        agent = custom_agent()
        op = Operation(name='test', agents=[agent], state='running')
        reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Operation not completed'
        assert reason['reason_id'] == Operation.Reason.OP_RUNNING.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_check_reason_skipped_other(self, custom_agent, test_ability, mocker):
        agent = custom_agent()
        op = Operation(name='test', agents=[agent], state='finished')
        reason = op._check_reason_skipped(agent=agent, ability=test_ability, op_facts=[], state=op.state,
                                          agent_executors=agent.executors, agent_ran={})
        assert reason['reason'] == 'Other'
        assert reason['reason_id'] == Operation.Reason.OTHER.value
        assert reason['ability_id'] == test_ability.ability_id
        assert reason['ability_name'] == test_ability.name

    async def test_add_ignored_link(self, make_test_link, operation_agent):
        test_agent = operation_agent
        test_link = make_test_link(9876, test_agent.paw, Link().states['DISCARD'])
        op = Operation(name='test', agents=[test_agent], state='running')
        op.add_ignored_link(test_link.id)
        assert op.ignored_links
        assert test_link.id in op.ignored_links
        assert len(op.ignored_links) == 1

    async def test_report_includes_steps_for_agents_not_in_host_group(
            self, operation_agent, operation_adversary, executor, ability, operation_link,
            encoded_command, parse_datestring, file_svc, data_svc, knowledge_svc, fire_event_mock):
        """Regression test for issue #3048: a link whose paw is absent from operation.agents
        must not cause report() to silently return None (i.e. download as 'Null')."""
        from app.objects.c_planner import Planner
        from app.objects.c_objective import Objective

        op = Operation(name='report-test', agents=[operation_agent], adversary=operation_adversary)
        op.set_start_details()
        op.planner = Planner(planner_id='testplanner', name='test_planner', module='test', params=None)
        op.objective = Objective(id='obj1', name='test objective')

        exe = executor(name='psh', platform='windows', command='whoami')
        ab = ability(ability_id='rep123', tactic='test tactic', technique_id='T0000',
                     technique_name='test technique', name='test ability',
                     description='test desc', executors=[exe])

        known_link = operation_link(
            command=encoded_command('whoami'),
            plaintext_command=encoded_command('whoami'),
            paw=operation_agent.paw,
            ability=ab, executor=exe, status=0, host=operation_agent.host, pid=1,
            decide=parse_datestring(LINK1_DECIDE_TIME),
        )
        orphan_paw = 'orphan-paw-not-in-agents'
        orphan_link = operation_link(
            command=encoded_command('id'),
            plaintext_command=encoded_command('id'),
            paw=orphan_paw,
            ability=ab, executor=exe, status=0, host='orphan-host', pid=2,
            decide=parse_datestring(LINK2_DECIDE_TIME),
        )
        op.chain = [known_link, orphan_link]

        report = await op.report(file_svc, data_svc, output=False)
        assert report is not None, 'report() must not return None when a link paw is absent from agents'
        assert 'steps' in report
        assert orphan_paw in report['steps'], 'orphan paw steps must appear in report'
        assert operation_agent.paw in report['steps'], 'known agent paw steps must appear in report'

    async def test_operation_cleanup_status(self, fake_planning_svc, operation_agent):
        services = {'planning_svc': fake_planning_svc}
        op = Operation(name='test with cleanup', agents=[operation_agent], state='running')
        assert op.state == 'running'
        assert await op.is_closeable()
        await op._cleanup_operation(services)
        assert op.state == 'cleanup'

    # -- Tests for issue #2988: fact source relationships should be resolved against source facts --

    def test_resolve_fact_with_value_unchanged(self, adversary):
        """A fact that already has a value should be returned as-is."""
        full_fact = Fact(trait='domain.user.name', value='alice')
        fact_list = [full_fact, Fact(trait='domain.user.name', value='bob')]
        op = Operation(name='test', agents=[], adversary=adversary)
        result = op._resolve_fact(full_fact, fact_list)
        assert result is full_fact
        assert result.value == 'alice'

    def test_resolve_fact_trait_only_resolves_to_matching_fact(self, adversary):
        """A trait-only fact (value=None) should be resolved to the first matching fact in the list."""
        stub = Fact(trait='domain.user.name', value=None)
        matched = Fact(trait='domain.user.name', value='alice')
        fact_list = [matched, Fact(trait='domain.user.password', value='secret')]
        op = Operation(name='test', agents=[], adversary=adversary)
        result = op._resolve_fact(stub, fact_list)
        assert result is matched
        assert result.value == 'alice'

    def test_resolve_fact_no_match_returns_original(self, adversary):
        """If no matching trait is found, the original stub is returned unchanged."""
        stub = Fact(trait='nonexistent.trait', value=None)
        fact_list = [Fact(trait='domain.user.name', value='alice')]
        op = Operation(name='test', agents=[], adversary=adversary)
        result = op._resolve_fact(stub, fact_list)
        assert result is stub

    async def test_init_source_seeds_relationship_with_resolved_facts(self, knowledge_svc, fire_event_mock, adversary):
        """Relationships in a fact source that use trait-only fact references should be seeded
        with the resolved (non-null) fact values from the source's fact list. Regression test
        for issue #2988."""
        source_fact_name = Fact(trait='domain.user.name', value='admin')
        source_fact_pass = Fact(trait='domain.user.password', value='s3cr3t')
        # Relationship stubs only carry trait (value=None), as stored when created via the UI
        rel_source_stub = Fact(trait='domain.user.name', value=None)
        rel_target_stub = Fact(trait='domain.user.password', value=None)
        rel = Relationship(source=rel_source_stub, edge='has_password', target=rel_target_stub)

        source = Source(id='src-2988', name='test-2988',
                        facts=[source_fact_name, source_fact_pass],
                        relationships=[rel])
        op = Operation(name='test-2988', agents=[], adversary=adversary, source=source)
        await op._init_source()

        seeded_rels = await knowledge_svc.get_relationships(criteria=dict(origin='src-2988'))
        assert len(seeded_rels) == 1
        seeded_rel = seeded_rels[0]
        assert seeded_rel.source.value == 'admin', (
            'Relationship source fact value should be resolved from the source fact list, not None'
        )
        assert seeded_rel.target.value == 's3cr3t', (
            'Relationship target fact value should be resolved from the source fact list, not None'
        )
