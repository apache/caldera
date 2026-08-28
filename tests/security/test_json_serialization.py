"""Tests for DataService JSON serialization (save_state / restore_state).

Validates that:
- save_state writes JSON (not pickle) to disk
- restore_state can read both JSON and legacy pickle formats
- Corrupted / unexpected-type stores are handled gracefully
"""
import json
import os
import pickle
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_svc():
    """Construct a minimal DataService with mocked service lookups."""
    from app.service.data_svc import DataService

    svc = DataService.__new__(DataService)
    svc.log = MagicMock()
    svc.ram = {'agents': [], 'operations': [], 'schedules': []}

    file_svc = MagicMock()
    file_svc.save_file = AsyncMock()
    file_svc.read_file = AsyncMock()

    svc.get_service = MagicMock(return_value=file_svc)
    svc._prune_non_critical_data = AsyncMock()
    svc.store = AsyncMock()

    return svc, file_svc


# ---------------------------------------------------------------------------
# Generic JSON / pickle behaviour (kept from the original test suite)
# ---------------------------------------------------------------------------

class TestJsonPickleBasics:
    def test_json_round_trip(self):
        data = {'agents': [{'paw': 'abc123'}], 'operations': []}
        serialized = json.dumps(data).encode('utf-8')
        deserialized = json.loads(serialized.decode('utf-8'))
        assert data == deserialized

    def test_json_decode_error_on_pickle(self):
        data = {'key': 'value'}
        pickled = pickle.dumps(data)
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(pickled.decode('utf-8'))


# ---------------------------------------------------------------------------
# DataService.save_state
# ---------------------------------------------------------------------------

class TestSaveState:
    @pytest.mark.asyncio
    async def test_save_state_writes_json(self):
        """save_state must persist data as JSON, not pickle."""
        svc, file_svc = _make_data_svc()

        # Populate ram with objects that have a schema.dump interface
        obj = MagicMock()
        obj.schema = MagicMock()
        obj.schema.dump.return_value = {'paw': 'abc123'}
        svc.ram['agents'] = [obj]

        await svc.save_state()

        file_svc.save_file.assert_awaited_once()
        call_args = file_svc.save_file.call_args
        written_bytes = call_args[0][1]
        # Must be valid JSON
        parsed = json.loads(written_bytes.decode('utf-8'))
        assert parsed['agents'] == [{'paw': 'abc123'}]

    @pytest.mark.asyncio
    async def test_save_state_never_writes_pickle(self):
        """save_state must not fall back to pickle on serialization error."""
        svc, file_svc = _make_data_svc()

        # Object whose schema.dump raises -- save_state should propagate the error
        obj = MagicMock()
        obj.schema = MagicMock()
        obj.schema.dump.side_effect = TypeError('not serializable')
        svc.ram['agents'] = [obj]

        with pytest.raises(TypeError):
            await svc.save_state()

        # Ensure nothing was written (no pickle fallback)
        file_svc.save_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_state_uses_display_fallback(self):
        """Objects without schema but with display attr should use display."""
        svc, file_svc = _make_data_svc()

        obj = MagicMock(spec=['display'])
        obj.display = {'name': 'test'}
        svc.ram['agents'] = [obj]

        await svc.save_state()

        written_bytes = file_svc.save_file.call_args[0][1]
        parsed = json.loads(written_bytes.decode('utf-8'))
        assert parsed['agents'] == [{'name': 'test'}]


# ---------------------------------------------------------------------------
# DataService.restore_state
# ---------------------------------------------------------------------------

class TestRestoreState:
    @pytest.mark.asyncio
    async def test_restore_from_json(self):
        """restore_state should load JSON data and iterate over keys."""
        svc, file_svc = _make_data_svc()

        store_data = json.dumps({'agents': [{'paw': 'abc'}], 'schedules': []}).encode()
        file_svc.read_file.return_value = ('object_store', store_data)

        with patch('os.path.exists', return_value=True):
            await svc.restore_state()

        # JSON dicts don't have .store, so store() should NOT be called for them
        svc.store.assert_not_awaited()
        # But the keys should still be initialized
        assert 'agents' in svc.ram

    @pytest.mark.asyncio
    async def test_restore_from_pickle(self):
        """restore_state should fall back to pickle for legacy stores."""
        svc, file_svc = _make_data_svc()

        obj = MagicMock()
        obj.store = AsyncMock()
        store_data = pickle.dumps({'agents': [obj], 'schedules': []})
        file_svc.read_file.return_value = ('object_store', store_data)

        with patch('os.path.exists', return_value=True):
            await svc.restore_state()

        svc.store.assert_awaited_once_with(obj)

    @pytest.mark.asyncio
    async def test_restore_rejects_non_dict(self):
        """restore_state should gracefully handle non-dict JSON (e.g. a list)."""
        svc, file_svc = _make_data_svc()

        store_data = json.dumps([1, 2, 3]).encode()
        file_svc.read_file.return_value = ('object_store', store_data)

        with patch('os.path.exists', return_value=True):
            await svc.restore_state()

        svc.log.warning.assert_called()
        # ram should remain in its initial state (not crash)
        assert 'schedules' in svc.ram

    @pytest.mark.asyncio
    async def test_on_disk_format_is_json_after_save(self):
        """After save_state, the persisted bytes must be valid JSON (not pickle)."""
        svc, file_svc = _make_data_svc()
        svc.ram = {'agents': [], 'schedules': []}

        await svc.save_state()

        written_bytes = file_svc.save_file.call_args[0][1]
        # Should not start with pickle protocol bytes
        assert not written_bytes.startswith(b'\x80')
        # Must parse as JSON
        json.loads(written_bytes)
