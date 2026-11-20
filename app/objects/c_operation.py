import asyncio
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from importlib import import_module

import marshmallow as ma

from app.objects.c_adversary import AdversarySchema
from app.objects.c_agent import AgentSchema
from app.objects.c_source import SourceSchema
from app.objects.c_planner import PlannerSchema
from app.objects.c_objective import ObjectiveSchema
from app.objects.secondclass.c_fact import OriginType
from app.objects.interfaces.i_object import FirstClassObjectInterface
from app.utility.base_object import BaseObject
from app.utility.base_planning_svc import BasePlanningService
from app.utility.base_service import BaseService

NO_PREVIOUS_STATE = object()


class InvalidOperationStateError(Exception):
    pass


class OperationOutputRequestSchema(ma.Schema):
    enable_agent_output = ma.fields.Boolean(dump_default=False)


class OperationSchema(ma.Schema):

    class Meta:
        unknown = ma.EXCLUDE

    id = ma.fields.String()
    name = ma.fields.String(required=True)
    host_group = ma.fields.List(ma.fields.Nested(AgentSchema()), attribute='agents', dump_only=True)
    adversary = ma.fields.Nested(AdversarySchema())
    jitter = ma.fields.String()
    planner = ma.fields.Nested(PlannerSchema())
    start = ma.fields.DateTime(format=BaseObject.TIME_FORMAT, dump_only=True)
    state = ma.fields.String()
    obfuscator = ma.fields.String()
    autonomous = ma.fields.Integer()
    chain = ma.fields.Function(lambda obj: [lnk.display for lnk in obj.chain])
    auto_close = ma.fields.Boolean()
    visibility = ma.fields.Integer()
    objective = ma.fields.Nested(ObjectiveSchema())
    use_learning_parsers = ma.fields.Boolean()
    group = ma.fields.String(load_default='')
    source = ma.fields.Nested(SourceSchema())

    @ma.pre_load()
    def remove_properties(self, data, **_):
        data.pop('host_group', None)
        data.pop('start', None)
        data.pop('chain', None)
        data.pop('objective', None)
        return data

    @ma.post_load
    def build_operation(self, data, **kwargs):
        return None if kwargs.get('partial') is True else Operation(**data)


class HostSchema(ma.Schema):
    display_name = ma.fields.String(dump_only=True)
    host = ma.fields.String()
    host_ip_addrs = ma.fields.List(ma.fields.String(), allow_none=True)
    platform = ma.fields.String()
    reachable_hosts = ma.fields.List(ma.fields.String(), allow_none=True)


class OperationSchemaAlt(OperationSchema):
    chain = property(lambda: AttributeError)
    host_group = property(lambda: AttributeError)
    source = property(lambda: AttributeError)
    visibility = property(lambda: AttributeError)
    agents = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.Nested(AgentSchema()))
    hosts = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.Nested(HostSchema()))


