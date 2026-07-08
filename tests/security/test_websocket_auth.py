"""
Security regression tests for WebSocket contact authentication (CWE-306 fix).

Verifies that Handler._is_authenticated() correctly validates API keys
and that handle() rejects unauthenticated connections.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from argon2 import PasswordHasher

from app.contacts.contact_websocket import Handler

_ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def _make_argon2_hash(plaintext):
    """Return an Argon2id hash of plaintext, matching how Caldera stores API keys."""
    return _ph.hash(plaintext)


def _make_connection(key_header=''):
    conn = MagicMock()
    conn.request.headers = {'KEY': key_header} if key_header else {}
    conn.close = AsyncMock()
    conn.request.path = '/manx/1'
    return conn


class TestIsAuthenticated:
    """Unit tests for Handler._is_authenticated()."""

    def _make_handler(self):
        return Handler(services={})

    def test_empty_key_rejected(self):
        """A connection with no KEY header must be rejected."""
        handler = self._make_handler()
        stored_hash = _make_argon2_hash('ADMIN123')
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection('')
            assert not handler._is_authenticated(conn)

    def test_wrong_key_rejected(self):
        """A connection supplying the wrong key must be rejected."""
        handler = self._make_handler()
        stored_hash = _make_argon2_hash('ADMIN123')
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection('WRONGKEY')
            assert not handler._is_authenticated(conn)

    def test_correct_key_accepted(self):
        """A connection supplying the correct plaintext key must be accepted."""
        handler = self._make_handler()
        correct_key = 'SECRETAPIKEY'
        stored_hash = _make_argon2_hash(correct_key)
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection(correct_key)
            assert handler._is_authenticated(conn)

    def test_none_stored_key_rejected(self):
        """If get_config returns None (key not configured), connection must be rejected."""
        handler = self._make_handler()
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=None):
            conn = _make_connection('ANYKEY')
            assert not handler._is_authenticated(conn)

    def test_red_key_accepted(self):
        """The red API key is checked; a matching red key must be accepted."""
        handler = self._make_handler()
        red_key = 'RED_SECRET'
        stored_hash = _make_argon2_hash(red_key)

        def mock_get_config(key_name):
            if key_name == 'api_key_red':
                return stored_hash
            return None  # blue key not configured

        with patch('app.contacts.contact_websocket.BaseWorld.get_config', side_effect=mock_get_config):
            conn = _make_connection(red_key)
            assert handler._is_authenticated(conn)

    def test_blue_key_accepted(self):
        """The blue API key is checked; a matching blue key must be accepted."""
        handler = self._make_handler()
        blue_key = 'BLUE_SECRET'
        stored_hash = _make_argon2_hash(blue_key)

        def mock_get_config(key_name):
            if key_name == 'api_key_blue':
                return stored_hash
            return None  # red key not configured

        with patch('app.contacts.contact_websocket.BaseWorld.get_config', side_effect=mock_get_config):
            conn = _make_connection(blue_key)
            assert handler._is_authenticated(conn)

    def test_verify_hash_exception_returns_false(self):
        """If verify_hash raises an unexpected exception, _is_authenticated must return False."""
        handler = self._make_handler()
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value='not-a-valid-hash'), \
             patch('app.contacts.contact_websocket.verify_hash', side_effect=Exception('bad hash format')):
            conn = _make_connection('ANYKEY')
            assert not handler._is_authenticated(conn)


class TestHandleAuthentication:
    """Integration-style tests for Handler.handle() authentication gating."""

    def _make_handler(self):
        return Handler(services={})

    @pytest.mark.asyncio
    async def test_handle_closes_unauthenticated_connection(self):
        """handle() must close with code 1008 when the API key does not match."""
        handler = self._make_handler()
        stored_hash = _make_argon2_hash('SECRET')
        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection('WRONGKEY')
            await handler.handle(conn)
            conn.close.assert_awaited_once_with(1008, 'Unauthorized')

    @pytest.mark.asyncio
    async def test_handle_does_not_close_authenticated_connection(self):
        """handle() must NOT close the connection when the correct key is supplied."""
        handler = self._make_handler()
        correct_key = 'MYKEY'
        stored_hash = _make_argon2_hash(correct_key)

        mock_handle = MagicMock()
        mock_handle.tag = 'manx'
        mock_handle.run = AsyncMock()
        handler.handles = [mock_handle]

        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection(correct_key)
            conn.request.path = '/manx/1'
            await handler.handle(conn)
            conn.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_routes_authenticated_connection(self):
        """handle() must invoke the matching plugin handler for an authenticated connection."""
        handler = self._make_handler()
        correct_key = 'MYKEY'
        stored_hash = _make_argon2_hash(correct_key)

        mock_handle = MagicMock()
        mock_handle.tag = 'manx'
        mock_handle.run = AsyncMock()
        handler.handles = [mock_handle]

        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection(correct_key)
            conn.request.path = '/manx/1'
            await handler.handle(conn)
            mock_handle.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_no_matching_plugin_does_not_error(self):
        """handle() with a valid key but no matching plugin tag should not raise."""
        handler = self._make_handler()
        correct_key = 'MYKEY'
        stored_hash = _make_argon2_hash(correct_key)
        handler.handles = []  # no plugins registered

        with patch('app.contacts.contact_websocket.BaseWorld.get_config', return_value=stored_hash):
            conn = _make_connection(correct_key)
            conn.request.path = '/unknown/1'
            # Should complete without raising or closing with an error code
            await handler.handle(conn)
            conn.close.assert_not_called()
