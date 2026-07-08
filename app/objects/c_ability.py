import collections
import logging
import uuid

import marshmallow as ma

from app.objects.interfaces.i_object import FirstClassObjectInterface
from app.objects.secondclass.c_executor import ExecutorSchema
from app.objects.secondclass.c_requirement import RequirementSchema
from app.utility.base_object import BaseObject
from app.utility.base_world import AccessSchema


class AbilitySchema(ma.Schema):

    class Meta:
        unknown = ma.EXCLUDE

    ability_id = ma.fields.String()
    tactic = ma.fields.String(load_default=None)
    technique_name = ma.fields.String(load_default=None)
    technique_id = ma.fields.String(load_default=None)
    name = ma.fields.String(load_default=None)
    description = ma.fields.String(load_default=None)
    executors = ma.fields.List(ma.fields.Nested(ExecutorSchema))
    requirements = ma.fields.List(ma.fields.Nested(RequirementSchema), load_default=None)
    privilege = ma.fields.String(load_default=None)
    repeatable = ma.fields.Bool(load_default=None)
    buckets = ma.fields.List(ma.fields.String(), load_default=None)
    additional_info = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.String())
    access = ma.fields.Nested(AccessSchema, load_default=None)
    singleton = ma.fields.Bool(load_default=None)
    plugin = ma.fields.String(load_default=None)
    delete_payload = ma.fields.Bool(load_default=None)

    @ma.pre_load
    def normalize_ability_file_fields(self, data, **_):
        """
        Ensures that ability file fields are formatted correctly for processing
        """
        if not isinstance(data, dict):
            return data
        if 'id' in data:
            data['ability_id'] = data.pop('id')
        if isinstance(data.get('technique'), dict):
            technique = data.pop('technique')
            data.setdefault('technique_id', technique.get('attack_id'))
            data.setdefault('technique_name', technique.get('name'))
        if 'platforms' in data and 'executors' not in data:
            data['executors'] = self._platforms_to_executor_list(data.pop('platforms'))
        if self._has_legacy_requirements(data.get('requirements')):
            data['requirements'] = self._legacy_requirements_to_list(data['requirements'])
        return data

    @ma.post_load
    def build_ability(self, data, **kwargs):
        return None if kwargs.get('partial') is True else Ability(**data)

    @staticmethod
    def _platforms_to_executor_list(platforms):
        """
        Translates legacy platform-structured YAML into caldera executor format
        """
        executors = []
        if not isinstance(platforms, dict):
            raise ma.ValidationError('Platforms must be a dictionary.', 'platforms')
        for platform_names, platform_executors in platforms.items():
            if not isinstance(platform_executors, dict):
                raise ma.ValidationError('Platform executors must be a dictionary.', 'platforms')

            platform_list = [name.strip() for name in str(platform_names).split(',')]

            for executor_names, executor_data in platform_executors.items():
                if not isinstance(executor_data, dict):
                    raise ma.ValidationError('Executor data must be a dictionary.', 'platforms')
                executor = dict(executor_data)  # make a dict of the data and fix up below
                if isinstance(executor.get('cleanup'), str):
                    # cleanup actions should be in a list
                    executor['cleanup'] = [executor['cleanup']]
                if isinstance(executor.get('parsers'), dict):
                    executor['parsers'] = [
                        {'module': module, 'parserconfigs': parserconfigs}
                        for module, parserconfigs in executor['parsers'].items()
                    ]

                executor_list = [name.strip() for name in str(executor_names).split(',')]
                executors.extend(
                    {**executor, 'platform': platform_name, 'name': executor_name}
                    for platform_name in platform_list
                    for executor_name in executor_list
                )
        return executors

    @staticmethod
    def _has_legacy_requirements(requirements):
        return (
            isinstance(requirements, list)
            and requirements
            and isinstance(requirements[0], dict)
            and 'relationship_match' not in requirements[0]
        )

    @staticmethod
    def _legacy_requirements_to_list(requirements):
        converted = []
        for requirement in requirements:
            for module, relationship_match in requirement.items():
                converted.append({'module': module, 'relationship_match': relationship_match})
        return converted