class Operation(FirstClassObjectInterface, BaseObject):
    EVENT_EXCHANGE = 'operation'
    EVENT_QUEUE_STATE_CHANGED = 'state_changed'
    EVENT_QUEUE_COMPLETED = 'completed'

    schema = OperationSchema()

    @property
    def unique(self):
        return self.hash('%s' % self.id)

    @property
    def states(self):
        return {state.name: state.value for state in self.States}

    @classmethod
    def get_states(cls):
        return [state.value for state in cls.States]

    @classmethod
    def get_finished_states(cls):
        return [cls.States.OUT_OF_TIME.value, cls.States.FINISHED.value, cls.States.CLEANUP.value]

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        previous_state = getattr(self, '_state', NO_PREVIOUS_STATE)

        self._state = value

        if previous_state is NO_PREVIOUS_STATE:
            return

        if previous_state == value:
            return

        self._emit_state_change_event(
            from_state=previous_state,
            to_state=value
        )

    def __init__(self, name, adversary=None, agents=None, id='', jitter='2/8', source=None, planner=None,
                 state='running', autonomous=True, obfuscator='plain-text', group=None, auto_close=True, visibility=50,
                 access=None, use_learning_parsers=True):
        super().__init__()
        self.id = str(id) if id else str(uuid.uuid4())
        self.start, self.finish = None, None
        self.base_timeout = 180
        self.link_timeout = 30
        self.name = name
        self.group = group
        self.agents = agents if agents else []
        self.untrusted_agents = set()
        self.adversary = adversary
        self.jitter = jitter
        self.source = source
        self.planner = planner
        self.state = state
        self.autonomous = autonomous
        self.last_ran = None
        self.obfuscator = obfuscator
        self.auto_close = auto_close
        self.visibility = visibility
        self.objective = None
        self.chain, self.potential_links, self.rules = [], [], []
        self.ignored_links = set()
        self.access = access if access else self.Access.APP
        self.use_learning_parsers = use_learning_parsers
        if source:
            self.rules = source.rules

    def store(self, ram):
        existing = self.retrieve(ram['operations'], self.unique)
        if not existing:
            ram['operations'].append(self)
            return self.retrieve(ram['operations'], self.unique)
        existing.update('state', self.state)
        existing.update('autonomous', self.autonomous)
        existing.update('obfuscator', self.obfuscator)
        return existing

    def set_start_details(self):
        self.id = self.id if self.id else str(uuid.uuid4())
        self.start = datetime.now(timezone.utc)

    def add_link(self, link):
        self.chain.append(link)

    def has_link(self, link_id):
        return any(lnk.id == link_id for lnk in self.potential_links + self.chain)

    def update_untrusted_agents(self, agent):
        if not agent.trusted and agent in self.agents:
            self.untrusted_agents.add(agent.paw)

    async def all_facts(self):
        knowledge_svc_handle = BaseService.get_service('knowledge_svc')
        data_svc_handle = BaseService.get_service('data_svc')
        seeded_facts = []
        if self.source:
            seeded_facts = await data_svc_handle.get_facts_from_source(self.source.id)
            seeded_facts = [f for f in seeded_facts if f.score > 0]
        learned_facts = await knowledge_svc_handle.get_facts(criteria=dict(source=self.id))
        learned_facts = [f for f in learned_facts if f.score > 0]
        return seeded_facts + learned_facts

    async def has_fact(self, trait, value):
        for f in await self.all_facts():
            if f.trait == trait and f.value == value:
                return True
        return False

    async def all_relationships(self):
        knowledge_svc_handle = BaseService.get_service('knowledge_svc')
        seeded_relationships = []
        if self.source:
            seeded_relationships = await knowledge_svc_handle.get_relationships(criteria=dict(origin=self.source.id))
        learned_relationships = await knowledge_svc_handle.get_relationships(criteria=dict(origin=self.id))
        return seeded_relationships + learned_relationships

    def ran_ability_id(self, ability_id):
        return ability_id in [link.ability.ability_id for link in self.chain if link.finish]

    async def apply(self, link):
        while self.state != self.states['RUNNING']:
            if self.state == self.states['RUN_ONE_LINK']:
                self.add_link(link)
                self.state = self.states['PAUSED']
                return link.id
            else:
                await asyncio.sleep(15)
        self.add_link(link)
        return link.id

    async def close(self, services):
        await self._cleanup_operation(services)
        await self._save_new_source(services)
        await services.get('event_svc').fire_event(
            exchange=Operation.EVENT_EXCHANGE,
            queue=Operation.EVENT_QUEUE_COMPLETED,
            op=self.id
        )

        if self.state not in [self.states['FINISHED'], self.states['OUT_OF_TIME']]:
            self.state = self.states['FINISHED']
        self.finish = self.get_current_timestamp()

    async def wait_for_completion(self):
        for member in self.agents:
            if not member.trusted:
                for link in await self._unfinished_links_for_agent(member.paw):
                    link.status = link.states['UNTRUSTED']
                continue
            while len(await self._unfinished_links_for_agent(member.paw)) > 0:
                await asyncio.sleep(3)
                if not member.trusted:
                    break

    async def wait_for_links_completion(self, link_ids):
        """
        Wait for started links to be completed
        :param link_ids:
        :return: None
        """
        for link_id in link_ids:
            link = [link for link in self.chain if link.id == link_id][0]
            if link.can_ignore():
                self.add_ignored_link(link.id)
            member = [member for member in self.agents if member.paw == link.paw][0]
            while not (link.finish or link.can_ignore()):
                await asyncio.sleep(5)
                if not member.trusted:
                    break

    async def is_closeable(self):
        return await self.is_finished() or self.auto_close

    async def is_finished(self):
        if self.state in [self.states['FINISHED'], self.states['OUT_OF_TIME'], self.states['CLEANUP']] \
                or (self.objective and self.objective.completed(await self.all_facts())):
            return True
        return False

    def link_status(self):
        return -3 if self.autonomous else -1

    def add_ignored_link(self, link_id):
        self.ignored_links.add(link_id)

    async def active_agents(self):
        active = []
        for agent in self.agents:
            if agent.last_seen > self.start:
                active.append(agent)
        return active

    async def get_active_agent_by_paw(self, paw):
        return [a for a in await self.active_agents() if a.paw == paw]

    async def get_skipped_abilities_by_agent(self, data_svc):
        abilities_by_agent = await self._get_all_possible_abilities_by_agent(data_svc)
        skipped_abilities = []
        for agent in self.agents:
            agent_skipped = defaultdict(dict)
            agent_executors = agent.executors
            agent_ran = set([link.ability.ability_id for link in self.chain if link.paw == agent.paw and link.finish])
            for ab in abilities_by_agent[agent.paw]['all_abilities']:
                skipped = self._check_reason_skipped(agent=agent, ability=ab, agent_executors=agent_executors,
                                                     op_facts=[f.trait for f in await self.all_facts()],
                                                     state=self.state, agent_ran=agent_ran)
                if skipped:
                    if agent_skipped[skipped['ability_id']]:
                        if agent_skipped[skipped['ability_id']]['reason_id'] > skipped['reason_id']:
                            agent_skipped[skipped['ability_id']] = skipped
                    else:
                        agent_skipped[skipped['ability_id']] = skipped
            skipped_abilities.append({agent.paw: list(agent_skipped.values())})
        return skipped_abilities

    async def report(self, file_svc, data_svc, output=False):
        try:
            report = dict(name=self.name, host_group=[a.display for a in self.agents],
                          start=self.start.strftime(self.TIME_FORMAT),
                          steps=[], finish=self.finish, planner=self.planner.name, adversary=self.adversary.display,
                          jitter=self.jitter, objectives=self.objective.display,
                          facts=[f.display for f in await self.all_facts()])
            agents_steps = {a.paw: {'steps': []} for a in self.agents}
            for step in self.chain:
                step_report = dict(link_id=step.id,
                                   ability_id=step.ability.ability_id,
                                   command=self._sanitize_for_json(self.decode_bytes(step.command)),
                                   plaintext_command=self._sanitize_for_json(self.decode_bytes(step.plaintext_command)),
                                   delegated=step.decide.strftime(self.TIME_FORMAT),
                                   run=step.finish,
                                   status=step.status,
                                   platform=step.executor.platform,
                                   executor=step.executor.name,
                                   pid=step.pid,
                                   description=self._sanitize_for_json(step.ability.description),
                                   name=self._sanitize_for_json(step.ability.name),
                                   attack=dict(tactic=step.ability.tactic,
                                               technique_name=self._sanitize_for_json(step.ability.technique_name),
                                               technique_id=step.ability.technique_id))
                if output and step.output:
                    try:
                        results = self.decode_bytes(file_svc.read_result_file(step.unique))
                        step_report['output'] = json.loads(results.replace('\\r\\n', '').replace('\\n', ''))
                    except Exception:
                        step_report['output'] = dict(stdout=self._sanitize_for_json(results if 'results' in dir() else ''),
                                                     stderr='', exit_code='')
                if step.agent_reported_time:
                    step_report['agent_reported_time'] = step.agent_reported_time.strftime(self.TIME_FORMAT)
                agents_steps.setdefault(step.paw, {'steps': []})['steps'].append(step_report)
            report['steps'] = agents_steps
            report['skipped_abilities'] = await self.get_skipped_abilities_by_agent(data_svc)
            return report
        except Exception:
            logging.error('Error generating operation report (%s)' % self.name, exc_info=True)
            raise

    async def event_logs(self, file_svc, data_svc, output=False):
        # Ignore discarded / high visibility links that did not actually run.
        event_logs = []
        for step in self.chain:
            if step.can_ignore():
                continue
            try:
                event_logs.append(await self._convert_link_to_event_log(step, file_svc, data_svc, output=output))
            except Exception:
                logging.warning('Error converting link %s to event log for operation %s, skipping',
                                step.id, self.name, exc_info=True)
        return event_logs

    async def cede_control_to_planner(self, services):
        planner = await self._get_planning_module(services)
        await planner.execute()
        while not await self.is_closeable():
            await asyncio.sleep(10)
        await self.close(services)

    async def run(self, services):
        await self._init_source()
        data_svc = services.get('data_svc')
        await self._load_objective(data_svc)
        try:
            await self.cede_control_to_planner(services)
            await self.write_event_logs_to_disk(services.get('file_svc'), data_svc, output=True)
        except Exception as e:
            logging.error(e, exc_info=True)

    async def write_event_logs_to_disk(self, file_svc, data_svc, output=False):
        event_logs = await self.event_logs(file_svc, data_svc, output=output)
        event_logs_dir = await file_svc.create_exfil_sub_directory('%s/event_logs' % self.get_config('reports_dir'))
        file_name = 'operation_%s.json' % self.id
        await self._write_logs_to_disk(event_logs, file_name, event_logs_dir, file_svc)
        logging.debug('Wrote event logs for operation %s to disk at %s/%s' % (self.name, event_logs_dir, file_name))

    async def _write_logs_to_disk(self, logs, file_name, dest_dir, file_svc):
        logs_dumps = json.dumps(logs, ensure_ascii=True, default=str) + os.linesep
        await file_svc.save_file(file_name, logs_dumps.encode(), dest_dir, encrypt=False)

    async def _load_objective(self, data_svc):
        obj = await data_svc.locate('objectives', match=dict(id=self.adversary.objective))
        if not obj:
            obj = await data_svc.locate('objectives', match=dict(name='default'))
        self.objective = deepcopy(obj[0])

    async def _convert_link_to_event_log(self, link, file_svc, data_svc, output=False):
        event_dict = dict(command=self._sanitize_for_json(self.decode_bytes(link.command)),
                          plaintext_command=self._sanitize_for_json(self.decode_bytes(link.plaintext_command)),
                          delegated_timestamp=link.decide.strftime(self.TIME_FORMAT),
                          collected_timestamp=link.collect.strftime(self.TIME_FORMAT) if link.collect else None,
                          finished_timestamp=link.finish,
                          status=link.status,
                          platform=link.executor.platform,
                          executor=link.executor.name,
                          pid=link.pid,
                          agent_metadata=await self._get_agent_info_for_event_log(link.paw, data_svc),
                          ability_metadata=self._get_ability_metadata_for_event_log(link.ability),
                          operation_metadata=self._get_operation_metadata_for_event_log(),
                          attack_metadata=self._get_attack_metadata_for_event_log(link.ability))
        if output and link.output:
            try:
                results = self.decode_bytes(file_svc.read_result_file(link.unique))
                event_dict['output'] = json.loads(results.replace('\\r\\n', '').replace('\\n', ''))
            except Exception:
                event_dict['output'] = dict(stdout=self._sanitize_for_json(results if 'results' in dir() else ''),
                                            stderr='', exit_code='')
        if link.agent_reported_time:
            event_dict['agent_reported_time'] = link.agent_reported_time.strftime(self.TIME_FORMAT)
        return event_dict

    async def _init_source(self):
        # seed knowledge_svc with source facts
        if self.source:
            knowledge_svc_handle = BaseService.get_service('knowledge_svc')
            for f in self.source.facts:
                f.origin_type = OriginType.SEEDED
                f.source = self.source.id
                await knowledge_svc_handle.add_fact(f)
            for r in self.source.relationships:
                r.origin = self.source.id
                r.source = self._resolve_fact(r.source, self.source.facts)
                r.target = self._resolve_fact(r.target, self.source.facts)
                await knowledge_svc_handle.add_relationship(r)

    @staticmethod
    def _resolve_fact(fact, fact_list):
        """Resolve a relationship fact stub (trait-only) against the source's full fact list.

        When relationships in a fact source are defined with only a trait reference (no value),
        the matching fact from the source's fact list is returned so the relationship carries real
        values.  If the fact already has a value, or no match is found, the original is returned.
        """
        if fact.value is None:
            for candidate in fact_list:
                if candidate.trait == fact.trait:
                    return candidate
        return fact

    async def _cleanup_operation(self, services):
        cleanup_count = 0
        for member in self.agents:
            for link in await services.get('planning_svc').get_cleanup_links(self, member):
                self.add_link(link)
                cleanup_count += 1
        if cleanup_count:
            self.state = self.states['CLEANUP']
            logging.debug(f'Starting cleanup for operation {self.id}')
            await self._safely_handle_cleanup(cleanup_count)
            logging.debug(f'Completed cleanup for operation {self.id}')

    async def _safely_handle_cleanup(self, cleanup_link_count):
        try:
            await asyncio.wait_for(self.wait_for_completion(),
                                   timeout=self.base_timeout + self.link_timeout * cleanup_link_count)
        except asyncio.TimeoutError:
            logging.warning(f"[OPERATION] - unable to close {self.name} cleanly due to timeout. Forcibly terminating.")
            self.state = self.states['OUT_OF_TIME']

    async def _get_planning_module(self, services):
        planning_module = import_module(self.planner.module)
        return planning_module.LogicalPlanner(self, services.get('planning_svc'), **self.planner.params,
                                              stopping_conditions=self.planner.stopping_conditions)

    async def _save_new_source(self, services):
        def fact_to_dict(f):
            if f:
                return dict(trait=f.trait, value=f.value, score=f.score)
        existing = await services.get('data_svc').locate('sources', match=dict(name=self.name))
        source_id = existing[0].id if existing else str(uuid.uuid4())
        data = dict(
            id=source_id,
            name=self.name,
            facts=[fact_to_dict(f) for link in self.chain for f in link.facts],
            relationships=[dict(source=fact_to_dict(r.source), edge=r.edge,
                                target=fact_to_dict(r.target), score=r.score)
                           for link in self.chain for r in link.relationships]
        )
        await services.get('rest_svc').persist_source(dict(access=[self.access]), data)

    async def update_operation_agents(self, services):
        self.agents = await services.get('rest_svc').construct_agents_for_group(self.group)

    async def _unfinished_links_for_agent(self, paw):
        return [link for link in self.chain if link.paw == paw and not link.finish and not link.can_ignore()]

    async def _get_all_possible_abilities_by_agent(self, data_svc):
        abilities = {'all_abilities': [ab for ab_id in self.adversary.atomic_ordering
                                       for ab in await data_svc.locate('abilities', match=dict(ability_id=ab_id))]}
        abilities_by_agent = {a.paw: abilities for a in self.agents}
        for link in self.chain:
            if link.ability.ability_id not in self.adversary.atomic_ordering:
                matching_abilities = await data_svc.locate('abilities', match=dict(ability_id=link.ability.ability_id))
                entry = abilities_by_agent.get(link.paw)
                if entry:
                    entry['all_abilities'].extend(matching_abilities)
        return abilities_by_agent

    def _check_reason_skipped(self, agent, ability, op_facts, state, agent_executors, agent_ran):
        if ability.ability_id in agent_ran:
            return

        valid_executors = ability.find_executors(agent_executors, agent.platform)

        fact_dependency_fulfilled = False
        for executor in valid_executors:
            facts = re.findall(BasePlanningService.re_variable, executor.test) if executor.command else []
            if not facts or all(fact in op_facts for fact in facts):
                fact_dependency_fulfilled = True
        associated_links = set([link.id for link in self.chain if link.paw == agent.paw
                                and link.ability.ability_id == ability.ability_id])

        if agent.platform == 'unknown':
            return dict(reason='Platform not available', reason_id=self.Reason.PLATFORM.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        elif not valid_executors:
            return dict(reason='Mismatched ability platform and executor', reason_id=self.Reason.EXECUTOR.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        elif not agent.privileged_to_run(ability):
            return dict(reason='Ability privilege not fulfilled', reason_id=self.Reason.PRIVILEGE.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        elif not fact_dependency_fulfilled:
            return dict(reason='Fact dependency not fulfilled', reason_id=self.Reason.FACT_DEPENDENCY.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        elif not set(associated_links).isdisjoint(self.ignored_links):
            return dict(reason='Link ignored - highly visible or discarded link',
                        reason_id=self.Reason.LINK_IGNORED.value, ability_id=ability.ability_id,
                        ability_name=ability.name)
        elif not agent.trusted:
            return dict(reason='Agent not trusted', reason_id=self.Reason.UNTRUSTED.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        elif state != 'finished':
            return dict(reason='Operation not completed', reason_id=self.Reason.OP_RUNNING.value,
                        ability_id=ability.ability_id, ability_name=ability.name)
        else:
            return dict(reason='Other', reason_id=self.Reason.OTHER.value,
                        ability_id=ability.ability_id, ability_name=ability.name)

    @staticmethod
    def _sanitize_for_json(s):
        """Remove characters that are problematic for JSON serialization.

        Strips surrogate characters, null bytes, and other non-serializable
        unicode from strings to ensure they can always be safely passed to
        json.dumps without raising encoding errors.
        """
        if not isinstance(s, str):
            return s
        try:
            # Round-trip through utf-8 to normalize surrogates and bad chars
            sanitized = s.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
            # Strip null bytes and other control characters (except tab, newline, carriage return)
            sanitized = ''.join(c if c in ('\t', '\n', '\r') or (c >= ' ' or ord(c) > 127) else '\ufffd' for c in sanitized)
            # Final verification: ensure json.dumps won't choke
            json.dumps(sanitized)
            return sanitized
        except Exception:
            # Last resort: ASCII-only representation
            return s.encode('ascii', errors='replace').decode('ascii')

    def _get_operation_metadata_for_event_log(self):
        return dict(operation_name=self._sanitize_for_json(self.name),
                    operation_start=self.start.strftime(self.TIME_FORMAT),
                    operation_adversary=self._sanitize_for_json(self.adversary.name))

    def _emit_state_change_event(self, from_state, to_state):
        event_svc = BaseService.get_service('event_svc')

        task = asyncio.get_event_loop().create_task(
            event_svc.fire_event(
                exchange=Operation.EVENT_EXCHANGE,
                queue=Operation.EVENT_QUEUE_STATE_CHANGED,
                op=self.id,
                from_state=from_state,
                to_state=to_state
            )
        )

        return task

    @staticmethod
    def _get_ability_metadata_for_event_log(ability):
        return dict(ability_id=ability.ability_id,
                    ability_name=Operation._sanitize_for_json(ability.name),
                    ability_description=Operation._sanitize_for_json(ability.description))

    @staticmethod
    def _get_attack_metadata_for_event_log(ability):
        return dict(tactic=ability.tactic,
                    technique_name=Operation._sanitize_for_json(ability.technique_name),
                    technique_id=ability.technique_id)

    @staticmethod
    async def _get_agent_info_for_event_log(agent_paw, data_svc):
        agent_search_results = await data_svc.locate('agents', match=dict(paw=agent_paw))
        if not agent_search_results:
            return {}
        else:
            # We expect only one agent per paw.
            agent = agent_search_results[0]
            return dict(paw=agent.paw,
                        group=agent.group,
                        architecture=agent.architecture,
                        username=agent.username,
                        location=agent.location,
                        pid=agent.pid,
                        ppid=agent.ppid,
                        privilege=agent.privilege,
                        host=agent.host,
                        contact=agent.contact,
                        created=agent.created.strftime(BaseObject.TIME_FORMAT))

    class Reason(Enum):
        PLATFORM = 0
        EXECUTOR = 1
        PRIVILEGE = 2
        FACT_DEPENDENCY = 3
        LINK_IGNORED = 4
        UNTRUSTED = 5
        OP_RUNNING = 6
        OTHER = 7

    class States(Enum):
        RUNNING = 'running'
        RUN_ONE_LINK = 'run_one_link'
        PAUSED = 'paused'
        OUT_OF_TIME = 'out_of_time'
        FINISHED = 'finished'
        CLEANUP = 'cleanup'
