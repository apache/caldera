import uuid

import marshmallow as ma

from app.objects.interfaces.i_object import FirstClassObjectInterface
from app.utility.base_object import BaseObject


DEFAULT_OBJECTIVE_ID = '495a9828-cab1-44dd-a0ca-66e58177d8cc'


class AdversarySchema(ma.Schema):

    class Meta:
        unknown = ma.EXCLUDE

    adversary_id = ma.fields.String()
    name = ma.fields.String()
    description = ma.fields.String()
    atomic_ordering = ma.fields.List(
        ma.fields.Raw(),  # Accepts either str (ability_id) or dict (step with metadata)
    )
    objective = ma.fields.String()
    tags = ma.fields.List(ma.fields.String(), allow_none=True)
    has_repeatable_abilities = ma.fields.Boolean(dump_only=True)
    plugin = ma.fields.String(load_default=None)

    @ma.pre_load
    def fix_id(self, adversary, **_):
        if 'id' in adversary:
            adversary['adversary_id'] = adversary.pop('id')
        return adversary

    @ma.pre_load
    def phase_to_atomic_ordering(self, adversary, **_):
        """
        Convert legacy adversary phases to atomic ordering
        """
        if 'phases' in adversary and 'atomic_ordering' in adversary:
            raise ma.ValidationError('atomic_ordering and phases cannot be used at the same time', 'phases', adversary)
        elif 'phases' in adversary:
            adversary['atomic_ordering'] = [ab_id for phase in adversary.get('phases', {}).values() for ab_id in phase]
            del adversary['phases']
        return adversary

    @ma.pre_load
    def remove_properties(self, data, **_):
        data.pop('has_repeatable_abilities', None)
        return data

    @ma.post_load
    def build_adversary(self, data, **kwargs):
        return None if kwargs.get('partial') is True else Adversary(**data)


class Adversary(FirstClassObjectInterface, BaseObject):

    schema = AdversarySchema()

    @property
    def unique(self):
        return self.hash('%s' % self.adversary_id)

    def __init__(self, name='', adversary_id='', description='', atomic_ordering=(), objective='', tags=None,
                 plugin='', metadata=None, **_):
        super().__init__()
        self.adversary_id = adversary_id if adversary_id else str(uuid.uuid4())
        self.name = name
        self.description = description
        self.atomic_ordering = list(atomic_ordering or [])
        self.objective = objective or DEFAULT_OBJECTIVE_ID
        self.tags = set(tags) if tags else set()
        self.has_repeatable_abilities = False
        self.plugin = plugin
        self.metadata = metadata or {}

    def store(self, ram):
        existing = self.retrieve(ram['adversaries'], self.unique)
        if not existing:
            ram['adversaries'].append(self)
            return self.retrieve(ram['adversaries'], self.unique)
        existing.update('name', self.name)
        existing.update('description', self.description)
        existing.update('atomic_ordering', self.atomic_ordering)
        existing.update('objective', self.objective)
        existing.update('tags', self.tags)
        existing.update('has_repeatable_abilities', self.check_repeatable_abilities(ram['abilities']))
        existing.update('plugin', self.plugin)
        return existing

    def verify(self, log, abilities, objectives):
        for step in self.atomic_ordering:
            ability_id = step if isinstance(step, str) else step.get('ability_id')
            if not any(ability.ability_id == ability_id for ability in abilities):
                log.warning('Ability referenced in adversary %s but not found: %s',
                            self.adversary_id, ability_id)

        if not self.objective:
            self.objective = DEFAULT_OBJECTIVE_ID
        elif not next((objective for objective in objectives if objective.id == self.objective), None):
            log.warning('Objective referenced in adversary %s but not found: %s. Setting default objective.',
                        self.adversary_id, self.objective)
            self.objective = DEFAULT_OBJECTIVE_ID

        self.has_repeatable_abilities = self.check_repeatable_abilities(abilities)

    def has_ability(self, ability):
        for a in self.atomic_ordering:
            if ability == a:
                return True
        return False

    async def which_plugin(self):
        return self.plugin

    def check_repeatable_abilities(self, ability_list):
        for step in self.atomic_ordering:
            ability_id = step if isinstance(step, str) else step.get('ability_id')
            for ab in ability_list:
                if ab.ability_id == ability_id and ab.repeatable:
                    return True
        return False
