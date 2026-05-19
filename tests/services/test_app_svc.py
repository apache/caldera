import pytest
import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.objects.c_agent import Agent
from app.service.app_svc import AppService
from app.service.contact_svc import ContactService
from app.service.data_svc import DataService
from app.service.file_svc import FileSvc
from app.utility.base_service import BaseService
from app.utility.base_world import BaseWorld


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

    async def test_validate_requirements(self, app_svc):
        reqs = dict(
            go=dict(
                command='go version',
                type='installed_program',
                version='1.24',
            ),
            python=dict(
                attr='version',
                module='sys',
                type='python_module',
                version='3.9.0'
            )
        )
        BaseWorld.set_config('main', 'requirements', reqs)

        # Test success
        with mock.patch.object(subprocess, 'check_output', return_value=b'go version go1.25.5 linux/arm64\n'):
            await app_svc.validate_requirements()
            for req, param in reqs.items():
                assert await app_svc.validate_requirement(req, param)

        # Test failure due to obsolete version
        with mock.patch.object(subprocess, 'check_output', return_value=b'go version go1.19 linux/arm64\n'):
            assert not await app_svc.validate_requirement('go', {'command': 'go version', 'type': 'installed_program', 'version': '1.24'})
            assert not await app_svc.validate_requirement('go', {'command': 'go version', 'type': 'installed_program', 'version': '1.24', 'optional': True})

        # Test failure due to unknown version
        with mock.patch.object(subprocess, 'check_output', return_value=b'go version X linux/arm64\n'):
            assert not await app_svc.validate_requirement('go', {'command': 'go version', 'type': 'installed_program', 'version': '1.24'})

        # Test FileNotFoundError due to bad command
        BaseWorld.set_config('main', 'requirements', dict(
            go=dict(
                command='thiscommanddoesnotexist',
                type='installed_program',
                version='1.24',
            )
        ))
        assert not await app_svc.validate_requirement('go', {'command': 'thiscommanddoesnotexist', 'type': 'installed_program', 'version': '1.24'})

        # Test Exception
        with mock.patch.object(subprocess, 'check_output') as mock_check_output:
            mock_check_output.side_effect = Exception('testexception')
            assert not await app_svc.validate_requirement('go', {'command': 'go version', 'type': 'installed_program', 'version': '1.24'})

    async def test_start_sniffer_untrusted_agents(self, app_svc):
        trusted_agent = Agent(paw='test', trusted=True, sleep_max=1)
        untrusted_agent = Agent(paw='test', trusted=True, sleep_max=1)
        start_time = datetime.now(timezone.utc)
        trusted_agent.last_trusted_seen = start_time
        untrusted_agent.last_trusted_seen = start_time - timedelta(0, 30)
        with mock.patch.object(asyncio, 'sleep'):
            with mock.patch.object(AppService, 'get_config', return_value=10):
                with mock.patch.object(AppService, 'update_operations_with_untrusted_agent') as mock_update_ops:
                    mock_update_ops.side_effect = Exception('terminate test_start_sniffer_untrusted_agents')
                    with mock.patch.object(DataService, 'locate', return_value=[trusted_agent, untrusted_agent]):
                        await app_svc.start_sniffer_untrusted_agents()
                        mock_update_ops.assert_called_once_with(untrusted_agent)
                        assert not untrusted_agent.trusted
                        assert trusted_agent.trusted

    @mock.patch.object(ContactService, 'build_filename', return_value='mock_filename')
    @mock.patch.object(logging.Logger, 'debug')
    @mock.patch.object(FileSvc, 'find_file_path', return_value=(None, 'mock_file'))
    async def test_retrieve_compiled_file(self, mock_find_filepath, mock_debug, mock_build_filename, app_svc):
        file_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])
        m = mock.mock_open(read_data=file_data)
        with mock.patch('builtins.open', m):
            result = await app_svc.retrieve_compiled_file('test_file', 'windows')
        assert result == ('test_file-windows', 'mock_filename')
        mock_find_filepath.assert_called_once_with('test_file-windows', location='')
        mock_debug.assert_called_once_with('test_file downloaded with hash=55c53f5d490297900cefa825d0c8e8e9532ee8a118abe7d8570762cd38be9818 and name=mock_filename')
