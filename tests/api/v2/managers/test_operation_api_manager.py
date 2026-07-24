"""Unit tests for OperationApiManager._call_ability_plugin_hooks."""
import types

import pytest
from unittest.mock import MagicMock


class TestCallAbilityPluginHooks:
    """Tests that OperationApiManager._call_ability_plugin_hooks iterates all HOOKS."""

    @pytest.fixture
    def manager(self):
        from app.api.v2.managers.operation_api_manager import OperationApiManager
        services = {
            'data_svc': MagicMock(),
            'file_svc': MagicMock(),
            'knowledge_svc': MagicMock(),
        }
        return OperationApiManager(services)

    @pytest.fixture
    def ability(self):
        return MagicMock()

    @pytest.fixture
    def executor(self):
        ex = MagicMock()
        ex.HOOKS = {}
        return ex

    @pytest.mark.asyncio
    async def test_invokes_all_hooks(self, manager, ability, executor):
        """All entries in executor.HOOKS must be called."""
        call_log = []

        async def hook_a(ab, ex):
            call_log.append('a')

        async def hook_b(ab, ex):
            call_log.append('b')

        executor.HOOKS = {'key_a': hook_a, 'key_b': hook_b}
        await manager._call_ability_plugin_hooks(ability, executor)
        assert set(call_log) == {'a', 'b'}

    @pytest.mark.asyncio
    async def test_empty_hooks_is_noop(self, manager, ability, executor):
        """Empty HOOKS dict must not raise."""
        executor.HOOKS = {}
        await manager._call_ability_plugin_hooks(ability, executor)

    @pytest.mark.asyncio
    async def test_absent_hooks_is_noop(self, manager, ability):
        """Missing HOOKS attribute must not raise.

        Use SimpleNamespace instead of MagicMock because MagicMock
        auto-creates attributes on access, so hasattr() always returns True.
        SimpleNamespace accurately reflects attribute absence.
        """
        executor_without_hooks = types.SimpleNamespace()
        await manager._call_ability_plugin_hooks(ability, executor_without_hooks)
