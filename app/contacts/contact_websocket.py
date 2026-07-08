import asyncio
import websockets
from websockets.exceptions import ConnectionClosed

from app.utility.base_world import BaseWorld
from app.utility.config_util import verify_hash

_HEADER_API_KEY = 'KEY'
_CONFIG_API_KEYS = ('api_key_red', 'api_key_blue')


class Contact(BaseWorld):

    def __init__(self, services):
        self.name = 'websocket'
        self.description = 'Accept data through web sockets'
        self.log = self.create_logger('contact_websocket')
        self.log.level = 100
        self.handler = Handler(services)
        self.stop_future = asyncio.Future()

    async def start(self):
        web_socket = self.get_config('app.contact.websocket')
        try:
            async with websockets.serve(self.handler.handle, *web_socket.split(':'), logger=self.log):
                await self.stop_future

        except OSError as e:
            self.log.error("WebSocket error: {}".format(e))

    async def stop(self):
        self.stop_future.set_result('')


class Handler:

    def __init__(self, services):
        self.services = services
        self.handles = []
        self.log = BaseWorld.create_logger('websocket_handler')

    def _is_authenticated(self, connection):
        """Return True if the WebSocket upgrade request carries a valid API key."""
        provided = connection.request.headers.get(_HEADER_API_KEY, '')
        for key_name in _CONFIG_API_KEYS:
            stored = BaseWorld.get_config(key_name)
            if stored:
                try:
                    if verify_hash(stored, provided):
                        return True
                except Exception:
                    self.log.warning('Hash verification failed for config key %s', key_name)
                    return False
        return False

    async def handle(self, connection):
        try:
            if not self._is_authenticated(connection):
                await connection.close(1008, 'Unauthorized')
                return
            path = connection.request.path
            for handle in [h for h in self.handles if path.split('/', 1)[1].startswith(h.tag)]:
                await handle.run(connection, path, self.services)
        except ConnectionClosed:
            pass
        except Exception as e:
            self.log.exception('Unexpected error handling WebSocket connection: %s', e)
            try:
                await connection.close(1011, 'Internal error')
            except Exception:
                pass
