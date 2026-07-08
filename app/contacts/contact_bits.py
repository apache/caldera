import os
import tempfile
import uuid

from aiohttp import web

from app.utility.base_world import BaseWorld

BITS_PROTOCOL_GUID = '{7df0354d-249b-430f-820d-3d2a9bef4931}'
# Fragment size limit.  Note: aiohttp enforces client_max_size (default 1 MB)
# at the transport layer, so fragments larger than client_max_size will be
# rejected before reaching the handler.  Adjust the server's client_max_size
# (or mount on a sub-app with a higher limit) if larger fragments are needed.
MAX_FRAGMENT_SIZE = 1 * 1024 * 1024  # 1 MB — aligned with aiohttp default client_max_size


class Contact(BaseWorld):

    def __init__(self, services):
        self.name = 'bits'
        self.description = 'Accept data through BITS (Background Intelligent Transfer Service)'
        self.app_svc = services.get('app_svc')
        self.contact_svc = services.get('contact_svc')
        self.file_svc = services.get('file_svc')
        self.log = self.create_logger('contact_bits')
        self.sessions = {}  # session_id -> {fragments: {offset: bytes}, total_length: int, filename: str}

    async def start(self):
        self.app_svc.application.router.add_route('HEAD', '/bits', self._create_session)
        self.app_svc.application.router.add_route('BITS_POST', '/bits', self._upload_fragment)
        self.app_svc.application.router.add_route('DELETE', '/bits', self._cancel_session)

    async def _create_session(self, request):
        """Handle BITS session creation via HEAD request."""
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            'fragments': {},
            'total_length': None,
            'filename': request.headers.get('X-Filename', session_id)
        }
        self.log.debug('BITS session created: %s', session_id)
        return web.Response(
            status=200,
            headers={
                'BITS-Supported-Protocols': BITS_PROTOCOL_GUID,
                'X-Session-Id': session_id,
            }
        )

    async def _upload_fragment(self, request):
        """Handle BITS fragment upload via BITS_POST request."""
        session_id = request.headers.get('X-Session-Id')
        if not session_id or session_id not in self.sessions:
            self.log.warning('BITS fragment upload for unknown session: %s', session_id)
            return web.Response(
                status=400,
                headers={'BITS-Error-Code': '0x80200004'},
                reason='Unknown session'
            )

        content_range = request.headers.get('Content-Range', '')
        try:
            content_length = int(request.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            return web.Response(
                status=400,
                headers={'BITS-Error-Code': '0x80200004'},
                reason='Invalid Content-Length'
            )

        if content_length > MAX_FRAGMENT_SIZE:
            self.log.warning('BITS fragment too large: %d bytes (session %s)', content_length, session_id)
            return web.Response(
                status=413,
                headers={'BITS-Error-Code': '0x80200020'},
                reason='Fragment too large'
            )

        # Parse Content-Range: bytes <start>-<end>/<total>
        fragment_offset, total_length = self._parse_content_range(content_range)
        if fragment_offset is None:
            self.log.warning('BITS missing or invalid Content-Range header (session %s)', session_id)
            return web.Response(
                status=400,
                headers={'BITS-Error-Code': '0x80200004'},
                reason='Invalid Content-Range'
            )

        data = await request.read()
        session = self.sessions[session_id]
        session['fragments'][fragment_offset] = data
        if total_length is not None:
            session['total_length'] = total_length

        self.log.debug('BITS fragment received: session=%s offset=%d size=%d total=%s',
                       session_id, fragment_offset, len(data), total_length)

        # Check whether all fragments have been received
        if session['total_length'] is not None:
            assembled = self._try_assemble(session)
            if assembled is not None:
                # Remove the session before awaiting save to prevent interleaving
                completed = self.sessions.pop(session_id, None)
                if completed is not None:
                    try:
                        await self._save_upload(session_id, completed['filename'], assembled)
                        self.log.debug('BITS upload complete for session %s', session_id)
                    except Exception as e:
                        self.log.error('Failed to persist BITS upload for session %s: %s', session_id, e)
                        return web.Response(status=500, reason='Failed to save upload')

        return web.Response(status=200)

    async def _cancel_session(self, request):
        """Handle BITS session cancellation via DELETE request."""
        session_id = request.headers.get('X-Session-Id')
        if session_id and session_id in self.sessions:
            del self.sessions[session_id]
            self.log.debug('BITS session cancelled: %s', session_id)
        return web.Response(status=200)

    async def _save_upload(self, session_id, filename, data):
        """Write the completed upload to the exfil directory.

        Raises on failure so callers can propagate a non-200 response.
        """
        exfil_dir = self.get_config('exfil_dir') or os.path.join(tempfile.gettempdir(), 'caldera')
        safe_filename = ''.join(c for c in filename if c.isalnum() or c in '._- ').rstrip()
        safe_filename = os.path.basename(safe_filename)
        if not safe_filename or safe_filename in ('.', '..'):
            safe_filename = session_id
        await self.file_svc.save_file(safe_filename, data, exfil_dir, encrypt=False)
        self.log.info('BITS upload saved: %s (%d bytes)', safe_filename, len(data))

    @staticmethod
    def _parse_content_range(header):
        """Parse Content-Range header, return (offset, total_length) or (None, None) on error.

        Expected format: bytes <start>-<end>/<total>  or  bytes <start>-<end>/*
        """
        try:
            # e.g. "bytes 0-1023/4096"
            if not header or not header.startswith('bytes '):
                return None, None
            range_part = header[len('bytes '):]
            range_section, total_part = range_part.split('/')
            start_str, end_str = range_section.split('-')
            offset = int(start_str)
            end = int(end_str)
            total_length = None if total_part == '*' else int(total_part)
            # Validate parsed values
            if offset < 0 or end < offset:
                return None, None
            if total_length is not None and (total_length <= 0 or end >= total_length):
                return None, None
            return offset, total_length
        except (ValueError, AttributeError):
            return None, None

    @staticmethod
    def _try_assemble(session):
        """Attempt to reassemble all fragments into a single bytes object.

        Returns the assembled bytes if complete, otherwise None.
        """
        total_length = session['total_length']
        fragments = session['fragments']
        assembled = bytearray(total_length)
        # Check for contiguous coverage from byte 0 to handle overlaps correctly.
        # Walk fragments in offset order and track the high-water mark of covered bytes.
        high_water = 0
        for offset in sorted(fragments.keys()):
            chunk = fragments[offset]
            end = offset + len(chunk)
            if end > total_length:
                chunk = chunk[:total_length - offset]
                end = total_length
            assembled[offset:end] = chunk
            if offset <= high_water:
                high_water = max(high_water, end)
        if high_water >= total_length:
            return bytes(assembled)
        return None
