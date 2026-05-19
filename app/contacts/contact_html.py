import json

from aiohttp_jinja2 import template

from app.utility.base_world import BaseWorld


class Contact(BaseWorld):

    def __init__(self, services):
        self.name = 'html'
        self.description = 'Accept beacons through an HTML page'
        self.app_svc = services.get('app_svc')
        self.contact_svc = services.get('contact_svc')

    async def start(self):
        self.app_svc.application.router.add_route('*', self.get_config('app.contact.html'), self._accept_beacon)

    async def _beacon_helper(self, request):
        try:
            profile = json.loads(self.decode_bytes(await request.text()))
            profile['paw'] = profile.get('paw')
            profile['contact'] = 'html'
            agent, instructions = await self.contact_svc.handle_heartbeat(**profile)
            response = dict(paw=agent.paw,
                            sleep=await agent.calculate_sleep(),
                            watchdog=agent.watchdog,
                            instructions=json.dumps([json.dumps(i.display) for i in instructions]))
            return dict(instructions=self.encode_string(json.dumps(response)))
        except Exception:
            return dict(instructions=[])

    @template('weather.html')
    async def _accept_beacon(self, request):
        return await self._beacon_helper(request)
