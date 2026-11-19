"""Tests that LDAP injection characters are properly escaped in the
DefaultLoginHandler before they are inserted into DN strings or
LDAP search filters."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn_value


class TestLdapRdnEscape:
    """escape_rdn_value must neutralise DN injection characters."""

    def test_escapes_comma(self):
        result = escape_rdn_value("admin,dc=evil,dc=com")
        # Commas must be escaped (as backslash-comma or hex \2c) so they
        # cannot split the RDN into multiple components.
        assert "\\," in result or "\\2c" in result.lower(), (
            f"Expected escaped comma in {result!r}"
        )

    def test_escapes_equals(self):
        result = escape_rdn_value("user=injected")
        # ldap3 escapes the = so it cannot break the DN structure
        assert result != "user=injected"

    def test_clean_username_unchanged(self):
        result = escape_rdn_value("jsmith")
        assert result == "jsmith"

    def test_escapes_null_byte(self):
        result = escape_rdn_value("admin\x00")
        assert "\x00" not in result
        # Verify the NUL was escaped to the expected representation
        assert "\\00" in result.lower() or "\\0" in result, (
            f"Expected escaped NUL (\\00) in {result!r}"
        )


class TestLdapFilterEscape:
    """escape_filter_chars must neutralise filter injection characters."""

    def test_escapes_asterisk(self):
        result = escape_filter_chars("*")
        assert result == "\\2a"

    def test_escapes_parentheses(self):
        result = escape_filter_chars(")(uid=*")
        assert "(" not in result
        assert ")" not in result

    def test_clean_username_unchanged(self):
        result = escape_filter_chars("jsmith")
        assert result == "jsmith"

    def test_escapes_null_byte(self):
        result = escape_filter_chars("admin\x00")
        assert "\x00" not in result
        # Verify the NUL was escaped to the expected LDAP filter hex form
        assert "\\00" in result.lower(), (
            f"Expected escaped NUL (\\00) in {result!r}"
        )


class TestDefaultLoginHandlerImports:
    """Verify that the login handler imports the sanitisation helpers."""

    def test_escape_rdn_value_imported(self):
        from app.service.login_handlers.default import escape_rdn_value as _erv
        assert callable(_erv)

    def test_escape_filter_chars_imported(self):
        from app.service.login_handlers.default import escape_filter_chars as _efc
        assert callable(_efc)


class TestLdapLoginEscapesUsername:
    """_ldap_login must use escape_rdn_value so injection chars are neutralised
    before the username is inserted into the bind DN."""

    def _make_handler(self, ldap_config):
        from app.service.login_handlers.default import DefaultLoginHandler
        services = MagicMock()
        handler = object.__new__(DefaultLoginHandler)
        handler.services = services
        handler.log = MagicMock()
        handler._ldap_config = ldap_config
        return handler

    @pytest.mark.asyncio
    async def test_ldap_login_escapes_comma_in_username(self):
        """A username containing a comma must be escaped in the bind DN."""
        config = {
            'server': 'ldap://localhost',
            'dn': 'dc=example,dc=com',
            'user_attr': 'uid',
        }
        handler = self._make_handler(config)

        captured_users = []

        def fake_connection(server, user, password):
            captured_users.append(user)
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.bind.return_value = False
            return conn

        with patch('app.service.login_handlers.default.ldap3.Connection', side_effect=fake_connection):
            with patch('app.service.login_handlers.default.ldap3.Server', return_value=MagicMock()):
                await handler._ldap_login("admin,dc=evil", "pass")

        assert len(captured_users) == 1
        bind_dn = captured_users[0]
        # The raw comma from the username must NOT appear unescaped in the bind DN
        # (the dn portion dc=example,dc=com will still have commas, but the
        # injected comma must be escaped).
        username_part = bind_dn.split(",dc=example")[0]
        assert "admin,dc=evil" not in username_part, (
            f"Unescaped injection username found in bind DN: {bind_dn!r}"
        )

    @pytest.mark.asyncio
    async def test_ldap_get_group_escapes_username_in_filter(self):
        """_ldap_get_group must escape the username before inserting it into
        the LDAP search filter."""
        config = {
            'server': 'ldap://localhost',
            'dn': 'dc=example,dc=com',
            'user_attr': 'uid',
            'group_attr': 'objectClass',
            'red_group': 'red',
        }
        handler = self._make_handler(config)

        captured_filters = []

        mock_conn = MagicMock()
        mock_entry = MagicMock()
        mock_entry.__getitem__ = MagicMock(return_value=MagicMock(value='blue'))
        mock_conn.entries = [mock_entry]

        def fake_search(dn, search_filter, attributes=None):
            captured_filters.append(search_filter)

        mock_conn.search = fake_search

        await handler._ldap_get_group(mock_conn, 'dc=example,dc=com', "user)(uid=*", "uid")

        assert len(captured_filters) == 1
        filt = captured_filters[0]
        # The raw injection string must not appear unescaped in the filter
        assert ")(uid=*" not in filt, (
            f"Unescaped injection characters found in search filter: {filt!r}"
        )
