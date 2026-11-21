import re
import uuid
import os
import yaml

from typing import Any

from app.api.v2.managers.base_api_manager import BaseApiManager
from app.api.v2.responses import JsonHttpBadRequest
from app.objects.c_ability import AbilitySchema
from app.service.file_svc import FileSvc
from app.utility.base_world import BaseWorld


class AbilityApiManager(BaseApiManager):
    _EXECUTOR_LABEL_PATTERN = re.compile(r'^[a-zA-Z0-9_.-]+$')

    def __init__(self, data_svc, file_svc):
        super().__init__(data_svc=data_svc, file_svc=file_svc)

    async def create_on_disk_object(self, data: dict, access: dict, ram_key: str, id_property: str, obj_class: type):
        self._validate_ability_data(create=True, data=data)
        obj_id = data['id']
        if self.find_object(ram_key, {id_property: obj_id}):
            raise JsonHttpBadRequest(f'Ability with given id already exists: {obj_id}')
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
        # Normalize ability ID: prefer explicit 'ability_id' if provided, otherwise preserve any existing 'id'.
        ability_id = None
        if 'ability_id' in data:
            ability_id = data.pop('ability_id')
        elif 'id' in data:
            ability_id = data.get('id')

        # Sanitize supplied IDs before assigning them internally. If no ID is supplied during creation,
        # generate one instead.
        if ability_id in (None, '') and create:
            data['id'] = str(uuid.uuid4())
        else:
            data['id'] = BaseApiManager._sanitize_id(ability_id)

        # If a new ability is being created, ensure required fields present.
        if create:
            if not data.get('name'):
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing name')
            if not data.get('tactic'):
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing tactic')
            if not (data.get('executors') or data.get('platforms')):
                raise JsonHttpBadRequest(f'Cannot create ability {data["id"]}: at least one executor required')

        # Validate tactic, used for directory creation, lower case if present
        validator = re.compile(r'^[a-zA-Z0-9-_]+$')
        if 'tactic' in data:
            if not isinstance(data['tactic'], str) or not validator.match(data['tactic']):
                raise JsonHttpBadRequest(f'Invalid ability tactic {data["tactic"]}. Tactics can only contain '
                                         'alphanumeric characters, hyphens, and underscores.')
            data['tactic'] = data['tactic'].lower()

        if 'executors' in data and not data.get('executors') and 'platforms' not in data:
            raise JsonHttpBadRequest(f'Cannot create ability {data["id"]}: at least one executor required')

        if 'name' in data and not data.get('name'):
            raise JsonHttpBadRequest(f'Cannot create ability {data["id"]} due to missing name')

        self._validate_ability_privilege(data)
        self._validate_ability_executors(data)

    def _validate_ability_privilege(self, data: dict):
        if 'privilege' not in data:
            return

        privilege = data.get('privilege')
        if privilege is None or privilege == '':
            return
        if not isinstance(privilege, str):
            raise JsonHttpBadRequest(f'Invalid ability privilege {privilege}. Privilege must be one of: '
                                     'User, Elevated.')

        allowed_privileges = {privilege.name for privilege in BaseWorld.Privileges}
        if privilege not in allowed_privileges:
            raise JsonHttpBadRequest(f'Invalid ability privilege {privilege}. Privilege must be one of: '
                                     'User, Elevated.')

    def _validate_ability_executors(self, data: dict):
        if data.get('executors') is not None:
            self._validate_executor_list(data['executors'])
        if data.get('platforms') is not None:
            self._validate_platform_executor_map(data['platforms'])

    def _validate_executor_list(self, executors):
        if not isinstance(executors, list):
            raise JsonHttpBadRequest('Invalid ability executors. Executors must be a list.')

        for index, executor in enumerate(executors):
            if not isinstance(executor, dict):
                raise JsonHttpBadRequest(f'Invalid ability executor at index {index}. Executor must be a dictionary.')
            self._validate_executor_label(executor.get('name'), f'executor[{index}].name')
            self._validate_executor_label(executor.get('platform'), f'executor[{index}].platform')
            self._validate_payloads(executor.get('payloads'), f'executor[{index}].payloads')

    def _validate_platform_executor_map(self, platforms):
        if not isinstance(platforms, dict):
            raise JsonHttpBadRequest('Invalid ability platforms. Platforms must be a dictionary.')

        for platform_names, platform_executors in platforms.items():
            for platform_name in self._split_and_validate_labels(platform_names, 'platform'):
                if not isinstance(platform_executors, dict):
                    raise JsonHttpBadRequest(f'Invalid ability platform {platform_name}. Platform executors must be '
                                             'a dictionary.')
                for executor_names, executor in platform_executors.items():
                    for executor_name in self._split_and_validate_labels(executor_names, 'executor'):
                        if not isinstance(executor, dict):
                            raise JsonHttpBadRequest(f'Invalid ability executor {executor_name} for platform '
                                                     f'{platform_name}. Executor must be a dictionary.')
                    self._validate_payloads(executor.get('payloads'), f'platforms.{platform_name}.payloads')

    @classmethod
    def _split_and_validate_labels(cls, value, field_name):
        if not isinstance(value, str):
            raise JsonHttpBadRequest(f'Invalid ability {field_name} {value}. {field_name.capitalize()} names must be '
                                     'strings.')

        labels = [label.strip() for label in value.split(',')]
        if not labels or any(not label for label in labels):
            raise JsonHttpBadRequest(f'Invalid ability {field_name} {value}. {field_name.capitalize()} names cannot '
                                     'be empty.')

        for label in labels:
            cls._validate_executor_label(label, field_name)
        return labels

    @classmethod
    def _validate_executor_label(cls, value, field_name):
        if not isinstance(value, str) or not value:
            raise JsonHttpBadRequest(f'Invalid ability {field_name}. Executor and platform names must be non-empty '
                                     'strings.')
        if not cls._EXECUTOR_LABEL_PATTERN.match(value):
            raise JsonHttpBadRequest(f'Invalid ability {field_name} {value}. Executor and platform names can only '
                                     'contain alphanumeric characters, periods, hyphens, and underscores.')

    @staticmethod
    def _validate_payloads(payloads, field_name):
        if payloads is None:
            return
        if not isinstance(payloads, list):
            raise JsonHttpBadRequest(f'Invalid ability {field_name}. Payloads must be a list.')

        for payload in payloads:
            if not isinstance(payload, str):
                raise JsonHttpBadRequest(f'Invalid ability payload {payload}. Payload names must be strings.')
            safe_filename = FileSvc._validate_filename(payload)
            if BaseWorld.is_uuid4(payload) and safe_filename:
                continue
            if not safe_filename:
                raise JsonHttpBadRequest(f'Invalid ability payload {payload}. Payload names cannot contain path '
                                         'separators, traversal sequences, null bytes, or unsafe characters.')

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
