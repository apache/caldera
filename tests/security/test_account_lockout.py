import time
import unittest
from unittest.mock import patch


class TestAccountLockout(unittest.TestCase):
    def setUp(self):
        from app.service.login_handlers import default
        default._login_attempts.clear()

    def test_no_lockout_initially(self):
        from app.service.login_handlers.default import _lockout_check
        self.assertFalse(_lockout_check('testuser'))

    def test_lockout_after_max_attempts(self):
        from app.service.login_handlers.default import _lockout_check, _record_failure
        for _ in range(5):
            _record_failure('testuser')
        self.assertTrue(_lockout_check('testuser'))

    def test_no_lockout_below_threshold(self):
        from app.service.login_handlers.default import _lockout_check, _record_failure
        for _ in range(4):
            _record_failure('testuser')
        self.assertFalse(_lockout_check('testuser'))

    def test_clear_failures(self):
        from app.service.login_handlers.default import _lockout_check, _record_failure, _clear_failures
        for _ in range(5):
            _record_failure('testuser')
        _clear_failures('testuser')
        self.assertFalse(_lockout_check('testuser'))

    def test_lockout_expires_after_window(self):
        from app.service.login_handlers.default import _lockout_check, _login_attempts, _LOCKOUT_WINDOW_SECONDS
        # Simulate old attempts
        old_time = time.monotonic() - _LOCKOUT_WINDOW_SECONDS - 10
        _login_attempts['testuser'] = [old_time] * 5
        self.assertFalse(_lockout_check('testuser'))


if __name__ == '__main__':
    unittest.main()
