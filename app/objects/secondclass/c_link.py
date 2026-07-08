import asyncio
import logging
import uuid
from base64 import b64decode
from datetime import datetime, timezone
from importlib import import_module

import marshmallow as ma

from app.objects.c_ability import Ability, AbilitySchema
from app.objects.secondclass.c_executor import Executor, ExecutorSchema
from app.objects.secondclass.c_fact import Fact, FactSchema, OriginType
from app.objects.secondclass.c_relationship import RelationshipSchema
from app.objects.secondclass.c_visibility import Visibility, VisibilitySchema
from app.utility.base_object import BaseObject
from app.utility.base_parser import PARSER_SIGNALS_FAILURE
from app.utility.base_service import BaseService


NO_STATUS_SET = object()


class LinkSchema(ma.Schema):

    class Meta:
        unknown = ma.EXCLUDE

    id = ma.fields.String(load_default='')
    paw = ma.fields.String()
    command = ma.fields.String()
    plaintext_command = ma.fields.String()
    status = ma.fields.Integer(load_default=-3)
    score = ma.fields.Integer(load_default=0)
    jitter = ma.fields.Integer(load_default=0)
    decide = ma.fields.DateTime(format=BaseObject.TIME_FORMAT)
    pin = ma.fields.Integer(load_default=0)
    pid = ma.fields.String()
    facts = ma.fields.List(ma.fields.Nested(FactSchema()))
    relationships = ma.fields.List(ma.fields.Nested(RelationshipSchema()))
    used = ma.fields.List(ma.fields.Nested(FactSchema()))
    unique = ma.fields.String()
    collect = ma.fields.DateTime(format=BaseObject.TIME_FORMAT, dump_default='')
    finish = ma.fields.String()
    ability = ma.fields.Nested(AbilitySchema())
    executor = ma.fields.Nested(ExecutorSchema())
    cleanup = ma.fields.Integer(load_default=0)
    visibility = ma.fields.Nested(VisibilitySchema())
    host = ma.fields.String(load_default=None)
    output = ma.fields.String()
    deadman = ma.fields.Boolean()
    agent_reported_time = ma.fields.DateTime(format=BaseObject.TIME_FORMAT, load_default=None)
    metadata = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.Raw(), allow_none=True)

    @ma.pre_load()
    def fix_ability(self, link, **_):
        if 'ability' in link and isinstance(link['ability'], Ability):
            ability = link.pop('ability')
            link['ability'] = ability.schema.dump(ability)
        return link

    @ma.pre_load()
    def fix_executor(self, link, **_):
        if 'executor' in link and isinstance(link['executor'], Executor):
            executor = link.pop('executor')
            link['executor'] = executor.schema.dump(executor)
        return link

    @ma.pre_load()
    def remove_properties(self, data, **_):
        data.pop('unique', None)
        data.pop('decide', None)
        data.pop('pid', None)
        data.pop('facts', None)
        data.pop('collect', None)
        data.pop('finish', None)
        data.pop('visibility', None)
        data.pop('output', None)
        data.pop('used.unique', None)
        return data

    @ma.post_load()
    def build_link(self, data, **kwargs):
        return None if kwargs.get('partial') is True else Link(**data)

    @ma.post_dump()
    def prepare_dump(self, data, **_):
        if data.get('agent_reported_time', None) is None:
            data.pop('agent_reported_time', None)
        return data


