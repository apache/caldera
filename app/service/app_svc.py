import asyncio
import copy
import glob
import hashlib
import json
import os
import re
import time
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from importlib import import_module

import aiohttp_jinja2
import jinja2
import yaml
from aiohttp import web
import croniter

from app.objects.c_adversary import Adversary
from app.objects.c_objective import Objective
from app.objects.c_plugin import Plugin
from app.objects.c_source import Source
from app.service.interfaces.i_app_svc import AppServiceInterface
from app.utility.base_service import BaseService

Error = namedtuple('Error', ['name', 'msg', 'optional'])


class AppService(AppServiceInterface, BaseService):

    @property
    def errors(self):
        return [dict(e._asdict()) for e in self._errors]

    def __init__(self, application):
        self.application = application
        self.log = self.add_service('app_svc', self)
        self.loop = asyncio.get_event_loop()
        self._errors = []
        self._loaded_plugins = []  # all plugins that were loaded, including disabled ones

    async def start_sniffer_untrusted_agents(self):
        next_check = self.get_config(name='agents', prop='untrusted_timer')
        try:
            while True:
                await asyncio.sleep(next_check + 1)
                trusted_agents = await self.get_service('data_svc').locate('agents', match=dict(trusted=1))
                next_check = self.get_config(name='agents', prop='untrusted_timer')
                for a in trusted_agents:
                    silence_time = (datetime.now(timezone.utc) - a.last_trusted_seen).total_seconds()
                    if silence_time > (self.get_config(name='agents', prop='untrusted_timer') + int(a.sleep_max)):
                        self.log.debug('Agent (%s) now untrusted. Last seen %s sec ago' % (a.paw, int(silence_time)))
                        a.trusted = 0
                        await self.update_operations_with_untrusted_agent(a)
                    else:
                        trust_time_left = self.get_config(name='agents', prop='untrusted_timer') - silence_time
                        if trust_time_left < next_check:
                            next_check = trust_time_left
                await asyncio.sleep(15)
        except Exception as e:
            self.log.error(repr(e), exc_info=True)

    async def update_operations_with_untrusted_agent(self, untrusted_agent):
        all_operations = await self.get_service('data_svc').locate('operations')
        for op in all_operations:
            if (not await op.is_finished()) and any(untrusted_agent.paw == agent.paw for agent in op.agents):
                op.update_untrusted_agents(untrusted_agent)

    async def find_link(self, unique):
        operations = await self.get_service('data_svc').locate('operations')
        agents = await self.get_service('data_svc').locate('agents')
        return self._check_links_for_match(unique, [op.chain for op in operations] + [a.links for a in agents])

    async def find_op_with_link(self, link_id):
        """
        Retrieves the operation that a link_id belongs to. Will search currently running
        operations first.
        """
        operations = await self.get_service('data_svc').locate('operations', match=dict(state='running'))
        op = next((o for o in operations if o.has_link(link_id)), None)
        if not op:
            operations = await self.get_service('data_svc').locate('operations', match=dict())
            op = next((o for o in operations if o.has_link(link_id)), None)
        return op

    async def run_scheduler(self):
        while True:
            interval = 60
            for s in await self.get_service('data_svc').locate('schedules'):
                if not croniter.croniter.is_valid(s.schedule):
                    match = re.match(r'^(\d{2}):(\d{2}):\d{2}\.\d{6}$', s.schedule)
                    if match:
                        hour, minute = match.groups()
                        s.schedule = f"{minute} {hour} * * *"
                        self.log.info(f"Converted time schedule {s.id} to cron format: {s.schedule}")
                    else:
                        self.log.warning(f"The schedule {s.id} with the format `{s.schedule}` is incompatible with cron!")
                        continue

                now = datetime.now()
                cron = croniter.croniter(s.schedule, now)
                diff = now - cron.get_prev(datetime)
                if interval > diff.total_seconds() > 0:
                    self.log.debug('Pulling %s off the scheduler' % s.id)
                    sop = copy.deepcopy(s.task)
                    sop.id = str(uuid.uuid4())
                    sop.name += f" ({datetime.now(timezone.utc).replace(microsecond=0).isoformat()})"
                    sop.set_start_details()
                    await sop.update_operation_agents(self.get_services())
                    await self._services.get('data_svc').store(sop)
                    self.loop.create_task(sop.run(self.get_services()))
            await asyncio.sleep(interval)

    async def resume_operations(self):
        await asyncio.sleep(10)
        for op in await self.get_service('data_svc').locate('operations', match=dict(finish=None)):
            self.loop.create_task(op.run(self.get_services()))

    async def load_plugins(self, plugins):
        def trim(p):
            if p.startswith('.'):
                return False
            return True

        async def load(p):
            plugin = Plugin(name=p)
            if plugin.load_plugin():
                await self.get_service('data_svc').store(plugin)
                self._loaded_plugins.append(plugin)

            if plugin.name in self.get_config('plugins') or plugin.name == 'magma':
                await plugin.enable(self.get_services())
                self.log.info('Enabled plugin: %s' % plugin.name)

        for plug in filter(trim, plugins):
            if not os.path.isdir('plugins/%s' % plug) or not os.path.isfile('plugins/%s/hook.py' % plug):
                self.log.error('Problem locating the "%s" plugin. Ensure code base was cloned recursively.' % plug)
                exit(0)
            asyncio.get_event_loop().create_task(load(plug))

        templates = ['plugins/%s/templates' % p.lower() for p in self.get_config('plugins')]
        magma_dist = 'plugins/magma/dist'
        if os.path.exists(magma_dist):
            templates.append(magma_dist)
        else:
            self.log.warning('Magma plugin dist not found at %s — web UI will not be available. '
                             'Run with --build or build the Magma plugin manually.', magma_dist)
        aiohttp_jinja2.setup(self.application, loader=jinja2.FileSystemLoader(templates))

    async def retrieve_compiled_file(self, name, platform, location=''):
        _, path = await self._services.get('file_svc').find_file_path('%s-%s' % (name, platform), location=location)
        signature = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        display_name = await self._services.get('contact_svc').build_filename()
        self.log.debug('%s downloaded with hash=%s and name=%s' % (name, signature, display_name))
        return '%s-%s' % (name, platform), display_name

    async def teardown(self, main_config_file='default'):
        await self._destroy_plugins()
        await self._deregister_contacts()
        await self._save_configurations(main_config_file=main_config_file)
        await self._services.get('data_svc').save_state()
        await self._services.get('knowledge_svc').save_state()
        await self._write_reports()
        self.log.debug('[!] shutting down server...good-bye')

    async def register_contacts(self):
        contact_svc = self.get_service('contact_svc')
        for contact_file in glob.iglob('app/contacts/*.py'):
            contact_module_name = contact_file.replace('/', '.').replace('\\', '.').replace('.py', '')
            contact_class = import_module(contact_module_name).Contact
            await contact_svc.register_contact(contact_class(self.get_services()))
        await self.register_contact_tunnels(contact_svc)

    async def register_contact_tunnels(self, contact_svc):
        for tunnel_file in glob.iglob('app/contacts/tunnels/*.py'):
            tunnel_module_name = tunnel_file.replace('/', '.').replace('\\', '.').replace('.py', '')
            tunnel_class = import_module(tunnel_module_name).Tunnel
            await contact_svc.register_tunnel(tunnel_class(self.get_services()))

    async def _deregister_contacts(self):
        contact_svc = self.get_service('contact_svc')
        await contact_svc.deregister_contacts()

    async def validate_requirement(self, requirement, params):
        if not self.check_requirement(params):
            msg = '%s does not meet the minimum version of %s' % (requirement, params['version'])
            if params.get('optional', False):
                msg = '. '.join([
                    msg,
                    '%s is an optional dependency which adds more functionality' % requirement.capitalize(),
                    params.get('reason', '')
                ])
                self.log.warning(msg)
            else:
                self.log.error(msg)
            self._errors.append(Error('requirement', '%s version needs to be >= %s' % (requirement, params['version']), params.get('optional')))
            return False
        return True

    async def validate_requirements(self):
        for requirement, params in self.get_config('requirements').items():
            await self.validate_requirement(requirement, params)

    async def load_plugin_expansions(self, plugins=()):
        for p in plugins:
            await p.expand(services=self.get_services())

    async def watch_ability_files(self):
        await asyncio.sleep(int(self.get_config('ability_refresh')))
        plugins = [p for p in await self.get_service('data_svc').locate('plugins', dict(enabled=True)) if p.data_dir]
        plugins.append(Plugin(data_dir='data'))
        while True:
            reloaded = False
            for p in plugins:
                files = (os.path.join(rt, fle) for rt, _, f in os.walk(p.data_dir+'/abilities') for fle in f if
                         time.time() - os.stat(os.path.join(rt, fle)).st_mtime < int(self.get_config('ability_refresh')))
                for f in files:
                    if not f.endswith(('.yml', '.yaml')):
                        self.log.debug('[%s] Skipping non YML file %s' % (p.name, f))
                        continue
                    self.log.debug('[%s] Reloading %s' % (p.name, f))
                    await self.get_service('data_svc').load_ability_file(filename=f, access=p.access)
                    reloaded = True
            if reloaded:
                self.log.debug('Ability files changed — rebuilding learning model')
                await self.get_service('learning_svc').build_model()
            await asyncio.sleep(int(self.get_config('ability_refresh')))

    async def watch_adversary_files(self):
        """Watch for new/modified adversary YAML files and reload them into RAM."""
        await self._watch_yaml_files('adversaries', Adversary)

    async def watch_source_files(self):
        """Watch for new/modified source YAML files and reload them into RAM."""
        await self._watch_yaml_files('sources', Source)

    async def watch_objective_files(self):
        """Watch for new/modified objective YAML files and reload them into RAM."""
        await self._watch_yaml_files('objectives', Objective)

    async def _watch_yaml_files(self, data_type, object_class):
        """Generic file watcher for YAML-based data types (adversaries, sources, objectives).

        Scans plugin and core data directories for new or modified YAML files
        and reloads them into RAM via data_svc.load_yaml_file().
        """
        refresh = int(self.get_config('ability_refresh'))
        await asyncio.sleep(refresh)
        plugins = [p for p in await self.get_service('data_svc').locate('plugins', dict(enabled=True)) if p.data_dir]
        plugins.append(Plugin(data_dir='data'))
        while True:
            for p in plugins:
                data_dir = os.path.join(p.data_dir, data_type)
                if not os.path.isdir(data_dir):
                    continue
                files = (os.path.join(rt, fle) for rt, _, f in os.walk(data_dir) for fle in f if
                         time.time() - os.stat(os.path.join(rt, fle)).st_mtime < refresh)
                for f in files:
                    if not f.endswith(('.yml', '.yaml')):
                        self.log.debug('[%s] Skipping non YML file %s' % (p.name, f))
                        continue
                    self.log.debug('[%s] Reloading %s %s' % (p.name, data_type, f))
                    await self.get_service('data_svc').load_yaml_file(object_class, filename=f, access=p.access)
            refresh = int(self.get_config('ability_refresh'))
            await asyncio.sleep(refresh)

    def register_subapp(self, path: str,  app: web.Application):
        """Registers a web application under the root application.

        Requests under `path` will be routed to this app.
        """
        self.application.add_subapp(path, app)

    def get_loaded_plugins(self):
        return tuple(self._loaded_plugins)

    async def _save_configurations(self, main_config_file='default'):
        for cfg_name, cfg_file in [('main', main_config_file), ('agents', 'agents'), ('payloads', 'payloads')]:
            with open('conf/%s.yml' % cfg_file, 'w') as config:
                config.write(yaml.dump(self.get_config(name=cfg_name)))

    async def _destroy_plugins(self):
        for plugin in await self._services.get('data_svc').locate('plugins', dict(enabled=True)):
            await plugin.destroy(self.get_services())

    async def _write_reports(self):
        file_svc = self.get_service('file_svc')
        r_dir = await file_svc.create_exfil_sub_directory('%s/reports' % self.get_config('reports_dir'))
        report = json.dumps(dict(self.get_service('contact_svc').report)).encode()
        await file_svc.save_file('contact_reports', report, r_dir)
        for op in await self.get_service('data_svc').locate('operations'):
            report = json.dumps(await op.report(self.get_service('file_svc'), self.get_service('data_svc')))
            if report:
                await file_svc.save_file('operation_%s' % op.id, report.encode(), r_dir)

    @staticmethod
    def _check_links_for_match(unique, links):
        for ll in links:
            exists = next((link for link in ll if link.unique == str(unique)), None)
            if exists:
                return exists
