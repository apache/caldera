import asyncio

import pytest
from aiohttp import web

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
    def test_init_without_preset_event_loop(self):
        """AppService must construct when no event loop is set on the thread.

        Python 3.14 no longer creates an implicit loop in
        asyncio.get_event_loop(), so constructing AppService before any other
        service has set one raised RuntimeError (#3368).
        """
        try:
            previous = asyncio.get_event_loop()
        except RuntimeError:
            previous = None
        asyncio.set_event_loop(None)
        try:
            service = AppService(web.Application())
            assert service.loop is not None
        finally:
            asyncio.set_event_loop(previous)

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
