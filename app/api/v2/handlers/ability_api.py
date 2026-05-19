import aiohttp_apispec
from aiohttp import web

from app.api.v2.handlers.base_object_api import BaseObjectApi
from app.api.v2.managers.ability_api_manager import AbilityApiManager
from app.api.v2.schemas.base_schemas import BaseGetAllQuerySchema, BaseGetOneQuerySchema
from app.objects.c_ability import Ability, AbilitySchema


class AbilityApi(BaseObjectApi):
    def __init__(self, services):
        super().__init__(description='ability', obj_class=Ability, schema=AbilitySchema, ram_key='abilities',
                         id_property='ability_id', auth_svc=services['auth_svc'])
        self._api_manager = AbilityApiManager(data_svc=services['data_svc'], file_svc=services['file_svc'])

    def add_routes(self, app: web.Application):
        router = app.router
        router.add_get('/abilities', self.get_abilities)
        router.add_get('/abilities/{ability_id}', self.get_ability_by_id)
        router.add_post('/abilities', self.create_ability)
        router.add_put('/abilities/{ability_id}', self.create_or_update_ability)
        router.add_patch('/abilities/{ability_id}', self.update_ability)
        router.add_delete('/abilities/{ability_id}', self.delete_ability)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Get all abilities.',
                          description='Provides a list of all available abilities.')
    @aiohttp_apispec.querystring_schema(BaseGetAllQuerySchema)
    @aiohttp_apispec.response_schema(AbilitySchema(many=True, partial=True),
                                     description='Returns a list of all abilities.')
    async def get_abilities(self, request: web.Request):
        abilities = await self.get_all_objects(request)
        return web.json_response(abilities)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Get an ability.',
                          description='Provides one ability based on its ability id.',
                          parameters=[{
                              'in': 'path',
                              'name': 'ability_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Ability to be retrieved'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.response_schema(AbilitySchema(partial=True),
                                     description='JSON dictionary representation of the existing Ability.')
    async def get_ability_by_id(self, request: web.Request):
        ability = await self.get_object(request)
        return web.json_response(ability)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Creates a new ability.',
                          description='Creates a new ability based on the `AbilitySchema`. '
                                      '"name", "tactic", "technique_name", "technique_id" and "executors" are all required fields.')
    @aiohttp_apispec.request_schema(AbilitySchema)
    @aiohttp_apispec.response_schema(AbilitySchema,
                                     description='JSON dictionary representation of the created Ability.')
    async def create_ability(self, request: web.Request):
        ability = await self.create_on_disk_object(request)
        return web.json_response(ability.display)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Replaces an existing ability.',
                          description='Replaces an ability based on the `AbilitySchema` values provided '
                                      'in the message body. "name", "tactic", and "executors" '
                                      'are all required fields.',
                          parameters=[{
                              'in': 'path',
                              'name': 'ability_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Ability to be retrieved'
                          }])
    @aiohttp_apispec.request_schema(AbilitySchema(partial=True))
    @aiohttp_apispec.response_schema(AbilitySchema,
                                     description='JSON dictionary representation of the replaced Ability.')
    async def create_or_update_ability(self, request: web.Request):
        ability = await self.create_or_update_on_disk_object(request)
        return web.json_response(ability.display)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Updates an existing ability.',
                          description='Updates an ability based on the `AbilitySchema` values provided '
                                      'in the message body.',
                          parameters=[{
                              'in': 'path',
                              'name': 'ability_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Ability to be retrieved'
                          }])
    @aiohttp_apispec.request_schema(AbilitySchema(partial=True,
                                                  exclude=['ability_id',
                                                           'requirements',
                                                           'additional_info',
                                                           'access']))
    @aiohttp_apispec.response_schema(AbilitySchema,
                                     description='JSON dictionary representation of the replaced Ability.')
    async def update_ability(self, request: web.Request):
        ability = await self.update_on_disk_object(request)
        return web.json_response(ability.display)

    @aiohttp_apispec.docs(tags=['abilities'], summary='Deletes an ability.',
                          description='Deletes an existing ability.',
                          parameters=[{
                              'in': 'path',
                              'name': 'ability_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Ability to be retrieved'
                          }])
    @aiohttp_apispec.response_schema(AbilitySchema, code=204,
                                     description='HTTP 204 Status Code (No Content)')
    async def delete_ability(self, request: web.Request):
        await self.delete_on_disk_object(request)
        raise web.HTTPNoContent()
