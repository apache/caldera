import abc
import json
import logging

from aiohttp import web

from app.api.v2.handlers.base_api import BaseApi
from app.api.v2.responses import JsonHttpBadRequest, JsonHttpForbidden, JsonHttpNotFound

_schema_warning_logger = logging.getLogger('caldera.schema')


def load_schema_with_warning(schema, data):
    """Load data through a schema and warn if unknown fields are silently dropped."""
    result = schema.load(data)
    if isinstance(data, dict):
        unknown_fields = set(data.keys()) - set(schema.fields.keys())
        if unknown_fields:
            # Cap logged names to avoid log-noise and limit information leakage
            # when clients supply many or very long unknown keys.
            _MAX_LOG_FIELDS = 5
            _MAX_KEY_LEN = 64
            logged = sorted(
                k[:_MAX_KEY_LEN] for k in list(unknown_fields)[:_MAX_LOG_FIELDS]
            )
            omitted = max(0, len(unknown_fields) - _MAX_LOG_FIELDS)
            suffix = f' (and {omitted} more)' if omitted else ''
            _schema_warning_logger.warning(
                'Schema [%s] silently dropped unknown fields: %s%s',
                schema.__class__.__name__, logged, suffix
            )
    return result


class BaseObjectApi(BaseApi):
    def __init__(self, description, obj_class, schema, ram_key, id_property, auth_svc, logger=None):
        super().__init__(auth_svc=auth_svc, logger=logger)

        self.description = description
        self.obj_class = obj_class
        self.schema = schema
        self.ram_key = ram_key
        self.id_property = id_property

        self._api_manager = None

    @abc.abstractmethod
    def add_routes(self, app: web.Application):
        raise NotImplementedError

    async def get_all_objects(self, request: web.Request):
        access = await self.get_request_permissions(request)

        sort = request['querystring'].get('sort', 'name')
        include = request['querystring'].get('include')
        exclude = request['querystring'].get('exclude')

        return self._api_manager.find_and_dump_objects(self.ram_key, access, sort, include, exclude)

    async def get_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)

        obj = self._api_manager.find_object(self.ram_key, query)
        if not obj:
            raise JsonHttpNotFound(f'{self.description.capitalize()} not found: {obj_id}')
        elif obj.access not in access['access']:
            raise JsonHttpForbidden(f'Cannot view {self.description} due to insufficient permissions: {obj_id}')

        include = request['querystring'].get('include')
        exclude = request['querystring'].get('exclude')

        return self._api_manager.dump_object_with_filters(obj, include, exclude)

    async def create_object(self, request: web.Request):
        data = await request.json()

        await self._error_if_object_with_id_exists(data.get(self.id_property))

        access = await self.get_request_permissions(request)
        return self._api_manager.create_object_from_schema(self.schema, data, access)

    async def create_on_disk_object(self, request: web.Request):
        data = await request.json()

        await self._error_if_object_with_id_exists(data.get(self.id_property))

        access = await self.get_request_permissions(request)
        obj = await self._api_manager.create_on_disk_object(data, access, self.ram_key, self.id_property,
                                                            self.obj_class)
        return obj

    async def _error_if_object_with_id_exists(self, obj_id: str):
        """Throw an error if an object (of the same type) exists with the given id"""
        if obj_id:
            search = {self.id_property: obj_id}
            if self._api_manager.find_object(self.ram_key, search):
                raise JsonHttpBadRequest(f'{self.description.capitalize()} with given id already exists: {obj_id}')

    async def update_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)

        obj = self._api_manager.find_and_update_object(self.ram_key, data, search)
        if not obj:
            raise JsonHttpNotFound(f'{self.description.capitalize()} not found: {obj_id}')
        return obj

    async def update_on_disk_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)

        obj = await self._api_manager.find_and_update_on_disk_object(data, search, self.ram_key, self.id_property,
                                                                     self.obj_class)
        if not obj:
            raise JsonHttpNotFound(f'{self.description.capitalize()} not found: {obj_id}')
        return obj

    async def create_or_update_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)

        matched_obj = self._api_manager.find_object(self.ram_key, query)
        if matched_obj and matched_obj.access not in access['access']:
            raise JsonHttpForbidden(f'Cannot update {self.description} due to insufficient permissions: {obj_id}')

        return self._api_manager.create_object_from_schema(self.schema, data, access)

    async def create_or_update_on_disk_object(self, request: web.Request):
        data, access, obj_id, query, search = await self._parse_common_data_from_request(request)

        matched_obj = self._api_manager.find_object(self.ram_key, query)
        if not matched_obj:
            obj = await self._api_manager.create_on_disk_object(data, access, self.ram_key, self.id_property,
                                                                self.obj_class)
        else:
            if matched_obj.access in access['access']:
                obj = await self._api_manager.replace_on_disk_object(matched_obj, data, self.ram_key, self.id_property)
            else:
                raise JsonHttpForbidden(f'Cannot update {self.description} due to insufficient permissions: {obj_id}')

        return obj

    async def delete_object(self, request: web.Request):
        obj_id = request.match_info.get(self.id_property)

        access = await self.get_request_permissions(request)
        query = {self.id_property: obj_id}
        search = {**query, **access}

        if not self._api_manager.find_object(self.ram_key, search):
            raise JsonHttpNotFound(f'{self.description.capitalize()} not found: {obj_id}')

        await self._api_manager.remove_object_from_memory_by_id(identifier=obj_id, ram_key=self.ram_key,
                                                                id_property=self.id_property)

    async def delete_on_disk_object(self, request: web.Request):
        await self.delete_object(request)

        obj_id = request.match_info.get(self.id_property)
        await self._api_manager.remove_object_from_disk_by_id(identifier=obj_id, ram_key=self.ram_key)

    async def _parse_common_data_from_request(self, request) -> (dict, dict, str, dict, dict):
        data = {}
        raw_body = await request.read()
        if raw_body:
            data = json.loads(raw_body)

        obj_id = request.match_info.get(self.id_property, '')
        if obj_id:
            data[self.id_property] = obj_id

        access = await self.get_request_permissions(request)
        query = {self.id_property: obj_id}
        search = {**query, **access}

        return data, access, obj_id, query, search
