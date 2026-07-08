import asyncio
import json
import aiohttp_apispec

from aiohttp import web

from app.api.v2.handlers.base_object_api import BaseObjectApi
from app.api.v2.managers.operation_api_manager import OperationApiManager
from app.api.v2.responses import JsonHttpNotFound
from app.api.v2.schemas.base_schemas import BaseGetAllQuerySchema, BaseGetOneQuerySchema
from app.api.v2.schemas.link_result_schema import LinkResultSchema
from app.objects.c_operation import Operation, OperationSchema, OperationSchemaAlt, OperationOutputRequestSchema
from app.objects.secondclass.c_link import LinkSchema


class OperationApi(BaseObjectApi):
    def __init__(self, services):
        super().__init__(description='operation', obj_class=Operation, schema=OperationSchema, ram_key='operations',
                         id_property='id', auth_svc=services['auth_svc'])
        self._api_manager = OperationApiManager(services)

    def add_routes(self, app: web.Application):
        router = app.router
        router.add_get('/operations', self.get_operations)
        router.add_get('/operations/summary', self.get_operations_summary)
        router.add_get('/operations/{id}', self.get_operation_by_id)
        router.add_post('/operations', self.create_operation)
        router.add_patch('/operations/{id}', self.update_operation)
        router.add_delete('/operations/{id}', self.delete_operation)
        router.add_post('/operations/{id}/report', self.get_operation_report)
        router.add_post('/operations/{id}/event-logs', self.get_operation_event_logs)
        router.add_get('/operations/{id}/links', self.get_operation_links)
        router.add_get('/operations/{id}/links/{link_id}', self.get_operation_link)
        router.add_get('/operations/{id}/links/{link_id}/result', self.get_operation_link_result)
        router.add_patch('/operations/{id}/links/{link_id}', self.update_operation_link)
        router.add_post('/operations/{id}/potential-links', self.create_potential_link)
        router.add_get('/operations/{id}/potential-links', self.get_potential_links)
        router.add_get('/operations/{id}/potential-links/{paw}', self.get_potential_links_by_paw)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve operations',
                          description='Retrieve all Caldera operations from memory.  Use fields from the '
                                      '`BaseGetAllQuerySchema` in the request body to filter.')
    @aiohttp_apispec.querystring_schema(BaseGetAllQuerySchema)
    @aiohttp_apispec.response_schema(OperationSchema(many=True, partial=True),
                                     description='The response is a list of all operations.')
    async def get_operations(self, request: web.Request):
        operations = await self.get_all_objects(request)
        return web.json_response(operations)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve an operation by operation id',
                          description='Retrieve one Caldera operation from memory based on the operation id (String '
                                      'UUID).  Use fields from the `BaseGetOneQuerySchema` in the request body to add '
                                      '`include` and `exclude` filters.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Operation object to be retrieved.'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.response_schema(OperationSchema(partial=True),
                                     description='The response is the operation with the specified id, if any.')
    async def get_operation_by_id(self, request: web.Request):
        operation = await self.get_object(request)
        return web.json_response(operation)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve operations (alternate)',
                          description='Retrieve all Caldera operations from memory, with an alternate selection'
                                      ' of properties. Use fields from the `BaseGetAllQuerySchema` in the request'
                                      ' body to filter.')
    @aiohttp_apispec.querystring_schema(BaseGetAllQuerySchema)
    @aiohttp_apispec.response_schema(OperationSchemaAlt(many=True, partial=True),
                                     description='The response is a list of all operations.')
    async def get_operations_summary(self, request: web.Request):
        remove_props = ['chain', 'host_group', 'source', 'visibility']
        operations = await self.get_all_objects(request)

        async def _enrich(op):
            op['agents'] = self._api_manager.get_agents(op)
            op['hosts'] = await self._api_manager.get_hosts(op)
            for prop in remove_props:
                op.pop(prop, None)
            return op

        operations_mod = await asyncio.gather(*(_enrich(op) for op in operations))
        return web.json_response(list(operations_mod))

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Create a new Caldera operation record',
                          description='Create a new Caldera operation using the format provided in the '
                                      '`OperationSchema`. Required schema fields are as follows: "name", '
                                      '"adversary.adversary_id", "planner.id", and "source.id"')
    @aiohttp_apispec.request_schema(OperationSchema)
    @aiohttp_apispec.response_schema(OperationSchema,
                                     description='The response is the newly-created operation report.')
    async def create_operation(self, request: web.Request):
        operation = await self.create_object(request)
        return web.json_response(operation.display)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Update fields within an operation',
                          description='Update one Caldera operation in memory based on the operation id (String '
                                      'UUID). The `state`, `autonomous` and `obfuscator` fields in the operation '
                                      'object may be edited in the request body using the `OperationSchema`.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Operation object to be retrieved.'
                          }])
    @aiohttp_apispec.request_schema(OperationSchema(partial=True, only=['state', 'autonomous', 'obfuscator']))
    @aiohttp_apispec.response_schema(OperationSchema(partial=True),
                                     description='The response is the updated operation, including user modifications.')
    async def update_operation(self, request: web.Request):
        operation = await self.update_object(request)
        return web.json_response(operation.display)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Delete an operation by operation id',
                          description='Delete one Caldera operation from memory based on the operation id (String '
                                      'UUID).',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the Operation object to be retrieved.'
                          }])
    @aiohttp_apispec.response_schema(OperationSchema,
                                     description='There is an empty response from a successful delete request.')
    async def delete_operation(self, request: web.Request):
        await self.delete_object(request)
        knowledge_svc_handle = self._api_manager.knowledge_svc
        await knowledge_svc_handle.delete_fact(criteria=dict(source=request.match_info.get('id')))
        await knowledge_svc_handle.delete_relationship(criteria=dict(origin=request.match_info.get('id')))
        return web.HTTPNoContent()

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Get Operation Report',
                          description='Retrieves the report for a given operation_id.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'operation_id': 'Unique ID for operation',
                              'access': 'Format for report',
                              'output': 'Boolean for Agent Output in report',
                              'schema': {'type': 'string'},
                              'required': 'true'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.request_schema(OperationOutputRequestSchema)
    @aiohttp_apispec.response_schema(OperationOutputRequestSchema)
    async def get_operation_report(self, request: web.Request):
        operation_id = request.match_info.get('id')
        access = await self.get_request_permissions(request)
        output = await self._read_output_parameter_(request)
        report = await self._api_manager.get_operation_report(operation_id, access, output)
        return web.json_response(report)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Get Operation Event Logs',
                          description='Retrieves the event logs for a given operation_id.',
                          parameters=[{
                                'in': 'path',
                                'name': 'id',
                                'operation_id': 'Unique ID for operation',
                                'access': 'Format for report',
                                'output': 'Boolean for Agent Output in report',
                                'schema': {'type': 'string'},
                                'required': 'true'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.request_schema(OperationOutputRequestSchema)
    @aiohttp_apispec.response_schema(OperationOutputRequestSchema)
    async def get_operation_event_logs(self, request: web.Request):
        operation_id = request.match_info.get('id')
        access = await self.get_request_permissions(request)
        output = await self._read_output_parameter_(request)
        report = await self._api_manager.get_operation_event_logs(operation_id, access, output)
        return web.json_response(report)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Get Links from Operation',
                          description='Retrieves all links for a given operation_id.',
                          parameters=[{
                                'in': 'path',
                                'name': 'id',
                                'operation_id': 'Unique ID for operation',
                                'schema': {'type': 'string'},
                                'required': 'true'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetAllQuerySchema)
    @aiohttp_apispec.response_schema(LinkSchema(many=True, partial=True),
                                     description='All links contained in operation with the given `id` (String UUID).')
    async def get_operation_links(self, request: web.Request):
        operation_id = request.match_info.get('id')
        access = await self.get_request_permissions(request)
        links = await self._api_manager.get_operation_links(operation_id, access)
        return web.json_response(links)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve a specified link from an operation',
                          description='Retrieve the link with the provided `link_id` (String UUID) from the operation '
                                      'with the given operation `id` (String UUID). Use fields from the '
                                      '`BaseGetOneQuerySchema` in the request body to add `include` and `exclude` '
                                      'filters.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'String UUID of the Operation containing desired link.'},
                              {
                              'in': 'path',
                              'name': 'link_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'String UUID of the Link with the above operation.'}
                          ])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.response_schema(LinkSchema(partial=True),
                                     description='The link matching the provided `link_id` within the operation '
                                                 'matching `id`. Use fields from the `BaseGetOneQuerySchema` in the '
                                                 'request body to add `include` and `exclude` filters.')
    async def get_operation_link(self, request: web.Request):
        operation_id = request.match_info.get('id')
        link_id = request.match_info.get('link_id')
        access = await self.get_request_permissions(request)
        link = await self._api_manager.get_operation_link(operation_id, link_id, access)
        return web.json_response(link)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve the result of a link',
                          description='Retrieve a dictionary containing a link and its results dictionary based on the operation id (String '
                                      'UUID) and link id (String UUID).  Use fields from the `BaseGetOneQuerySchema` in the '
                                      'request body to add `include` and `exclude` filters.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the operation object to be retrieved.'
                          },
                          {
                              'in': 'path',
                              'name': 'link_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the link object to retrieve results of.'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.response_schema(LinkResultSchema(),
                                     description='Contains a dictionary with the requested link and its results dictionary.')
    async def get_operation_link_result(self, request: web.Request):
        operation_id = request.match_info.get('id')
        link_id = request.match_info.get('link_id')
        access = await self.get_request_permissions(request)
        result = await self._api_manager.get_operation_link_result(operation_id, link_id, access)
        return web.json_response(result)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Update the specified link within an operation',
                          description='Update the `command` (String) or `status` (Integer) field within the link with '
                                      'the provided  `link_id` (String UUID) from the operation with the given '
                                      'operation `id` (String UUID).',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'String UUID of the Operation containing desired link.'},
                              {
                              'in': 'path',
                              'name': 'link_id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'String UUID of the Link with the above operation.'}
                          ])
    @aiohttp_apispec.request_schema(LinkSchema(partial=True, only=['command', 'status']))
    @aiohttp_apispec.response_schema(LinkSchema,
                                     description='The updated link after a successful `PATCH` request.')
    async def update_operation_link(self, request: web.Request):
        operation_id = request.match_info.get('id')
        link_id = request.match_info.get('link_id')
        access = await self.get_request_permissions(request)
        data = await request.json()
        link = await self._api_manager.update_operation_link(operation_id, link_id, data, access)
        return web.json_response(link)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Creates a potential Link',
                          description='Creates a potential link to be executed by an agent. Create a potential Link using '
                                      'the format provided in the `LinkSchema`. The request body requires `paw`, '
                                      '`executor`, and `ability`.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the operation object for the link to be created on.'
                          }])
    @aiohttp_apispec.request_schema(LinkSchema)
    @aiohttp_apispec.response_schema(LinkSchema,
                                     description='Response contains the newly assigned Link object.')
    async def create_potential_link(self, request: web.Request):
        operation_id = request.match_info.get('id')
        access = await self.get_request_permissions(request)
        data = await request.json()
        potential_link = await self._api_manager.create_potential_link(operation_id, data, access)
        return web.json_response(potential_link)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve potential links for an operation.',
                          description='Retrieve all potential links for an operation based on the operation id (String '
                                      'UUID).  Use fields from the `BaseGetAllQuerySchema` in the request body to add '
                                      '`include`, `exclude`, and `sort` filters.',
                          parameters=[{
                              'in': 'path',
                              'name': 'id',
                              'schema': {'type': 'string'},
                              'required': 'true',
                              'description': 'UUID of the operation object to retrieve links for.'
                          }])
    @aiohttp_apispec.querystring_schema(BaseGetAllQuerySchema)
    @aiohttp_apispec.response_schema(LinkSchema(many=True, partial=True),
                                     description='Response contains a list of link objects for the requested id.')
    async def get_potential_links(self, request: web.Request):
        operation_id = request.match_info.get('id')
        access = await self.get_request_permissions(request)
        potential_links = await self._api_manager.get_potential_links(operation_id, access)
        return web.json_response(potential_links)

    @aiohttp_apispec.docs(tags=['operations'],
                          summary='Retrieve potential links for an operation filterd by agent paw (id)',
                          description='Retrieve all potential links for an operation-agent pair based on the operation id (String '
                                      'UUID) and the agent paw (id) (String).  Use fields from the `BaseGetAllQuerySchema` '
                                      'in the request body to add `include`, `exclude`, and `sort` filters.',
                          parameters=[
                              {
                                'in': 'path',
                                'name': 'id',
                                'schema': {'type': 'string'},
                                'required': 'true',
                                'description': 'String UUID of the Operation containing desired links.'},
                              {
                                'in': 'path',
                                'name': 'paw',
                                'schema': {'type': 'string'},
                                'required': 'true',
                                'description': 'Agent paw for the specified operation.'
                              }])
    @aiohttp_apispec.querystring_schema(BaseGetOneQuerySchema)
    @aiohttp_apispec.response_schema(LinkSchema(partial=True),
                                     description='All potential links for operation and the specified agent paw.')
    async def get_potential_links_by_paw(self, request: web.Request):
        operation_id = request.match_info.get('id')
        paw = request.match_info.get('paw')
        access = await self.get_request_permissions(request)
        potential_links = await self._api_manager.get_potential_links(operation_id, access, paw)
        return web.json_response(potential_links)

    async def create_object(self, request: web.Request):
        data = await request.json()
        await self._error_if_object_with_id_exists(data.get(self.id_property))
        access = await self.get_request_permissions(request)
        return await self._api_manager.create_object_from_schema(self.schema, data, access)

    async def update_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)
        obj = await self._api_manager.find_and_update_object(self.ram_key, data, search)
        if not obj:
            raise JsonHttpNotFound(f'{self.description.capitalize()} not found: {obj_id}')
        return obj

    async def _read_output_parameter_(self, request: web.Request):
        raw_body = await request.read()
        output = False
        if raw_body:
            output = json.loads(raw_body).get('enable_agent_output', False)
        return output
