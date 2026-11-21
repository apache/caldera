"""Tests for per-link file encoding via x-link-id header (issue #3274)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from base64 import b64encode

from app.objects.secondclass.c_link import Link, LinkSchema
from app.service.file_svc import FileSvc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_link(link_id, file_encoding=None, command='ls', paw='abc123'):
    """Return a minimal Link object."""
    link = Link(
        command=b64encode(command.encode()).decode(),
        paw=paw,
        id=link_id,
        file_encoding=file_encoding,
    )
    return link


def _make_data_svc(operations):
    """Return an async mock DataService that returns *operations* from locate()."""
    data_svc = MagicMock()
    data_svc.locate = AsyncMock(return_value=operations)
    return data_svc


def _make_operation(links):
    op = MagicMock()
    op.chain = links
    return op


# ---------------------------------------------------------------------------
# Link schema / serialisation tests
# ---------------------------------------------------------------------------

class TestLinkFileEncodingField:

    def test_file_encoding_defaults_to_none(self):
        link = Link(command='', paw='abc', id='link-1')
        assert link.file_encoding is None

    def test_file_encoding_can_be_set(self):
        link = _make_link('link-42', file_encoding='base64')
        assert link.file_encoding == 'base64'

    def test_link_schema_serialises_file_encoding(self):
        link = _make_link('link-s1', file_encoding='plain-text')
        dumped = LinkSchema().dump(link)
        assert dumped.get('file_encoding') == 'plain-text'

    def test_link_schema_serialises_none_file_encoding(self):
        link = _make_link('link-s2', file_encoding=None)
        dumped = LinkSchema().dump(link)
        # field present with None value (marshmallow dumps it as None)
        assert 'file_encoding' in dumped
        assert dumped['file_encoding'] is None

    def test_link_schema_deserialises_file_encoding(self):
        raw = {
            'command': b64encode(b'whoami').decode(),
            'paw': 'xyz',
            'file_encoding': 'base64',
        }
        link = LinkSchema().load(raw)
        assert link.file_encoding == 'base64'

    def test_link_schema_deserialises_missing_file_encoding_as_none(self):
        raw = {
            'command': b64encode(b'whoami').decode(),
            'paw': 'xyz',
        }
        link = LinkSchema().load(raw)
        assert link.file_encoding is None

    def test_display_includes_file_encoding(self, init_base_world):
        link = _make_link('link-d1', file_encoding='plain-text')
        display = link.display
        assert display.get('file_encoding') == 'plain-text'


# ---------------------------------------------------------------------------
# FileSvc.get_file_encoding() tests
# ---------------------------------------------------------------------------

class TestGetFileEncoding:

    @pytest.fixture
    def file_svc(self):
        """Create a minimal FileSvc without full __init__ (avoids config dependency)."""
        svc = object.__new__(FileSvc)
        svc.data_svc = None
        return svc

    # -- x-link-id resolution -----------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_link_encoding_when_x_link_id_matches(self, file_svc):
        link = _make_link('link-abc', file_encoding='plain-text')
        op = _make_operation([link])
        file_svc.data_svc = _make_data_svc([op])

        result = await file_svc.get_file_encoding({'x-link-id': 'link-abc'})

        assert result == 'plain-text'

    @pytest.mark.asyncio
    async def test_skips_link_with_no_file_encoding(self, file_svc):
        """When the matched link has no file_encoding, fall through to header."""
        link = _make_link('link-noenc', file_encoding=None)
        op = _make_operation([link])
        file_svc.data_svc = _make_data_svc([op])

        result = await file_svc.get_file_encoding({'x-link-id': 'link-noenc', 'x-file-encoding': 'base64'})

        assert result == 'base64'

    @pytest.mark.asyncio
    async def test_skips_unmatched_links(self, file_svc):
        link = _make_link('link-other', file_encoding='plain-text')
        op = _make_operation([link])
        file_svc.data_svc = _make_data_svc([op])

        result = await file_svc.get_file_encoding({'x-link-id': 'link-unknown', 'x-file-encoding': 'base64'})

        assert result == 'base64'

    @pytest.mark.asyncio
    async def test_searches_multiple_operations(self, file_svc):
        link_a = _make_link('link-A', file_encoding=None)
        link_b = _make_link('link-B', file_encoding='plain-text')
        op1 = _make_operation([link_a])
        op2 = _make_operation([link_b])
        file_svc.data_svc = _make_data_svc([op1, op2])

        result = await file_svc.get_file_encoding({'x-link-id': 'link-B'})

        assert result == 'plain-text'

    # -- x-file-encoding fallback --------------------------------------------

    @pytest.mark.asyncio
    async def test_falls_back_to_x_file_encoding_when_no_x_link_id(self, file_svc):
        file_svc.data_svc = _make_data_svc([])
        result = await file_svc.get_file_encoding({'x-file-encoding': 'base64'})
        assert result == 'base64'

    @pytest.mark.asyncio
    async def test_falls_back_to_x_file_encoding_without_data_svc(self, file_svc):
        file_svc.data_svc = None
        result = await file_svc.get_file_encoding({'x-file-encoding': 'plain-text'})
        assert result == 'plain-text'

    # -- no-header fallback --------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_none_when_no_headers(self, file_svc):
        file_svc.data_svc = None
        result = await file_svc.get_file_encoding({})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_only_x_link_id_but_no_data_svc(self, file_svc):
        file_svc.data_svc = None
        result = await file_svc.get_file_encoding({'x-link-id': 'link-xyz'})
        assert result is None
