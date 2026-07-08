import pytest
from unittest.mock import patch
from app.utility.base_world import BaseWorld

# Patch the subprocess reference inside base_world to avoid import-order
# dependent behavior when subprocess is imported differently in other modules.
_PATCH_TARGET = 'app.utility.base_world.subprocess.check_output'


class TestShlexSplit:
    def test_simple_command(self):
        params = {'type': 'installed_program', 'command': 'python3 --version', 'version': '3.0'}
        with patch(_PATCH_TARGET, return_value=b'Python 3.12.0') as mock:
            result = BaseWorld.check_requirement(params)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert args == ['python3', '--version']
            assert result is True

    def test_command_with_quotes(self):
        params = {'type': 'installed_program', 'command': 'echo "hello world"', 'version': '0.0.0'}
        with patch(_PATCH_TARGET, return_value=b'1.0.0') as mock:
            result = BaseWorld.check_requirement(params)
            args = mock.call_args[0][0]
            assert args == ['echo', 'hello world']
            assert result is True

    def test_command_with_spaces_in_path(self):
        params = {'type': 'installed_program', 'command': "'/path/to/my program' --version", 'version': '1.0'}
        with patch(_PATCH_TARGET, return_value=b'1.5.0') as mock:
            result = BaseWorld.check_requirement(params)
            args = mock.call_args[0][0]
            assert args == ['/path/to/my program', '--version']
            assert result is True

    def test_unmatched_quote_returns_false(self):
        """shlex.split raises ValueError on unmatched quotes; must return False, not raise."""
        params = {'type': 'installed_program', 'command': "python3 --flag 'unterminated", 'version': '3.0'}
        with patch(_PATCH_TARGET) as mock:
            result = BaseWorld.check_requirement(params)
            mock.assert_not_called()
            assert result is False

    def test_version_below_minimum_returns_false(self):
        """check_requirement must return False when the installed version is too old."""
        params = {'type': 'installed_program', 'command': 'python3 --version', 'version': '99.0'}
        with patch(_PATCH_TARGET, return_value=b'Python 3.12.0'):
            result = BaseWorld.check_requirement(params)
            assert result is False
