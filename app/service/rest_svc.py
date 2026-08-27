import asyncio
import copy
import glob
import itertools
import os
import pathlib
import uuid
from datetime import time
import re
import logging

import yaml
from aiohttp import web

from app.objects.c_adversary import Adversary
from app.objects.c_objective import Objective
from app.objects.c_operation import Operation
from app.objects.c_ability import Ability
from app.objects.c_source import Source
from app.objects.c_schedule import Schedule
from app.objects.secondclass.c_executor import Executor
from app.objects.secondclass.c_link import Link
from app.objects.secondclass.c_fact import Fact
from app.service.interfaces.i_rest_svc import RestServiceInterface
from app.utility.base_service import BaseService


class RestService(RestServiceInterface, BaseService):

    def __init__(self):
        logging.getLogger('asyncio').setLevel(logging.WARNING)
        self.log = self.add_service('rest_svc', self)
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    async def persist_adversary(self, access, data):
        """Persist adversaries. Accepts single adversary or bulk set of adversaries.
        For bulk, supply dict of form {"bulk": [{<adversary>}, {<adversary>},...]}.
        """
        if data.get('bulk', False):
            data = data['bulk']
        else:
            data = [data]
        r = []
        for adv in data:
            r.extend(await self._persist_adversary(access, adv))
        return r

    async def update_planner(self, data):
        planner = (await self.get_service('data_svc').locate('planners', dict(name=data['name'])))[0]
        planner_id = planner.planner_id
        file_path = await self._get_file_path(planner_id)
        planner_dict = await self._read_from_yaml(file_path)
        planner_dict['stopping_conditions'] = self._get_stopping_conditions(data)
        await self._write_to_yaml(file_path, planner_dict)
        planner.stopping_conditions = [Fact.load(dict(trait=f.get('trait'), value=f.get('value')))
                                       for f in data['stopping_conditions']]
        await self.get_service('data_svc').store(planner)

    async def persist_ability(self, access, data):
        """Persist abilities. Accepts single ability or bulk set of abilities.
        For bulk, supply dict of form {"bulk": [{<ability>}, {<ability>},...]}.
        """
        if data.get('bulk', False):
            data = data['bulk']
        else:
            data = [data]
        r = []
        for ab in data:
            r.append(await self._persist_ability(access, ab))
        return r

    async def persist_source(self, access, data):
        """Persist sources. Accepts single source or bulk set of sources.
        For bulk, supply dict of form {"bulk": [{<sourc>}, {<source>},...]}.
        """
        if data.get('bulk', False):
            data = data['bulk']
        else:
            data = [data]
        r = []
        for source in data:
            r.extend(await self._persist_source(access, source))
        return r

    async def persist_objective(self, access, data):
        """Persist objectives. Accepts single objective or a bulk set of objectives.
        For bulk, supply dict of form {"bulk": [{objective}, ...]}.
        """
        if data.get('bulk', False):
            data = data['bulk']
        else:
            data = [data]
        r = []
        for obj in data:
            r.extend(await self._persist_objective(access, obj))
        return r

    async def delete_agent(self, data):
        await self.get_service('data_svc').remove('agents', data)
        return 'Delete action completed'

    async def delete_ability(self, data):
        return await self._delete_data_from_memory_and_disk(ram_key='abilities', identifier='ability_id', data=data)

    async def delete_adversary(self, data):
        return await self._delete_data_from_memory_and_disk(ram_key='adversaries', identifier='adversary_id', data=data)

    async def delete_operation(self, data):
        await self.get_service('data_svc').remove('operations', data)
        await self.get_service('data_svc').remove('sources', dict(id=str(data.get('id'))))
        for f in glob.glob('data/results/*'):
            if '%s-' % data.get('id') in f:
                os.remove(f)
        for f in glob.glob('data/facts/*.yml'):
            if '%s' % data.get('id') in f:
                os.remove(f)
        return 'Delete action completed'

    async def display_objects(self, object_name, data):
        results = [o.display for o in await self.get_service('data_svc').locate(object_name, match=data)]
        return await self._explode_display_results(object_name, results)

    async def display_result(self, data):
        link_id = str(data.pop('link_id'))
        link = await self.get_service('app_svc').find_link(link_id)
        if link:
            try:
                content = self.get_service('file_svc').read_result_file('%s' % link_id)
                return dict(link=link.display, output=content)
            except FileNotFoundError:
                return dict(link=link.display, output='')
        return dict()

    async def display_operation_report(self, data):
        op_id = data.pop('op_id')
        op = (await self.get_service('data_svc').locate('operations', match=dict(id=op_id)))[0]
        report_format = data.pop('format', 'full-report')
        if report_format == 'full-report':
            generator_func = op.report
        elif report_format == 'event-logs':
            generator_func = op.event_logs
        else:
            self.log.error('Unsupported operation report format requested: %s' % report_format)
            return ''
        return await generator_func(file_svc=self.get_service('file_svc'), data_svc=self.get_service('data_svc'),
                                    output=data.get('agent_output'))

    async def download_contact_report(self, contact):
        return dict(contacts=self.get_service('contact_svc').report.get(contact.get('contact'), dict()))

    async def update_agent_data(self, data):
        paw = data.pop('paw', None)
        if paw is None:
            await self._update_global_props(**data)
            return self.get_config(name='agents')
        for agent in await self.get_service('data_svc').locate('agents', match=dict(paw=paw)):
            await agent.gui_modification(**data)
            return agent.display

    async def update_chain_data(self, data):
        link = await self.get_service('app_svc').find_link(data.pop('link_id'))
        link.status = data.get('status')
        if data.get('command'):
            link.command = data.get('command')
        return ''

    async def create_operation(self, access, data):
        operation = await self._build_operation_object(access, data)
        operation.set_start_details()
        await self.get_service('data_svc').store(operation)
        self.loop.create_task(operation.run(self.get_services()))
        return [operation.display]

    async def create_schedule(self, access, data):
        operation = await self._build_operation_object(access, data['operation'])
        schedule_id = str(uuid.uuid4())
        schedules = await self.get_service('data_svc').locate('schedules', match=dict(id=schedule_id))
        if schedules:
            self.log.debug('A Schedule with the id "%s" already exists, skipping' % schedule_id)
        else:
            scheduled = await self.get_service('data_svc').store(
                Schedule(schedule=time(data['schedule']['hour'], data['schedule']['minute'], 0),
                         task=operation, id=schedule_id)
            )
            self.log.debug('Scheduled new operation (%s) for %s' % (operation.name, scheduled.schedule))

    async def list_payloads(self):
        file_svc = self.get_service('file_svc')
        payload_dirs = [pathlib.Path.cwd() / 'data' / 'payloads']
        payload_dirs.extend(pathlib.Path.cwd() / 'plugins' / plugin.name / 'payloads'
                            for plugin in await self.get_service('data_svc').locate('plugins') if plugin.enabled)
        payloads = {
            file_svc.remove_xored_extension(p.name)
            for p in itertools.chain.from_iterable(p_dir.glob('[!.]*') for p_dir in payload_dirs)
            if p.is_file()
        }
        return payloads

    async def find_abilities(self, paw):
        data_svc = self.get_service('data_svc')
        agent = (await data_svc.locate('agents', match=dict(paw=paw)))[0]
        return await agent.capabilities(await self.get_service('data_svc').locate('abilities'))

    async def get_potential_links(self, op_id, paw=None):
        operation = (await self.get_service('data_svc').locate('operations', match=dict(id=op_id)))[0]
        if operation.finish:
            return []
        agents = await self.get_service('data_svc').locate('agents', match=dict(paw=paw)) if paw else operation.agents
        potential_abilities = await self.build_potential_abilities(operation)
        operation.potential_links = await self.build_potential_links(operation, agents, potential_abilities)
        return dict(links=[s_link.display for s_link in operation.potential_links])

    async def apply_potential_link(self, link):
        operation = await self.get_service('app_svc').find_op_with_link(link.id)
        return await operation.apply(link)

    async def add_manual_command(self, access, data):
        for parameter in ['operation', 'agent', 'executor', 'command']:
            if parameter not in data.keys():
                return dict(error='Missing parameter: %s' % parameter)

        operation_search = {'id': data['operation'], **access}
        operation = next(iter(await self.get_service('data_svc').locate('operations', match=operation_search)), None)
        if not operation:
            return dict(error='Operation not found')

        agent_search = {'paw': data['agent'], **access}
        agent = next(iter(await self.get_service('data_svc').locate('agents', match=agent_search)), None)
        if not agent:
            return dict(error='Agent not found')

        if data['executor'] not in agent.executors:
            return dict(error='Agent missing specified executor')

        encoded_command = self.encode_string(data['command'])
        ability_id = str(uuid.uuid4())

        executor = Executor(name=data['executor'], platform=agent.platform, command=data['command'])
        ability = Ability(ability_id=ability_id, tactic='auto-generated', technique_id='auto-generated',
                          technique_name='auto-generated', name='Manual Command', description='Manual command ability',
                          executors=[executor])
        link = Link.load(dict(command=encoded_command, paw=agent.paw, cleanup=0, ability=ability, score=0, jitter=2,
                              executor=executor, status=operation.link_status()))
        link.apply_id(agent.host)
        operation.add_link(link)

        return dict(link=link.unique)

    async def task_agent_with_ability(self, paw, ability_id, obfuscator, facts=()):
        new_links = []
        for agent in await self.get_service('data_svc').locate('agents', dict(paw=paw)):
            self.log.debug('Tasking %s with %s' % (paw, ability_id))
            links = await agent.task(
                abilities=await self.get_service('data_svc').locate('abilities', match=dict(ability_id=ability_id)),
                obfuscator=obfuscator,
                facts=facts
            )
            new_links.extend(links)
        return new_links

    async def get_link_pin(self, json_data):
        link = await self.get_service('app_svc').find_link(json_data['link'])
        if link and link.collect and not link.finish:
            return link.pin
        return 0

    async def construct_agents_for_group(self, group):
        if group:
            return await self.get_service('data_svc').locate('agents', match=dict(group=group))
        return await self.get_service('data_svc').locate('agents')

    async def update_operation(self, op_id, state=None, autonomous=None, obfuscator=None):
        async def validate(op):
            try:
                if not len(op):
                    raise web.HTTPNotFound
                elif await op[0].is_finished():
                    raise web.HTTPBadRequest(body='This operation has already finished.')
                elif state not in op[0].states.values():
                    raise web.HTTPBadRequest(body='state must be one of {}'.format(op[0].states.values()))
            except Exception as e:
                self.log.error(repr(e))
        operation = await self.get_service('data_svc').locate('operations', match=dict(id=op_id))
        if state:
            await validate(operation)
            operation[0].state = state
            if state == operation[0].states['FINISHED']:
                operation[0].finish = self.get_current_timestamp()
            self.log.debug('Changing operation=%s state to %s' % (op_id, state))
        if autonomous:
            operation[0].autonomous = 0 if operation[0].autonomous else 1
            self.log.debug('Toggled operation=%s autonomous to %s' % (op_id, bool(operation[0].autonomous)))
        if obfuscator:
            operation[0].obfuscator = obfuscator
            self.log.debug('Updated operation=%s obfuscator to %s' % (op_id, operation[0].obfuscator))

    async def get_agent_configuration(self, data):
        abilities = await self.get_service('data_svc').locate('abilities', data)

        raw_abilities = []
        for ability in abilities:
            for executor in ability.executors:
                variations = [{'description': v.description, 'command': v.raw_command} for v in executor.variations]
                raw_abilities.append({'platform': executor.platform, 'executor': executor.name,
                                      'description': ability.description, 'command': executor.command,
                                      'variations': variations})

        app_config = {k: v for k, v in self.get_config().items() if k.startswith('app.')}
        app_config.update({'agents.%s' % k: v for k, v in self.get_config(name='agents').items()})

        return dict(abilities=raw_abilities, app_config=app_config)

    async def list_exfil_files(self, data):
        files = self.get_service('file_svc').list_exfilled_files()
        if data.get('operation_id'):
            folders = await self._get_operation_exfil_folders(data['operation_id'])
            for key in list(files.keys()):
                if key not in folders:
                    files.pop(key, None)
        return files

    async def build_potential_abilities(self, operation):
        potential_abilities = []
        for a in await self.get_service('data_svc').locate('abilities', match=dict(access=operation.access)):
            if not operation.adversary.has_ability(a.ability_id):
                potential_abilities.append(a)
        return potential_abilities

    async def build_potential_links(self, operation, agents, abilities):
        potential_links = []
        for a in agents:
            for pl in await self.get_service('planning_svc').generate_and_trim_links(a, operation, abilities):
                potential_links.append(pl)
        return await self.get_service('planning_svc').sort_links(potential_links)

    async def _build_operation_object(self, access, data):
        name = data.pop('name')
        group = data.pop('group', '')
        planner = await self.get_service('data_svc').locate('planners', match=dict(name=data.get('planner', 'atomic')))
        adversary = await self._construct_adversary_for_op(data.pop('adversary_id', ''))
        agents = await self.construct_agents_for_group(group)
        sources = await self.get_service('data_svc').locate('sources', match=dict(name=data.pop('source', 'basic')))
        allowed = self._get_allowed_from_access(access)

        return Operation(name=name, planner=planner[0], agents=agents, adversary=adversary,
                         group=group, jitter=data.pop('jitter', '2/8'), source=next(iter(sources), None),
                         state=data.pop('state', 'running'), autonomous=int(data.pop('autonomous', 1)), access=allowed,
                         obfuscator=data.pop('obfuscator', 'plain-text'),
                         auto_close=bool(int(data.pop('auto_close', 0))), visibility=int(data.pop('visibility', '50')),
                         use_learning_parsers=bool(int(data.pop('use_learning_parsers', 0))))

    def _get_allowed_from_access(self, access):
        if self.Access.HIDDEN in access['access']:
            return self.Access.HIDDEN
        elif self.Access.BLUE in access['access']:
            return self.Access.BLUE
        else:
            return self.Access.RED

    @staticmethod
    async def _read_from_yaml(file_path):
        with open(file_path, 'r') as f:
            return yaml.safe_load(f.read())

    @staticmethod
    async def _write_to_yaml(file_path, content):
        with open(file_path, 'w') as f:
            f.write(yaml.dump(content))

    async def _get_file_path(self, planner_id):
        _, file_path = await self.get_service('file_svc').find_file_path('%s.yml' % planner_id, location='data')
        if not file_path:
            file_path = 'data/planners/%s.yml' % planner_id
        return file_path

    @staticmethod
    def _get_stopping_conditions(data):
        new_stopping_conditions = data.get('stopping_conditions')
        if new_stopping_conditions:
            return [{s.get('trait'): s.get('value')} for s in new_stopping_conditions]

    async def _construct_adversary_for_op(self, adversary_id):
        adv = await self.get_service('data_svc').locate('adversaries', match=dict(adversary_id=adversary_id))
        if adv:
            return copy.deepcopy(adv[0])
        return Adversary.load(dict(adversary_id='ad-hoc', name='ad-hoc', description='an empty adversary profile',
                                   atomic_ordering=[]))

    async def _update_global_props(self, sleep_min, sleep_max, watchdog, untrusted, implant_name,
                                   bootstrap_abilities, deadman_abilities):
        """Update global agent properties

        :param sleep_min: Beacon min sleep time (seconds)
        :type sleep_min: int
        :param sleep_max: Beacon max sleep time (seconds)
        :type sleep_max: int
        :param watchdog: Watchdog timer (seconds)
        :type watchdog: int
        :param untrusted: Untrusted timer (seconds)
        :type untrusted: int
        :param implant_name: Agent file name
        :type implant_name: str
        :param bootstrap_abilities: Comma-separated ability UUIDs
        :type bootstrap_abilities: str
        :param deadman_abilities: Comma-separated ability UUIDs
        :type deadman_abilities: str
        """
        self.set_config(name='agents', prop='sleep_min', value=sleep_min)
        self.set_config(name='agents', prop='sleep_max', value=sleep_max)
        self.set_config(name='agents', prop='untrusted_timer', value=untrusted)
        self.set_config(name='agents', prop='watchdog', value=watchdog)
        if implant_name:
            self.set_config(name='agents', prop='implant_name', value=implant_name)
        if bootstrap_abilities is not None:
            await self._update_agent_ability_list_property(bootstrap_abilities, 'bootstrap_abilities')

        if deadman_abilities is not None:
            await self._update_agent_ability_list_property(deadman_abilities, 'deadman_abilities')

    async def _update_agent_ability_list_property(self, abilities_str, prop_name):
        """Set the specified agent config property with the specified abilities.

        :param abilities_str: Comma-separated ability UUIDs
        :type abilities_str: str
        :param prop_name: name of the configuration property to set (e.g. 'bootstrap_abilities', 'deadman_abilities')
        :type prop_name: str
        """
        abilities = []
        ability_id_list = [ability_id.strip() for ability_id in abilities_str.split(',') if ability_id.strip()]
        for ability_id in ability_id_list:
            if await self.get_service('data_svc').locate('abilities', dict(ability_id=ability_id.strip())):
                abilities.append(ability_id)
            else:
                self.log.debug('Could not find ability with id "{}" for property "{}"'.format(ability_id, prop_name))
        self.set_config(name='agents', prop=prop_name, value=abilities)

    async def _explode_display_results(self, object_name, results):
        if object_name == 'adversaries':
            for adv in results:
                adv['atomic_ordering'] = [ab.display for ab_id in adv['atomic_ordering'] for ab in
                                          await self.get_service('data_svc').locate('abilities',
                                                                                    match=dict(ability_id=ab_id))]
                if adv['objective']:
                    objectives = await self.get_service('data_svc').locate('objectives',
                                                                           match=dict(id=adv['objective']))
                    if objectives:
                        adv['objective'] = objectives[0].display
        return results

    async def _delete_data_from_memory_and_disk(self, ram_key, identifier, data):
        await self.get_service('data_svc').remove(ram_key, data)
        _, file_path = await self.get_service('file_svc').find_file_path('%s.yml' % data.get(identifier),
                                                                         location='data')
        if not file_path:
            file_path = 'data/%s/%s.yml' % (ram_key, data.get(identifier))
        if os.path.exists(file_path):
            os.remove(file_path)
        return 'Delete action completed'

    async def _persist_adversary(self, access, adv):
        """Persist adversary.

        Current policy for 'objective' field of adversary: If there isn't an
        objective when an adversary is loaded from disk, it gets assigned a
        default one, which will then get written out if the adversary is
        explicitly saved. Newly created adversaries will also be given default
        objective if created without one.
        """
        if not adv.get('id') or not adv['id']:
            adv['id'] = str(uuid.uuid4())
        obj_default = (await self._services.get('data_svc').locate('objectives', match=dict(name='default')))[0]
        adv['atomic_ordering'] = [list(ab_dict.values())[0] for ab_dict in adv['atomic_ordering']]
        _, file_path = await self.get_service('file_svc').find_file_path('%s.yml' % adv['id'], location='data')
        if file_path:
            # exists
            current_adv = dict(self.strip_yml(file_path)[0])
            allowed = (await self.get_service('data_svc').locate('adversaries', dict(adversary_id=adv['id'])))[0].access
            current_adv.update(adv)
            final = current_adv
        else:
            # new
            file_path = 'data/adversaries/%s.yml' % adv['id']
            allowed = self._get_allowed_from_access(access)
            adv['objective'] = adv.get('objective', obj_default)
            final = adv
        # verfiy objective is valid
        if len(await self.get_service('data_svc').locate('objectives', match=dict(id=final['objective']))) == 0:
            final['objective'] = obj_default.id
        await self._save_and_refresh_item(file_path, Adversary, final, allowed)
        stored_adv = await self._services.get('data_svc').locate('adversaries', dict(adversary_id=final["id"]))
        for a in stored_adv:
            a.has_repeatable_abilities = a.check_repeatable_abilities(self.get_service('data_svc').ram['abilities'])
        return [a.display for a in stored_adv]

    async def _persist_ability(self, access, ab):
        """Persist ability.

        The model/format of the incoming ability (i.e. 'ab') is most similar to the ability
        yaml file definition, with a few exceptions:
          - 'platforms' sub-dict has sub executor keys split out versus a joined csv string
          - 'platforms' executor sub-dicts dont have a 'parsers' field
          - 'platforms' executor sub-dicts have a 'timeout' field

        Update Strategy:
            'new' ability is the ability dict that is supplied
            'current' ability is the ability dict as read in directly from yaml file
            ------------
            - on new ability, stash executor timeouts and then drop from new ability
            - on new ability, combine executors that are the same under common platform
            - on current ability, stash parsers and then drop from current ability
            - update current ability with new ability
            - add parsers back in to current ability
            - save current ability to disk, then re-load ability from file
        :param access: Current access list
        :type access: dict
        :param ab: Ability to add or
        :type ab: dict
        :return: Created / updated ability
        :rtype: Ability
        """
        # Set ability ID if undefined
        if not ab.get('id'):
            ab['id'] = str(uuid.uuid4())

        # Validate ID, used for file creation
        validator = re.compile(r'^[a-zA-Z0-9-_]+$')
        if not ab.get('id') or not validator.match(ab.get('id')):
            self.log.debug('Invalid ability ID "%s". IDs can only contain '
                           'alphanumeric characters, hyphens, and underscores.' % ab.get('id'))
            return []

        # Validate tactic, used for directory creation, lower case if present
        if not ab.get('tactic') or not validator.match(ab.get('tactic')):
            self.log.debug('Invalid ability tactic "%s". Tactics can only contain '
                           'alphanumeric characters, hyphens, and underscores.' % ab.get('tactic'))
            return []
        ab['tactic'] = ab.get('tactic').lower()

        # Validate platforms, ability will not be loaded if empty
        if not ab.get('platforms'):
            self.log.debug('At least one executor is required to save ability.')
            return []

        # Update or create ability
        _, file_path = await self.get_service('file_svc').find_file_path('{}.yml'.format(ab['id']), location='data')
        if file_path:
            # Ability exists, update
            current_ability = dict(self.strip_yml(file_path)[0][0])
            current_ability, current_parsers = await self._strip_parsers_from_ability(current_ability)
            current_ability.update(ab)
            final = await self._add_parsers_to_ability(current_ability, current_parsers)
            # Get access
            found_abilities = await self.get_service('data_svc').locate('abilities', dict(ability_id=ab['id']))
            allowed = found_abilities[0].access if found_abilities else self._get_allowed_from_access(access)
        else:
            # Create new ability, create file / directory
            tactic_dir = os.path.join('data', 'abilities', ab.get('tactic'))
            if not os.path.exists(tactic_dir):
                os.makedirs(tactic_dir)
            file_path = os.path.join(tactic_dir, '%s.yml' % ab['id'])
            final = ab
            # Get access
            allowed = self._get_allowed_from_access(access)

        await self.get_service('file_svc').save_file(file_path, yaml.dump([final], encoding='utf-8', sort_keys=False),
                                                     '', encrypt=False)
        await self.get_service('data_svc').remove('abilities', dict(ability_id=final['id']))
        await self.get_service('data_svc').load_ability_file(file_path, allowed)

        loaded_abilities = await self.get_service('data_svc').locate('abilities', dict(ability_id=final['id']))
        if not loaded_abilities:
            self.log.error('An error occurred when attempting to load the newly-created ability')
            return {}
        return loaded_abilities[0].display

    async def _persist_source(self, access, source):
        return await self._persist_item(access, 'sources', Source, source)

    async def _persist_objective(self, access, objective):
        return await self._persist_item(access, 'objectives', Objective, objective)

    async def _persist_item(self, access, object_class_name, object_class, item):
        if not item.get('id') or not item['id']:
            item['id'] = str(uuid.uuid4())
        _, file_path = await self.get_service('file_svc').find_file_path('%s.yml' % item['id'], location='data')
        if file_path:
            current_item = dict(self.strip_yml(file_path)[0])
            allowed = (await self.get_service('data_svc').locate(object_class_name, dict(id=item['id'])))[0].access
            current_item.update(item)
            final = item
        else:
            file_path = 'data/%s/%s.yml' % (object_class_name, item['id'])
            allowed = self._get_allowed_from_access(access)
            final = item
        await self._save_and_refresh_item(file_path, object_class, final, allowed)
        return [i.display for i in await self.get_service('data_svc').locate(object_class_name, dict(id=final['id']))]

    async def _save_and_refresh_item(self, file_path, object_class, final, allowed):
        await self.get_service('file_svc').save_file(file_path, yaml.dump(final, encoding='utf-8', sort_keys=False),
                                                     '', encrypt=False)
        await self.get_service('data_svc').load_yaml_file(object_class, file_path, allowed)

    async def _strip_parsers_from_ability(self, ability):
        """Remove the parsers sub-dict from the executors of an ability
        (where the ability is not an ability object but just the loaded
        dict from yaml ability file)

        Return ability (minus parsers) and parsers as seperate dict
        """
        parsers = {}
        for platform, executors in ability.get('platforms', {}).items():
            parsers[platform] = {}
            for executor, d in executors.items():
                if d.get('parsers', False):
                    parsers[platform][executor] = d['parsers']
                    del ability["platforms"][platform][executor]['parsers']
        return ability, parsers

    async def _add_parsers_to_ability(self, ability, parsers):
        """Add parsers back into an ability (where the ability is
        not an ability object but just the loaded dict from yaml
        ability file)
        """
        for platform, executors in ability.get('platforms', {}).items():
            if parsers.get(platform, False):
                for executor, _ in executors.items():
                    if parsers[platform].get(executor, False):
                        ability['platforms'][platform][executor]['parsers'] = parsers[platform][executor]
        return ability

    async def _get_operation_exfil_folders(self, operation_id):
        op = (await self.get_service('data_svc').locate('operations', match=dict(id=operation_id)))[0]
        return [a.paw for a in op.agents]