class Ability(FirstClassObjectInterface, BaseObject):

    schema = AbilitySchema()
    display_schema = AbilitySchema()

    HOOKS = dict()

    @property
    def unique(self):
        return self.ability_id

    @property
    def executors(self):
        yield from self._executor_map.values()

    def __init__(self, ability_id='', name=None, description=None, tactic=None, technique_id=None, technique_name=None,
                 executors=(), requirements=None, privilege=None, repeatable=False, buckets=None, access=None,
                 additional_info=None, tags=None, singleton=False, plugin='', delete_payload=True, **kwargs):
        super().__init__()
        self.ability_id = ability_id if ability_id else str(uuid.uuid4())
        self.tactic = tactic.lower() if tactic else None
        self.technique_name = technique_name
        self.technique_id = technique_id
        self.name = name
        self.description = description

        self._executor_map = collections.OrderedDict()
        self.add_executors(executors)

        self.requirements = requirements if requirements else []
        self.privilege = privilege
        self.repeatable = repeatable
        self.buckets = buckets if buckets else []
        self.singleton = singleton
        if access:
            self.access = self.Access(access)
        self.additional_info = additional_info or dict()
        self.additional_info.update(**kwargs)
        self.tags = set(tags) if tags else set()
        self.plugin = plugin
        self.delete_payload = delete_payload

    def __getattr__(self, item):
        try:
            return super().__getattribute__('additional_info')[item]
        except KeyError:
            raise AttributeError(item)

    def store(self, ram):
        existing = self.retrieve(ram['abilities'], self.unique)
        if not existing:
            name_match = [x for x in ram['abilities'] if x.name == self.name]
            if name_match:
                self.name = self.name + " (2)"
                logging.debug(f"Collision in ability name detected for {self.ability_id} and {name_match[0].ability_id} "
                              f"({name_match[0].name}). Modifying name of the second ability to {self.name}...")
            ram['abilities'].append(self)
            return self.retrieve(ram['abilities'], self.unique)
        existing.update('tactic', self.tactic)
        existing.update('technique_name', self.technique_name)
        existing.update('technique_id', self.technique_id)
        existing.update('name', self.name)
        existing.update('description', self.description)
        existing.update('_executor_map', self._executor_map)
        existing.update('privilege', self.privilege)
        existing.update('repeatable', self.repeatable)
        existing.update('buckets', self.buckets)
        existing.update('tags', self.tags)
        existing.update('singleton', self.singleton)
        existing.update('plugin', self.plugin)
        existing.update('delete_payload', self.delete_payload)
        return existing

    async def which_plugin(self):
        return self.plugin

    def find_executor(self, name, platform):
        return self._executor_map.get(self._make_executor_map_key(name, platform))

    def find_executors(self, names, platform):
        """Find executors for matching platform/executor names

        Only the first instance of a matching executor will be returned,
            as there should not be multiple executors matching a single
            platform/executor name pair.

        :param names: Executors to search. ex: ['psh', 'cmd']
        :type names: list(str)
        :param platform: Platform to search. ex: windows
        :type platform: str
        :return: List of executors ordered based on ordering of `names`
        :rtype: list(Executor)
        """
        executors = []
        seen_names = set()
        for name in names:
            if name in seen_names:
                continue
            seen_names.add(name)

            executor = self.find_executor(name, platform)
            if executor:
                executors.append(executor)

        return executors

    def add_executor(self, executor):
        """Add executor to map

        If the executor exists, delete the current entry and add the
            new executor to the bottom for FIFO
        """
        map_key = self._make_executor_map_key(executor.name, executor.platform)
        if map_key in self._executor_map:
            del self._executor_map[map_key]
        self._executor_map[map_key] = executor

    def add_executors(self, executors):
        """Create executor map from list of executor objects"""
        for executor in executors:
            self.add_executor(executor)

    def remove_all_executors(self):
        self._executor_map = collections.OrderedDict()

    async def add_bucket(self, bucket):
        if bucket not in self.buckets:
            self.buckets.append(bucket)

    @staticmethod
    def _make_executor_map_key(name, platform):
        return name, platform
