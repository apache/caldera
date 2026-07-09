import asyncio
import uuid

from marshmallow.schema import SchemaMeta
from typing import Any

from app.api.v2.managers.base_api_manager import BaseApiManager
from app.api.v2.responses import JsonHttpNotFound, JsonHttpForbidden, JsonHttpBadRequest
from app.objects.c_adversary import Adversary, AdversarySchema
from app.objects.c_operation import Operation, OperationSchema
from app.objects.c_planner import PlannerSchema
from app.objects.c_source import SourceSchema
from app.objects.c_ability import AbilitySchema
from app.objects.c_agent import Agent
from app.objects.secondclass.c_executor import Executor, ExecutorSchema
from app.objects.secondclass.c_link import Link
from app.utility.base_world import BaseWorld


class OperationApiManager(BaseApiManager):
    def __init__(self, services):
        super().__init__(data_svc=services['data_svc'], file_svc=services['file_svc'])
        self.services = services
        self.knowledge_svc = services['knowledge_svc']

    async def get_operation_report(self, operation_id: str, access: dict, output: bool):
        operation = await self.get_operation_object(operation_id, access)
        report = await operation.report(file_svc=self._file_svc, data_svc=self._data_svc, output=output)
        return report

    async def get_operation_event_logs(self, operation_id: str, access: dict, output: bool):
        operation = await self.get_operation_object(operation_id, access)
        event_logs = await operation.event_logs(file_svc=self._file_svc, data_svc=self._data_svc, output=output)
        return event_logs

    async def create_object_from_schema(self, schema: SchemaMeta, data: dict,
                                        access: BaseWorld.Access, existing_operation: Operation = None):
        if data.get('state'):
            await self.validate_operation_state(data, existing_operation)
        operation = await self.setup_operation(data, access)
        operation.set_start_details()
        operation.store(self._data_svc.ram)
        asyncio.get_event_loop().create_task(operation.run(self.services))
        return operation

    async def find_and_update_object(self, ram_key: str, data: dict, search: dict = None):
        for obj in self.find_objects(ram_key, search):
            new_obj = await self.update_object(obj, data)
            return new_obj

    async def update_object(self, obj: Any, data: dict):
        await self.validate_operation_state(data, obj)
        return super().update_object(obj, data)

    async def get_operation_links(self, operation_id: str, access: dict):
        operation = await self.get_operation_object(operation_id, access)
        links = [link.display for link in operation.chain]
        return links

    async def get_operation_link(self, operation_id: str, link_id: str, access: dict):
        operation = await self.get_operation_object(operation_id, access)
        link = self.search_operation_for_link(operation, link_id)
        return link.display

    async def get_operation_link_result(self, operation_id: str, link_id: str, access: dict):
        operation = await self.get_operation_object(operation_id, access)
        link = self.search_operation_for_link(operation, link_id)
        try:
            result = self.services['file_svc'].read_result_file('%s' % link_id)
            return dict(link=link.display, result=result)
        except FileNotFoundError:
            return dict(link=link.display, result='')

    async def update_operation_link(self, operation_id: str, link_id: str, link_data: dict, access: BaseWorld.Access):
        operation = await self.get_operation_object(operation_id, access)
        link = self.search_operation_for_link(operation, link_id)
        if link.access not in access['access']:
            raise JsonHttpForbidden(f'Cannot update link {link_id} due to insufficient permissions.')
        if link.is_finished() or link.can_ignore():
            raise JsonHttpForbidden(f'Cannot update a finished link: {link_id}')
        if link_data.get('command'):
            command_str = link_data.get('command')
            link.executor.command = command_str
            link.ability = self.build_ability(link_data.get('ability', {}), link.executor)
            link.command = self._encode_string(command_str)
        if link_data.get('status'):
            link_status = link_data['status']
            if not link.is_valid_status(link_status):
                raise JsonHttpBadRequest(f'Cannot update link {link_id} due to invalid link status.')
            link.status = link_status
            if link.can_ignore():
                operation.add_ignored_link(link.id)
        return link.display

    async def create_potential_link(self, operation_id: str, data: dict, access: BaseWorld.Access):
        self.validate_link_data(data)
        operation = await self.get_operation_object(operation_id, access)
        agent = await self.get_agent(operation, data)
        if data['executor']['name'] not in agent.executors:
            raise JsonHttpBadRequest(f'Agent {agent.paw} missing specified executor')
        executor = self.build_executor(data=data.pop('executor', {}), agent=agent)
        ability = self.build_ability(data=data.pop('ability', {}), executor=executor)
        await self._call_ability_plugin_hooks(ability, executor)
        link = Link.load(dict(command=encoded_command, plaintext_command=encoded_command, paw=agent.paw, ability=ability, executor=executor,
                              status=operation.link_status(), score=data.get('score', 0), jitter=data.get('jitter', 0),
                              cleanup=data.get('cleanup', 0), pin=data.get('pin', 0),
                              host=agent.host, deadman=data.get('deadman', False), used=data.get('used', []),
                              relationships=data.get('relationships', [])))
        link.apply_id(agent.host)
        await operation.apply(link)
        return link.display

    async def get_potential_links(self, operation_id: str, access: dict, paw: str = None):
        operation = await self.get_operation_object(operation_id, access)
        if operation.finish:
            return []
        if paw:
            agents = [a for a in operation.agents if a.paw == paw]
            if not agents:
                raise JsonHttpNotFound(f'Agent not found: {paw}')
        else:
            agents = operation.agents
        potential_abilities = await self.services['rest_svc'].build_potential_abilities(operation)
        operation.potential_links = await self.services['rest_svc'].build_potential_links(operation, agents,
                                                                                          potential_abilities)
        potential_links = [potential_link.display for potential_link in operation.potential_links]
        return potential_links

    async def get_operation_object(self, operation_id: str, access: dict):
        try:
            operation = (await self._data_svc.locate('operations', {'id': operation_id}))[0]
        except IndexError:
            raise JsonHttpNotFound(f'Operation not found: {operation_id}')
        if operation.match(access):
            return operation
        raise JsonHttpForbidden(f'Cannot view operation due to insufficient permissions: {operation_id}')

    async def setup_operation(self, data: dict, access: BaseWorld.Access):
        """Applies default settings to an operation if data is missing."""
        planner_id = data.pop('planner', {}).get('id', '')
        data['planner'] = await self._construct_and_dump_planner(planner_id)
        adversary_id = data.pop('adversary', {}).get('adversary_id', '')
        data['adversary'] = await self._construct_and_dump_adversary(adversary_id)
        fact_source_id = data.pop('source', {}).get('id', '')
        data['source'] = await self._construct_and_dump_source(fact_source_id)
        operation = OperationSchema().load(data)
        await operation.update_operation_agents(self.services)
        allowed = self._get_allowed_from_access(access)
        operation.access = allowed
        return operation

    async def _construct_and_dump_planner(self, planner_id: str):
        planner = await self.services['data_svc'].locate('planners', match=dict(planner_id=planner_id))
        if not planner:
            planner = await self.services['data_svc'].locate('planners', match=dict(name='atomic'))
        return PlannerSchema().dump(planner[0])

    async def _construct_and_dump_adversary(self, adversary_id: str):
        adv = await self.services['data_svc'].locate('adversaries', match=dict(adversary_id=adversary_id))
        if not adv:
            adv = Adversary.load(dict(adversary_id='ad-hoc', name='ad-hoc', description='an empty adversary profile',
                                      atomic_ordering=[]))
        else:
            adv = adv[0]
        return AdversarySchema().dump(adv)

    async def _construct_and_dump_source(self, source_id: str):
        source = await self.services['data_svc'].locate('sources', match=dict(id=source_id))
        if not source:
            source = (await self.services['data_svc'].locate('sources', match=dict(name='basic')))
        return SourceSchema().dump(source[0])

    async def _call_ability_plugin_hooks(self, ability, executor):
        """Calls any plugin hooks (at runtime) that exist for the ability and executor."""
        if (hasattr(executor, 'HOOKS') and executor.HOOKS and
                hasattr(executor, 'language') and executor.language and
                executor.language in executor.HOOKS):
            await executor.HOOKS[executor.language](ability, executor)

    async def validate_operation_state(self, data: dict, existing: Operation = None):
        if not existing:
            if data.get('state') in Operation.get_finished_states():
                raise JsonHttpBadRequest('Cannot create a finished operation.')
            elif data.get('state') not in Operation.get_states():
                raise JsonHttpBadRequest('state must be one of {}'.format(Operation.get_states()))
        else:
            if await existing.is_finished():
                raise JsonHttpBadRequest('This operation has already finished.')
            elif 'state' in data and data.get('state') not in Operation.get_states():
                raise JsonHttpBadRequest('state must be one of {}'.format(Operation.get_states()))

    def search_operation_for_link(self, operation: Operation, link_id: str):
        for link in operation.chain:
            if link.id == link_id:
                return link
        raise JsonHttpNotFound(f'Link {link_id} was not found in Operation {operation.id}')

    def validate_link_data(self, link_data: dict):
        if not link_data.get('executor'):
            raise JsonHttpBadRequest('\'executor\' is a required field for link creation.')
        if not link_data['executor'].get('name'):
            raise JsonHttpBadRequest('\'name\' is a required field for link executor.')
        if not link_data['executor'].get('command'):
            raise JsonHttpBadRequest('\'command\' is a required field for link executor.')
        if not link_data.get('paw'):
            raise JsonHttpBadRequest('\'paw\' is a required field for link creation.')

    async def get_agent(self, operation: Operation, data: dict):
        paw = data['paw']
        try:
            agent = [a for a in operation.agents if a.paw == paw][0]
        except IndexError:
            raise JsonHttpNotFound(f'Agent {data["paw"]} was not found.')
        return agent

    def get_agents(self, operation: dict):
        agents = {}
        chain = operation.get('chain', [])
        for link in chain:
            paw = link.get('paw')
            if not paw:
                continue
            if paw not in agents:
                agent_obj = self.find_object('agents', {'paw': paw})
                if agent_obj is None:
                    continue
                tmp_agent = agent_obj.display
                tmp_agent['links'] = []
                agents[paw] = tmp_agent
            agents[paw]['links'].append(link)
        return agents

    async def get_hosts(self, operation: dict):
        hosts = {}
        chain = operation.get('chain', [])
        for link in chain:
            host = link.get('host')
            if not host:
                continue
            if host not in hosts:
                agent_obj = self.find_object('agents', {'host': host})
                if agent_obj is None:
                    continue
                tmp_agent = agent_obj.display
                tmp_host = {
                    'host': tmp_agent.get('host'),
                    'host_ip_addrs': tmp_agent.get('host_ip_addrs'),
                    'platform': tmp_agent.get('platform'),
                    'reachable_hosts': await self.get_reachable_hosts(agent=tmp_agent)
                }
                hosts[host] = tmp_host
        return hosts

    async def get_reachable_hosts(self, agent: dict = None, operation: dict = None):
        """
        NOTE: When agent is supplied, only hosts discovered by agent
        are retrieved.
        """
        trait_names = BaseWorld.get_config('reachable_host_traits') or []
        paws = ()

        if agent is not None:
            paws = paws + (agent.get('paw'),)
        else:
            for agent in operation.get('host_group', []):
                paw = agent.get('paw')
                if paw:
                    paws = paws + (paw,)

        hosts = []
        for trait in trait_names:
            fqdns = await self.services['knowledge_svc'].get_facts({
                'trait': trait,
                'collected_by': paws,
            })
            for name in fqdns:
                hosts.append(name.value)

        return hosts

    def build_executor(self, data: dict, agent: Agent):
        if not data.get('timeout'):
            data['timeout'] = 60
        data['platform'] = agent.platform
        executor = ExecutorSchema().load(data)
        return executor

    def build_ability(self, data: dict, executor: Executor):
        if not data.get('ability_id'):
            data['ability_id'] = str(uuid.uuid4())
        if not data.get('tactic'):
            data['tactic'] = 'auto-generated'
        if not data.get('technique_id'):
            data['technique_id'] = 'auto-generated'
        if not data.get('technique_name'):
            data['technique_name'] = 'auto-generated'
        if not data.get('name'):
            data['name'] = 'Manual Command'
        if not data.get('description'):
            data['description'] = 'Manual command ability'
        data['executors'] = [ExecutorSchema().dump(executor)]
        ability = AbilitySchema().load(data)
        return ability
