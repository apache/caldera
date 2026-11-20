import asyncio
from unittest import mock

import pytest

from app.objects.c_adversary import Adversary
from app.objects.c_operation import Operation
from app.objects.c_planner import Planner
from app.objects.c_schedule import Schedule
from app.objects.c_source import Source
from app.service.app_svc import AppService
from app.utility.base_service import BaseService


@pytest.fixture
async def setup_running_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.RUNNING.value


@pytest.fixture
async def setup_run_one_link_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.RUN_ONE_LINK.value


@pytest.fixture
async def setup_paused_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.PAUSED.value


@pytest.fixture
async def setup_cleanup_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.CLEANUP.value


@pytest.fixture
async def setup_out_of_time_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.OUT_OF_TIME.value


@pytest.fixture
async def setup_finished_operation(setup_operations_api_test, test_agent, test_operation):
    operation = (await BaseService.get_service('data_svc').locate('operations', {'id': test_operation['id']}))[0]
    operation.agents.append(test_agent)
    operation.state = operation.States.FINISHED.value


class TestAppService:
    async def test_mark_agent_as_untrusted_running_operation(self, setup_running_operation, test_agent, app_svc, mocker,
                                                             async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.RUNNING.value
                assert test_agent in operation.agents
                assert test_agent.paw in operation.untrusted_agents

    async def test_mark_agent_as_untrusted_run_one_link_operation(self, setup_run_one_link_operation, test_agent,
                                                                  app_svc, mocker, async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.RUN_ONE_LINK.value
                assert test_agent in operation.agents
                assert test_agent.paw in operation.untrusted_agents

    async def test_mark_agent_as_untrusted_paused_operation(self, setup_paused_operation, test_agent, app_svc, mocker,
                                                            async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.PAUSED.value
                assert test_agent in operation.agents
                assert test_agent.paw in operation.untrusted_agents

    async def test_mark_agent_as_untrusted_out_of_time_operation(self, setup_out_of_time_operation, test_agent, app_svc,
                                                                 mocker, async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.OUT_OF_TIME.value
                assert test_agent in operation.agents
                assert not operation.untrusted_agents

    async def test_mark_agent_as_untrusted_finished_operation(self, setup_finished_operation, test_agent, app_svc,
                                                              mocker, async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.FINISHED.value
                assert test_agent in operation.agents
                assert not operation.untrusted_agents

    async def test_mark_agent_as_untrusted_cleanup_operation(self, setup_cleanup_operation, test_agent, app_svc, mocker,
                                                             async_return, test_operation):
        with mocker.patch('app.objects.c_operation.Operation.all_facts') as mock_all_facts:
            mock_all_facts.return_value = async_return([])
            with mocker.patch('app.objects.c_objective.Objective.completed') as mock_completed:
                mock_completed.return_value = False
                operation = (await BaseService.get_service('data_svc').locate('operations',
                                                                              {'id': test_operation['id']}))[0]
                test_agent.trusted = False
                await app_svc.update_operations_with_untrusted_agent(test_agent)
                assert operation.state == operation.States.CLEANUP.value
                assert test_agent in operation.agents
                assert not operation.untrusted_agents


class _StopScheduler(Exception):
    pass


def _build_app_svc_before_loop(running_loop):
    """Construct AppService the way server.py does: before run_tasks() creates the loop that runs it."""
    construction_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(construction_loop)
    try:
        svc = AppService(None)
    finally:
        asyncio.set_event_loop(running_loop)
    return svc, construction_loop


class TestOperationStartup:
    def test_resume_operations_starts_on_running_loop(self, event_loop, data_svc):
        svc, construction_loop = _build_app_svc_before_loop(event_loop)
        operation = Operation(name='resume check', id='resume-check-op')
        event_loop.run_until_complete(data_svc.store(operation))
        started = []

        async def fake_run(op, services):
            started.append(op.id)

        try:
            with mock.patch.object(Operation, 'run', fake_run):
                with mock.patch('app.service.app_svc.asyncio.sleep', new=mock.AsyncMock()):
                    event_loop.run_until_complete(svc.resume_operations())
                event_loop.run_until_complete(asyncio.sleep(0))
        finally:
            construction_loop.close()
            event_loop.run_until_complete(data_svc.remove('operations', dict(id=operation.id)))
        assert operation.id in started

    def test_scheduled_operation_uses_stored_objects_and_running_loop(self, event_loop, data_svc):
        svc, construction_loop = _build_app_svc_before_loop(event_loop)
        adversary = Adversary(adversary_id='sched-adv', name='scheduled adversary', atomic_ordering=['sched-ability'])
        planner = Planner(planner_id='sched-planner', name='scheduled planner', module='app.planners.atomic')
        source = Source(id='sched-source', name='scheduled source', facts=[])
        for obj in (adversary, planner, source):
            event_loop.run_until_complete(data_svc.store(obj))
        # Stand-ins that carry only the identifiers, so the assertions below show that
        # run_scheduler() replaced them with the objects stored in data_svc.
        task = Operation(name='scheduled op', adversary=Adversary(adversary_id='sched-adv', atomic_ordering=[]),
                         planner=Planner(planner_id='sched-planner'), source=Source(id='sched-source'))
        schedule = Schedule(schedule='* * * * *', task=task, id='sched-check')
        event_loop.run_until_complete(data_svc.store(schedule))
        started = []

        async def fake_run(op, services):
            started.append(op)

        async def no_agents(op, services):
            op.agents = []

        try:
            with mock.patch.object(Operation, 'run', fake_run), \
                    mock.patch.object(Operation, 'update_operation_agents', no_agents):
                with mock.patch('app.service.app_svc.asyncio.sleep', new=mock.AsyncMock(side_effect=_StopScheduler)):
                    with pytest.raises(_StopScheduler):
                        event_loop.run_until_complete(svc.run_scheduler())
                event_loop.run_until_complete(asyncio.sleep(0))
        finally:
            construction_loop.close()
            event_loop.run_until_complete(data_svc.remove('schedules', dict(id=schedule.id)))
            for op in started:
                event_loop.run_until_complete(data_svc.remove('operations', dict(id=op.id)))
            event_loop.run_until_complete(data_svc.remove('adversaries', dict(adversary_id=adversary.adversary_id)))
            event_loop.run_until_complete(data_svc.remove('planners', dict(planner_id=planner.planner_id)))
            event_loop.run_until_complete(data_svc.remove('sources', dict(id=source.id)))
        assert len(started) == 1
        scheduled = started[0]
        assert scheduled.name.startswith('scheduled op (')
        assert scheduled.adversary.atomic_ordering == ['sched-ability']
        assert scheduled.planner.module == 'app.planners.atomic'
        assert scheduled.source.name == 'scheduled source'
