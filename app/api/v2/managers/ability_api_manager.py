import re
import uuid
import os
import yaml
from typing import Any
from app.api.v2.managers.base_api_manager import BaseApiManager
from app.api.v2.responses import JsonHttpBadRequest
from app.objects.c_ability import AbilitySchema
from app.utility.base_world import BaseWorld


class AbilityApiManager(BaseApiManager):
    def __init__(self, data_svc, file_svc):
        super().__init__(data_svc=data_svc, file_svc=file_svc)
    
    async def create_on_disk_object(self, data: dict, access: dict, ram_key: str, id_property: str, obj_class: type):
        self._validate_ability_data(create=True, data=data)
        obj_id = data.get('id')
        file_path = self._create_ability_filepath(data.get('tactic'), obj_id)
        allowed = self._get_allowed_from_access(access)
        await self._save_and_reload_object(file_path, data, obj_class, allowed)
        await self._data_svc.create_or_update_everything_adversary()
        return next(self.find_objects(ram_key, {id_property: obj_id}))
    
    async def replace_on_disk_object(self, obj: Any, data: dict, ram_key: str, id_property: str):
        self._validate_ability_data(create=True, data=data)
        obj_id = getattr(obj, id_property)
        file_path = await self._get_existing_object_file_path(obj_id, ram_key)
        if data.get('tactic') not in file_path:
            await self.remove_object_from_disk_by_id(obj_id, ram_key)
            file_path = self._create_ability_filepath(data.get('tactic'), obj_id)
        await self._save_and_reload_object(file_path, data, type(obj), obj.access)
        return next(self.find_objects(ram_key, {id_property: obj_id}))
    
    async def remove_object_from_disk_by_id(self, identifier: str, ram_key: str):
        await super().remove_object_from_disk_by_id(identifier, ram_key)
        await self._data_svc.create_or_update_everything_adversary()
    
    async def update_on_disk_object(self, obj: Any, data: dict, ram_key: str, id_property: str, obj_class: type):
        obj_id = getattr(obj, id_property)
        file_path = await self._get_existing_object_file_path(obj_id, ram_key)
        existing_obj_data = AbilitySchema().dump(obj)
        existing_obj_data.update(data)
        self._validate_ability_data(create=False, data=existing_obj_data)
        if existing_obj_data.get('tactic') not in file_path:
            await self.remove_object_from_disk_by_id(obj_id, ram_key)
            file_path = self._create_ability_filepath(data.get('tactic'), obj_id)
        await self._save_and_reload_object(file_path, existing_obj_data, obj_class, obj.access)
        return next(self.find_objects(ram_key, {id_property: obj_id}))
    
    def _validate_ability_data(self, create: bool, data: dict):
        # Correct ability_id key for ability file saving.
        data['id'] = data.pop('ability_id', '')
        # If a new ability is being created, ensure required fields present.
        if create:
            # Set ability ID if undefined
            if not data['id']:
                data['id'] = str(uuid.uuid4())
            if not data.get('name'):
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing name')
            if 'tactic' not in data:
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing tactic')
            if not data.get('executors'):
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]}: at least one executor required')
        # Validate ID, used for file creation
        validator = re.compile(r'^[a-zA-Z0-9-_]+$')
        if 'id' in data and not validator.match(data['id']):
            raise JsonHttpBadRequest(f'Invalid ability ID {data["id"]}. IDs can only contain '
                                     'alphanumeric characters, hyphens, and underscores.')
        # Validate tactic, used for directory creation, lower case if present
        if 'tactic' in data:
            if not validator.match(data['tactic']):
                raise JsonHttpBadRequest(f'Invalid ability tactic {data["tactic"]}. Tactics can only contain '
                                         'alphanumeric characters, hyphens, and underscores.')
            data['tactic'] = data['tactic'].lower()
        # PATCH: Validate technique_name to block HTML/script injection.
        # Allowlist covers all legitimate ATT&CK technique name characters:
        # letters, digits, spaces, hyphens, underscores, parentheses,
        # slashes, dots, commas, colons, and ampersands.
        # Excludes < > " ' ; which are not present in any ATT&CK technique name.
        technique_name_validator = re.compile(r'^[a-zA-Z0-9\s\-_()/.,&:]+$')
        if data.get('technique_name') and not technique_name_validator.match(data['technique_name']):
            raise JsonHttpBadRequest(f'Invalid technique_name "{data["technique_name"]}". technique_name can only '
                                     'contain alphanumeric characters, spaces, hyphens, underscores, '
                                     'parentheses, slashes, dots, commas, colons, and ampersands.')
        if 'executors' in data and not data.get('executors'):
            raise JsonHttpBadRequest(f'Cannot create ability {data["id"]}: at least one executor required')
        if 'name' in data and not data.get('name'):
            raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing name')
    
    def _create_ability_filepath(self, tactic: str, obj_id: str):
        tactic_dir = os.path.join('data', 'abilities', tactic)
        if not os.path.exists(tactic_dir):
            os.makedirs(tactic_dir)
        return os.path.join(tactic_dir, '%s.yml' % obj_id)

    async def _save_and_reload_object(self, file_path: str, data: dict, obj_type: type, access: BaseWorld.Access):
        await self._file_svc.save_file(file_path, yaml.dump([data], encoding='utf-8', sort_keys=False),
                                       '', encrypt=False)
        await self._data_svc.remove('abilities', dict(ability_id=data['id']))
        await self._data_svc.load_ability_file(file_path, access)