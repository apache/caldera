"""Tests for operation API performance optimizations (issue #3019).

Verifies that get_hosts() and get_reachable_hosts() use parallel async queries
via asyncio.gather() instead of sequential awaits.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v2.managers.operation_api_manager import OperationApiManager


@pytest.fixture
def mock_services():
    knowledge_svc = MagicMock()
    knowledge_svc.get_facts = AsyncMock(return_value=[])
    return {
        'data_svc': MagicMock(),
        'file_svc': MagicMock(),
        'knowledge_svc': knowledge_svc,
    }


@pytest.fixture
def manager(mock_services):
    return OperationApiManager(mock_services)


class TestGetHostsParallel:
    """Verify get_hosts uses asyncio.gather for parallel host resolution."""

    async def test_get_hosts_empty_chain(self, manager):
        result = await manager.get_hosts({'chain': []})
        assert result == {}

    async def test_get_hosts_skips_none_host(self, manager):
        result = await manager.get_hosts({'chain': [{'host': None}]})
        assert result == {}

    async def test_get_hosts_skips_missing_agent(self, manager):
        manager.find_object = MagicMock(return_value=None)
        result = await manager.get_hosts({'chain': [{'host': 'host1'}]})
        assert result == {}

    async def test_get_hosts_deduplicates(self, manager):
        """Multiple links with the same host should only resolve once."""
        agent = MagicMock()
        agent.display = {'host': 'h1', 'host_ip_addrs': ['10.0.0.1'], 'platform': 'linux', 'paw': 'abc'}
        manager.find_object = MagicMock(return_value=agent)

        with patch.object(manager, 'get_reachable_hosts', new_callable=AsyncMock, return_value=[]):
            result = await manager.get_hosts({
                'chain': [{'host': 'h1'}, {'host': 'h1'}, {'host': 'h1'}]
            })
            assert len(result) == 1
            manager.get_reachable_hosts.assert_called_once()

    async def test_get_hosts_parallel_multiple_hosts(self, manager):
        """Multiple distinct hosts should be resolved in parallel via gather."""
        agents = {}
        for name in ('h1', 'h2', 'h3'):
            agent = MagicMock()
            agent.display = {'host': name, 'host_ip_addrs': [], 'platform': 'linux', 'paw': name}
            agents[name] = agent
        manager.find_object = MagicMock(side_effect=lambda _, match: agents.get(match.get('host')))

        call_order = []

        async def mock_reachable(agent=None):
            call_order.append(agent['host'])
            return [f'{agent["host"]}-peer']

        with patch.object(manager, 'get_reachable_hosts', side_effect=mock_reachable):
            result = await manager.get_hosts({
                'chain': [{'host': 'h1'}, {'host': 'h2'}, {'host': 'h3'}]
            })
            assert len(result) == 3
            for h in ('h1', 'h2', 'h3'):
                assert h in result
                assert result[h]['reachable_hosts'] == [f'{h}-peer']

    async def test_get_hosts_uses_gather(self, manager):
        """Verify asyncio.gather is used for parallel host resolution.

        A sequential implementation would also produce correct results,
        so this test explicitly asserts that asyncio.gather is invoked.
        """
        agent = MagicMock()
        agent.display = {'host': 'h1', 'host_ip_addrs': [], 'platform': 'linux', 'paw': 'abc'}
        manager.find_object = MagicMock(return_value=agent)

        with patch.object(manager, 'get_reachable_hosts', new_callable=AsyncMock, return_value=[]):
            with patch('app.api.v2.managers.operation_api_manager.asyncio.gather',
                       wraps=__import__('asyncio').gather) as mock_gather:
                await manager.get_hosts({'chain': [{'host': 'h1'}]})
                mock_gather.assert_called_once()


class TestGetReachableHostsParallel:
    """Verify get_reachable_hosts batches trait queries via asyncio.gather."""

    async def test_no_traits_returns_empty(self, manager):
        with patch('app.api.v2.managers.operation_api_manager.BaseWorld') as mock_bw:
            mock_bw.get_config.return_value = []
            result = await manager.get_reachable_hosts(agent={'paw': 'abc'})
            assert result == []

    async def test_multiple_traits_queried_in_parallel(self, manager, mock_services):
        """Each trait should trigger a knowledge_svc.get_facts call, all via gather."""
        fact1 = MagicMock()
        fact1.value = 'host-a'
        fact2 = MagicMock()
        fact2.value = 'host-b'
        mock_services['knowledge_svc'].get_facts = AsyncMock(side_effect=[[fact1], [fact2]])

        with patch('app.api.v2.managers.operation_api_manager.BaseWorld') as mock_bw:
            mock_bw.get_config.return_value = ['remote.host.fqdn', 'remote.host.ip']
            result = await manager.get_reachable_hosts(agent={'paw': 'abc'})
            assert set(result) == {'host-a', 'host-b'}
            assert mock_services['knowledge_svc'].get_facts.call_count == 2

    async def test_get_reachable_hosts_uses_gather(self, manager, mock_services):
        """Verify asyncio.gather is used so trait queries run concurrently.

        Without this assertion a sequential refactor would silently pass
        all other tests.
        """
        mock_services['knowledge_svc'].get_facts = AsyncMock(return_value=[])

        with patch('app.api.v2.managers.operation_api_manager.BaseWorld') as mock_bw:
            mock_bw.get_config.return_value = ['trait.a', 'trait.b']
            with patch('app.api.v2.managers.operation_api_manager.asyncio.gather',
                       wraps=__import__('asyncio').gather) as mock_gather:
                await manager.get_reachable_hosts(agent={'paw': 'abc'})
                mock_gather.assert_called_once()