class Link(BaseObject):

    schema = LinkSchema()
    display_schema = LinkSchema(exclude=['jitter'])
    load_schema = LinkSchema(exclude=['decide', 'pid', 'facts', 'unique', 'collect', 'finish', 'visibility',
                                      'output', 'used.unique'])

    RESERVED = dict(origin_link_id='#{origin_link_id}')

    EVENT_EXCHANGE = 'link'
    EVENT_QUEUE_STATUS_CHANGED = 'status_changed'

    @property
    def raw_command(self):
        return self.decode_bytes(self.command) if self.command else ''

    @property
    def unique(self):
        return self.hash('%s' % self.id)

    @property
    def pin(self):
        return self._pin

    @pin.setter
    def pin(self, p):
        self._pin = p

    @property
    def states(self):
        return dict(HIGH_VIZ=-5,
                    UNTRUSTED=-4,
                    EXECUTE=-3,
                    DISCARD=-2,
                    PAUSE=-1,
                    SUCCESS=0,
                    ERROR=1,
                    TIMEOUT=124)

    @property
    def status(self):
        return self._status

    @property
    def display(self):
        dump = LinkSchema(exclude=['jitter']).dump(self)
        dump['command'] = self.decode_bytes(dump['command'])
        dump['plaintext_command'] = self.decode_bytes(dump['plaintext_command'])
        return dump

    @status.setter
    def status(self, value):
        previous_status = getattr(self, '_status', NO_STATUS_SET)

        self._status = value

        if previous_status is NO_STATUS_SET:
            return

        if previous_status == value:
            return

        self._emit_status_change_event(
            from_status=previous_status,
            to_status=value
        )

    @classmethod
    def is_global_variable(cls, variable):
        return variable in cls.RESERVED

    def __init__(self, command='', plaintext_command='', paw='', ability=None, executor=None, status=-3, score=0, jitter=0, cleanup=0, id='',
                 pin=0, host=None, deadman=False, used=None, relationships=None, agent_reported_time=None, metadata=None):
        super().__init__()
        self.id = str(id)
        self.command = command
        self.plaintext_command = plaintext_command
        self.command_hash = None
        self.paw = paw
        self.host = host
        self.cleanup = cleanup
        self.ability = ability
        self.executor = executor
        self.status = status
        self.score = score
        self.jitter = jitter
        self.decide = datetime.now(timezone.utc)
        self.pid = None
        self.collect = None
        self.finish = None
        self.facts = []
        self.relationships = relationships if relationships else []
        self.used = used if used else []
        self.visibility = Visibility()
        self._pin = pin
        self.output = False
        self.deadman = deadman
        self.agent_reported_time = agent_reported_time
        self.metadata = metadata or {}

    def __eq__(self, other):
        if isinstance(other, Link):
            return other.paw == self.paw and other.ability.ability_id == self.ability.ability_id \
                   and other.used == self.used
        return False

    async def parse(self, operation, result):
        if self.status != 0:
            return
        for parser in self.executor.parsers:
            source_facts = operation.source.facts if operation else []
            try:
                relationships = await self._parse_link_result(result, parser, source_facts)

                if len(relationships) > 0 and relationships[0] == PARSER_SIGNALS_FAILURE:
                    logging.getLogger('link').debug(f'link {self.id} (ability id={self.ability.ability_id}) encountered '
                                                    f'an error during execution, which was caught during parsing.')
                    self.status = self.states['ERROR']
                    relationships = []  # we didn't actually get anything out of this, so let's reset
                else:
                    await self.create_relationships(relationships, operation)
                await update_scores(operation, increment=len(relationships), used=self.used, facts=self.facts)
            except Exception as e:
                logging.getLogger('link').debug('error in %s while parsing ability %s: %s'
                                                % (parser.module, self.ability.ability_id, e))

    def apply_id(self, host):
        self.id = str(uuid.uuid4())
        self.host = host
        self.replace_origin_link_id()

    def can_ignore(self):
        return self.status in [self.states['DISCARD'], self.states['HIGH_VIZ']]

    def is_finished(self):
        return self.status in [self.states['DISCARD'], self.states['SUCCESS'],
                               self.states['ERROR'], self.states['TIMEOUT']]

    def is_valid_status(self, status):
        return status in self.states.values()

    def replace_origin_link_id(self):
        decoded_cmd = self.decode_bytes(self.command)
        self.command = self.encode_string(decoded_cmd.replace(self.RESERVED['origin_link_id'], self.id))

    def _emit_status_change_event(self, from_status, to_status):
        event_svc = BaseService.get_service('event_svc')

        task = asyncio.get_event_loop().create_task(
            event_svc.fire_event(
                exchange=Link.EVENT_EXCHANGE,
                queue=Link.EVENT_QUEUE_STATUS_CHANGED,
                link=self.id,
                from_status=from_status,
                to_status=to_status
            )
        )

        return task

    async def _parse_link_result(self, result, parser, source_facts):
        blob = b64decode(result).decode('utf-8')
        parser_info = dict(module=parser.module, used_facts=self.used, mappers=parser.parserconfigs,
                           source_facts=source_facts)
        p_inst = await self._load_module('Parser', parser_info)
        return p_inst.parse(blob=blob)

    @staticmethod
    async def _load_module(module_type, module_info):
        module = import_module(module_info['module'])
        return getattr(module, module_type)(module_info)

    async def create_relationships(self, relationships, operation):
        for relationship in relationships:
            relationship.origin = operation.id if operation else self.id
            await self.save_fact(operation, relationship.source, relationship.score, relationship.shorthand)
            await self.save_fact(operation, relationship.target, relationship.score, relationship.shorthand)
            if all((relationship.source.trait, relationship.edge)):
                knowledge_svc_handle = BaseService.get_service('knowledge_svc')
                await knowledge_svc_handle.add_relationship(relationship)
                self.relationships.append(relationship)

    async def save_fact(self, operation, fact, score, relationship):
        knowledge_svc_handle = BaseService.get_service('knowledge_svc')
        all_facts = await operation.all_facts() if operation else self.facts
        source = operation.id if operation else self.id
        rl = [relationship] if relationship else []
        if all([fact.trait, fact.value]):
            if operation and operation.source:
                if any([(fact.trait, fact.value) == (x.trait, x.value) for x in
                        await knowledge_svc_handle.get_facts(criteria=dict(source=operation.source.id))]):
                    source = operation.source.id
            fact.source = source  # Manual addition to ensure the check works correctly
            if not await knowledge_svc_handle.check_fact_exists(fact, all_facts):
                f_gen = Fact(trait=fact.trait, value=fact.value, source=source, score=score, collected_by=[self.paw],
                             technique_id=self.ability.technique_id, links=[self.id], relationships=rl,
                             origin_type=OriginType.LEARNED)
                self.facts.append(f_gen)
                await knowledge_svc_handle.add_fact(f_gen)
            else:
                existing_fact = (await knowledge_svc_handle.get_facts(criteria=dict(trait=fact.trait,
                                                                                    value=fact.value,
                                                                                    source=fact.source)))[0]
                if self.id not in existing_fact.links:
                    existing_fact.links.append(self.id)
                if relationship not in existing_fact.relationships:
                    existing_fact.relationships.append(relationship)
                if self.paw not in existing_fact.collected_by and existing_fact not in self.used:
                    existing_fact.collected_by.append(self.paw)
                await knowledge_svc_handle.update_fact(criteria=dict(trait=fact.trait, value=fact.value,
                                                                     source=fact.source),
                                                       updates=dict(links=existing_fact.links,
                                                                    relationships=existing_fact.relationships,
                                                                    collected_by=existing_fact.collected_by))
                existing_local_record = [x for x in self.facts if x.trait == fact.trait and x.value == fact.value]
                if existing_local_record:
                    existing_local_record[0].links = existing_fact.links
                else:
                    self.facts.append(existing_fact)


async def update_scores(operation, increment, used, facts):
    knowledge_svc_handle = BaseService.get_service('knowledge_svc')
    for uf in used:
        all_facts = await operation.all_facts() if operation else facts
        for found_fact in all_facts:
            if found_fact.unique == uf.unique:
                found_fact.score += increment
                await knowledge_svc_handle.update_fact(dict(trait=found_fact.trait, value=found_fact.value,
                                                            source=found_fact.source), dict(score=found_fact.score))
                break
