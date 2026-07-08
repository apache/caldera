import pytest
from unittest.mock import AsyncMock, MagicMock

from app.contacts.contact_bits import Contact as BitsContact, BITS_PROTOCOL_GUID, MAX_FRAGMENT_SIZE
from app.utility.base_world import BaseWorld


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def base_world():
    BaseWorld.clear_config()
    BaseWorld.apply_config(name='main', config={
        'app.contact.bits': '/bits',
        'exfil_dir': '/tmp/caldera',
        'plugins': ['sandcat', 'stockpile'],
        'crypt_salt': 'BLAH',
        'api_key': 'ADMIN123',
        'encryption_key': 'ADMIN123',
    })
    BaseWorld.apply_config(name='agents', config={
        'sleep_max': 5,
        'sleep_min': 5,
        'untrusted_timer': 90,
        'watchdog': 0,
        'implant_name': 'splunkd',
        'bootstrap_abilities': ['43b3754c-def4-4699-a673-1d85648fda6a'],
    })
    yield BaseWorld
    BaseWorld.clear_config()


@pytest.fixture
def bits_contact(app_svc, base_world, contact_svc, data_svc, file_svc):
    services = app_svc.get_services()
    contact = BitsContact(services=services)
    return contact


# ---------------------------------------------------------------------------
# Helper: build a minimal fake aiohttp request
# ---------------------------------------------------------------------------

def make_request(headers=None, body=b''):
    req = MagicMock()
    req.headers = headers or {}
    req.read = AsyncMock(return_value=body)
    return req


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBitsContactInit:

    def test_name_and_description(self, bits_contact):
        assert bits_contact.name == 'bits'
        assert bits_contact.description == 'Accept data through BITS (Background Intelligent Transfer Service)'

    def test_sessions_initially_empty(self, bits_contact):
        assert bits_contact.sessions == {}


class TestBitsSessionCreation:

    async def test_head_returns_protocol_header(self, bits_contact):
        req = make_request()
        response = await bits_contact._create_session(req)
        assert response.status == 200
        assert BITS_PROTOCOL_GUID in response.headers.get('BITS-Supported-Protocols', '')

    async def test_head_returns_session_id(self, bits_contact):
        req = make_request()
        response = await bits_contact._create_session(req)
        session_id = response.headers.get('X-Session-Id')
        assert session_id is not None
        assert session_id in bits_contact.sessions

    async def test_head_uses_x_filename_header(self, bits_contact):
        req = make_request(headers={'X-Filename': 'loot.txt'})
        response = await bits_contact._create_session(req)
        session_id = response.headers.get('X-Session-Id')
        assert bits_contact.sessions[session_id]['filename'] == 'loot.txt'

    async def test_head_defaults_filename_to_session_id(self, bits_contact):
        req = make_request()
        response = await bits_contact._create_session(req)
        session_id = response.headers.get('X-Session-Id')
        assert bits_contact.sessions[session_id]['filename'] == session_id


class TestBitsFragmentUpload:

    async def test_upload_unknown_session_returns_400(self, bits_contact):
        req = make_request(headers={'X-Session-Id': 'nonexistent'})
        response = await bits_contact._upload_fragment(req)
        assert response.status == 400

    async def test_upload_no_session_id_returns_400(self, bits_contact):
        req = make_request(headers={})
        response = await bits_contact._upload_fragment(req)
        assert response.status == 400

    async def test_fragment_too_large_returns_413(self, bits_contact):
        # Create a session first
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')

        req = make_request(headers={
            'X-Session-Id': session_id,
            'Content-Range': 'bytes 0-1023/1024',
            'Content-Length': str(MAX_FRAGMENT_SIZE + 1),
        })
        response = await bits_contact._upload_fragment(req)
        assert response.status == 413

    async def test_upload_fragment_stored(self, bits_contact):
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')

        payload = b'hello world'
        req = make_request(
            headers={
                'X-Session-Id': session_id,
                'Content-Range': f'bytes 0-{len(payload)-1}/*',
                'Content-Length': str(len(payload)),
            },
            body=payload,
        )
        response = await bits_contact._upload_fragment(req)
        assert response.status == 200
        # Session still alive because total_length not known yet
        assert session_id in bits_contact.sessions
        assert bits_contact.sessions[session_id]['fragments'][0] == payload

    async def test_upload_missing_content_range_returns_400(self, bits_contact):
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')

        req = make_request(headers={
            'X-Session-Id': session_id,
            'Content-Length': '5',
        })
        response = await bits_contact._upload_fragment(req)
        assert response.status == 400

    async def test_complete_single_fragment_triggers_save(self, bits_contact):
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')

        payload = b'complete data'
        req = make_request(
            headers={
                'X-Session-Id': session_id,
                'Content-Range': f'bytes 0-{len(payload)-1}/{len(payload)}',
                'Content-Length': str(len(payload)),
            },
            body=payload,
        )

        bits_contact.file_svc.save_file = AsyncMock(return_value=None)
        response = await bits_contact._upload_fragment(req)
        assert response.status == 200
        # Session should be removed after complete upload
        assert session_id not in bits_contact.sessions
        bits_contact.file_svc.save_file.assert_called_once()

    async def test_reassembly_from_multiple_fragments(self, bits_contact):
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')

        part1 = b'hello '
        part2 = b'world'
        total = len(part1) + len(part2)

        # First fragment (no total length yet)
        req1 = make_request(
            headers={
                'X-Session-Id': session_id,
                'Content-Range': f'bytes 0-{len(part1)-1}/*',
                'Content-Length': str(len(part1)),
            },
            body=part1,
        )
        bits_contact.file_svc.save_file = AsyncMock(return_value=None)
        await bits_contact._upload_fragment(req1)
        # Not yet complete
        assert session_id in bits_contact.sessions

        # Second (final) fragment with total length
        req2 = make_request(
            headers={
                'X-Session-Id': session_id,
                'Content-Range': f'bytes {len(part1)}-{total-1}/{total}',
                'Content-Length': str(len(part2)),
            },
            body=part2,
        )
        await bits_contact._upload_fragment(req2)
        # Complete – session gone
        assert session_id not in bits_contact.sessions
        bits_contact.file_svc.save_file.assert_called_once()
        # Verify assembled data
        _name, assembled_bytes, *_ = bits_contact.file_svc.save_file.call_args[0]
        assert assembled_bytes == b'hello world'


