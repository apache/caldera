"""Tests for TTL-based eviction in DataService.

Rather than duplicating the production filtering predicate, these tests
drive the actual _evict_expired_objects() coroutine so that any future
changes to the eviction logic are automatically exercised.
"""
import asyncio
import datetime
import unittest
from unittest.mock import MagicMock, patch


def _make_data_svc():
    """Return a DataService instance with a minimal stub environment."""
    from app.service.data_svc import DataService
    svc = DataService.__new__(DataService)
    svc.log = MagicMock()
    svc._ttl_config = {'operations': 7 * 86400}  # 7 days
    svc.ram = {'operations': []}
    svc._eviction_task = None
    return svc


def _make_op(finish_marker, age_days):
    """Build a mock operation object."""
    now = datetime.datetime.utcnow()
    op = MagicMock()
    op.finish = finish_marker
    op.start = now - datetime.timedelta(days=age_days)
    return op


class TestTTLEviction(unittest.TestCase):
    def _run_one_eviction_cycle(self, svc):
        """Run one pass of the eviction coroutine by patching asyncio.sleep."""
        async def run():
            # Patch sleep so the infinite loop runs only once then raises to exit.
            call_count = 0

            async def fake_sleep(_):
                nonlocal call_count
                call_count += 1
                if call_count >= 1:
                    raise asyncio.CancelledError

            with patch('asyncio.sleep', side_effect=fake_sleep):
                try:
                    await svc._evict_expired_objects()
                except asyncio.CancelledError:
                    pass

        asyncio.get_event_loop().run_until_complete(run())

    def test_old_finished_operations_evicted(self):
        """Finished operations older than TTL must be removed."""
        svc = _make_data_svc()
        old_op = _make_op('done', age_days=10)
        new_op = _make_op('done', age_days=1)
        running_op = _make_op(None, age_days=30)  # no finish marker – not expired
        svc.ram['operations'] = [old_op, new_op, running_op]

        self._run_one_eviction_cycle(svc)

        self.assertNotIn(old_op, svc.ram['operations'])
        self.assertIn(new_op, svc.ram['operations'])
        self.assertIn(running_op, svc.ram['operations'])

    def test_running_operations_not_evicted(self):
        """Operations without a finish marker must never be evicted regardless of age."""
        svc = _make_data_svc()
        running_op = _make_op(None, age_days=100)
        svc.ram['operations'] = [running_op]

        self._run_one_eviction_cycle(svc)

        self.assertIn(running_op, svc.ram['operations'])

    def test_no_eviction_when_ttl_not_set(self):
        """When no TTL is configured for operations, no eviction should occur."""
        svc = _make_data_svc()
        svc._ttl_config = {}
        old_op = _make_op('done', age_days=365)
        svc.ram['operations'] = [old_op]

        self._run_one_eviction_cycle(svc)

        self.assertIn(old_op, svc.ram['operations'])

    def test_eviction_exception_is_logged_not_propagated(self):
        """An exception during eviction must be logged without crashing the loop."""
        svc = _make_data_svc()
        # Make .ram raise on access to trigger error path.
        svc.ram = MagicMock()
        svc.ram.__contains__ = MagicMock(side_effect=RuntimeError('boom'))

        self._run_one_eviction_cycle(svc)

        svc.log.exception.assert_called_once()