class TestBitsSessionCancel:

    async def test_delete_removes_session(self, bits_contact):
        create_req = make_request()
        create_resp = await bits_contact._create_session(create_req)
        session_id = create_resp.headers.get('X-Session-Id')
        assert session_id in bits_contact.sessions

        delete_req = make_request(headers={'X-Session-Id': session_id})
        response = await bits_contact._cancel_session(delete_req)
        assert response.status == 200
        assert session_id not in bits_contact.sessions

    async def test_delete_unknown_session_returns_200(self, bits_contact):
        req = make_request(headers={'X-Session-Id': 'unknown-id'})
        response = await bits_contact._cancel_session(req)
        assert response.status == 200

    async def test_delete_no_session_id_returns_200(self, bits_contact):
        req = make_request(headers={})
        response = await bits_contact._cancel_session(req)
        assert response.status == 200


class TestParseContentRange:

    def test_valid_range_with_total(self):
        offset, total = BitsContact._parse_content_range('bytes 0-1023/4096')
        assert offset == 0
        assert total == 4096

    def test_valid_range_without_total(self):
        offset, total = BitsContact._parse_content_range('bytes 1024-2047/*')
        assert offset == 1024
        assert total is None

    def test_invalid_range_returns_none(self):
        offset, total = BitsContact._parse_content_range('invalid header')
        assert offset is None
        assert total is None

    def test_empty_string_returns_none(self):
        offset, total = BitsContact._parse_content_range('')
        assert offset is None
        assert total is None

    def test_none_returns_none(self):
        offset, total = BitsContact._parse_content_range(None)
        assert offset is None
        assert total is None


class TestTryAssemble:

    def test_single_complete_fragment(self):
        session = {
            'fragments': {0: b'hello world'},
            'total_length': 11,
        }
        result = BitsContact._try_assemble(session)
        assert result == b'hello world'

    def test_two_fragments_complete(self):
        session = {
            'fragments': {0: b'hello ', 6: b'world'},
            'total_length': 11,
        }
        result = BitsContact._try_assemble(session)
        assert result == b'hello world'

    def test_incomplete_returns_none(self):
        session = {
            'fragments': {0: b'hello '},
            'total_length': 11,
        }
        result = BitsContact._try_assemble(session)
        assert result is None

    def test_overlapping_fragments_do_not_falsely_complete(self):
        """Duplicate/overlapping fragments must not inflate covered count."""
        session = {
            'fragments': {0: b'hello '},  # only 6 unique bytes covered, not 11
            'total_length': 11,
        }
        result = BitsContact._try_assemble(session)
        assert result is None


class TestParseContentRangeEdgeCases:

    def test_negative_start_rejected(self):
        offset, total = BitsContact._parse_content_range('bytes -1-5/10')
        assert offset is None

    def test_end_less_than_start_rejected(self):
        offset, total = BitsContact._parse_content_range('bytes 5-3/10')
        assert offset is None

    def test_end_equals_total_rejected(self):
        """end must be strictly less than total_length."""
        offset, total = BitsContact._parse_content_range('bytes 0-10/10')
        assert offset is None

    def test_valid_range_accepted(self):
        offset, total = BitsContact._parse_content_range('bytes 0-9/10')
        assert offset == 0
        assert total == 10
