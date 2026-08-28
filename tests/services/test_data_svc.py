import glob
import json
import logging
import yaml

from unittest import mock
from unittest.mock import patch, call

from app.objects.c_ability import Ability
from app.objects.c_adversary import Adversary
from app.objects.c_agent import Agent
from app.objects.c_operation import Operation
from app.objects.c_planner import Planner
from app.objects.c_plugin import Plugin
from app.objects.secondclass.c_executor import Executor
from app.service.data_svc import DataService
from app.utility.base_world import BaseWorld


PAYLOAD_CONFIG_YAMLS = {
    'path1': [yaml.safe_load('''
---
id: testid1
name: Plugin1 Payloads
standard_payloads:
  file.exe:
    description: file desc
    id: fileid1
  file.ps1:
    description: file desc
    id: fileid2
special_payloads:
  file.go:
    description: file desc
    id: specialid1
    service: stockpile_svc
    function: funcname
extensions:
  .donut: plugins.stockpile.app.donut.donut_handler
''')],
    'path2': [yaml.safe_load('''
---
id: testid2
name: Plugin1 Payloads 2
standard_payloads:
  file.py:
    description: file desc
    id: fileid3
  file.txt:
    description: file desc
    id: fileid4
special_payloads:
  file.cpp:
    description: file desc
    id: specialid2
    service: stockpile_svc
    function: funcname
''')],
    'path3': [yaml.safe_load('''
---
id: testid3
name: Plugin2 Payloads
special_payloads:
  special.py:
    description: file desc
    id: specialid3
    service: stockpile_svc
    function: funcname
  file.cpp:
    description: overridden desc
    id: overridden
    service: stockpile_svc
    function: overridden
extensions:
  .testext: handler
''')],
}


ABILITY_YAMLS = {
    'plugins/testing/data/discovery/764efa883dda1e11db47671c4a3bbd9e.yml': [yaml.safe_load('''
---

- id: 764efa883dda1e11db47671c4a3bbd9e
  name: Find deletable dirs (per user)
  description: Discover all directories containing deletable files by user
  tactic: discovery
  technique:
    attack_id: T1082
    name: System Information Discovery
  platforms:
    darwin:
      sh:
        command: |
          testcommand
    linux:
      sh:
        command: |
          testcommand
''')],
    'plugins/testing/data/discovery/848aa201-4b00-4f08-ae3a-3e84dfb5065c.yml': [yaml.safe_load('''
---

- id: 848aa201-4b00-4f08-ae3a-3e84dfb5065c
  name: Find deletable dirs (per user)
  description: Discover all directories containing deletable files by user
  tactic: discovery
  technique:
    attack_id: T1082
    name: System Information Discovery
  platforms:
    darwin:
      sh:
        command: |
          testcommand
    linux:
      sh:
        command: |
          testcommand
''')],
    'plugins/testing/data/discovery/101.yml': [yaml.safe_load('''
---

- id: 101
  name: Find deletable dirs (per user)
  description: Discover all directories containing deletable files by user
  tactic: purposefullywrongtactic
  technique:
    attack_id: T1082
    name: System Information Discovery
  platforms:
    darwin:
      sh:
        command: |
          testcommand
    linux:
      sh:
        command: |
          testcommand
''')],
    'plugins/testing/data/discovery/102.yml': [yaml.safe_load('''
malformed
''')],
}


def strip_payload_yaml(path):
    return PAYLOAD_CONFIG_YAMLS.get(path, [])


def strip_ability_yaml(path):
    return ABILITY_YAMLS.get(path, [])


class TestDataService:
    mock_payload_config = dict()

    def test_no_duplicate_adversary(self, event_loop, data_svc):
        event_loop.run_until_complete(data_svc.store(
            Adversary(adversary_id='123', name='test', description='test adversary', atomic_ordering=list())
        ))
        event_loop.run_until_complete(data_svc.store(
            Adversary(adversary_id='123', name='test', description='test adversary', atomic_ordering=list())
        ))
        adversaries = event_loop.run_until_complete(data_svc.locate('adversaries'))

        assert len(adversaries) == 1
        for x in adversaries:
            json.dumps(x.display)

    def test_no_duplicate_planner(self, event_loop, data_svc):
        event_loop.run_until_complete(data_svc.store(Planner(name='test', planner_id='some_id', module='some.path.here', params=None, description='description')))
        event_loop.run_until_complete(data_svc.store(Planner(name='test', planner_id='some_id', module='some.path.here', params=None, description='description')))
        planners = event_loop.run_until_complete(data_svc.locate('planners'))

        assert len(planners) == 1
        for x in planners:
            json.dumps(x.display)

    def test_multiple_agents(self, event_loop, data_svc):
        event_loop.run_until_complete(data_svc.store(Agent(sleep_min=2, sleep_max=8, watchdog=0)))
        event_loop.run_until_complete(data_svc.store(Agent(sleep_min=2, sleep_max=8, watchdog=0)))
        agents = event_loop.run_until_complete(data_svc.locate('agents'))

        assert len(agents) == 2
        for x in agents:
            json.dumps(x.display)

    def test_no_duplicate_ability(self, event_loop, data_svc):
        executor = Executor(name='special_executor', platform='darwin', command='whoami', payloads=['wifi.sh'])
        event_loop.run_until_complete(data_svc.store(
            Ability(ability_id='123', tactic='discovery', technique_id='1', technique_name='T1033', name='test',
                    description='find active user', privilege=None, executors=[executor])
        ))
        event_loop.run_until_complete(data_svc.store(
            Ability(ability_id='123', tactic='discovery', technique_id='1', technique_name='T1033', name='test',
                    description='find active user', privilege=None, executors=[executor])
        ))
        abilities = event_loop.run_until_complete(data_svc.locate('abilities'))

        assert len(abilities) == 1

    def test_operation(self, event_loop, data_svc):
        adversary = event_loop.run_until_complete(data_svc.store(
            Adversary(adversary_id='123', name='test', description='test adversary', atomic_ordering=list())
        ))
        event_loop.run_until_complete(data_svc.store(Operation(name='my first op', agents=[], adversary=adversary)))

        operations = event_loop.run_until_complete(data_svc.locate('operations'))
        assert len(operations) == 1
        for x in operations:
            json.dumps(x.display)

    def test_remove(self, event_loop, data_svc):
        a1 = event_loop.run_until_complete(data_svc.store(Agent(sleep_min=2, sleep_max=8, watchdog=0)))
        agents = event_loop.run_until_complete(data_svc.locate('agents', match=dict(paw=a1.paw)))
        assert len(agents) == 1
        event_loop.run_until_complete(data_svc.remove('agents', match=dict(paw=a1.paw)))
        agents = event_loop.run_until_complete(data_svc.locate('agents', match=dict(paw=a1.paw)))
        assert len(agents) == 0

    def test_no_autogen_cleanup_cmds(self, event_loop, data_svc):
        cleanup_executor = Executor(name='sh', platform='linux', cleanup='rm #{payload}')
        event_loop.run_until_complete(data_svc.store(
            Ability(ability_id='4cd4eb44-29a7-4259-91ae-e457b283a880', tactic='defense-evasion', technique_id='T1070.004',
                    technique_name='Indicator Removal on Host: File Deletion', name='Delete payload',
                    description='Remove a downloaded payload file', privilege=None, executors=[cleanup_executor])
        ))
        executor = Executor(name='special_executor', platform='darwin', command='whoami', payloads=['wifi.sh'])
        event_loop.run_until_complete(data_svc.store(
            Ability(ability_id='123', tactic='discovery', technique_id='1', technique_name='T1033', name='test',
                    description='find active user', privilege=None, executors=[executor])
        ))
        event_loop.run_until_complete(data_svc._verify_abilities())
        abilities = event_loop.run_until_complete(data_svc.locate('abilities', dict(ability_id='123')))

        for ability in abilities:
            for executor in ability.executors:
                assert not executor.cleanup

    @mock.patch.object(BaseWorld, 'strip_yml', wraps=strip_payload_yaml)
    @mock.patch.object(DataService, '_apply_special_payload_hooks', return_value=None)
    @mock.patch.object(DataService, '_apply_special_extension_hooks', return_value=None)
    def test_load_payloads(self, mock_ext_hooks, mock_payload_hooks, mock_strip_yml, event_loop, data_svc):
        def _mock_apply_payload_config(config=None, **_):
            TestDataService.mock_payload_config = config

        test_plugin = Plugin(data_dir='test_plugin/data')
        test_plugin2 = Plugin(data_dir='test_plugin2/data')
        with patch.object(glob, 'iglob', return_value=['path1', 'path2']) as mock_iglob:
            with patch.object(BaseWorld, 'apply_config', wraps=_mock_apply_payload_config) as mock_apply_config:
                with patch.object(DataService, 'get_config', return_value=self.mock_payload_config):
                    event_loop.run_until_complete(data_svc._load_payloads(test_plugin))
        mock_iglob.assert_called_once_with('test_plugin/data/payloads/*.yml', recursive=False)
        mock_strip_yml.assert_has_calls([call('path1'), call('path2')])
        mock_payload_hooks.assert_has_calls([
            call(PAYLOAD_CONFIG_YAMLS['path1'][0]['special_payloads']),
            call(PAYLOAD_CONFIG_YAMLS['path2'][0]['special_payloads']),
        ], any_order=True)
        mock_ext_hooks.assert_has_calls([
            call(PAYLOAD_CONFIG_YAMLS['path1'][0]['extensions']),
        ], any_order=True)

        expected_config_part1 = {
            'standard_payloads': {
                'file.exe': PAYLOAD_CONFIG_YAMLS['path1'][0]['standard_payloads']['file.exe'],
                'file.ps1': PAYLOAD_CONFIG_YAMLS['path1'][0]['standard_payloads']['file.ps1'],
                'file.py': PAYLOAD_CONFIG_YAMLS['path2'][0]['standard_payloads']['file.py'],
                'file.txt': PAYLOAD_CONFIG_YAMLS['path2'][0]['standard_payloads']['file.txt'],
            },
            'special_payloads': {
                'file.go': PAYLOAD_CONFIG_YAMLS['path1'][0]['special_payloads']['file.go'],
                'file.cpp': PAYLOAD_CONFIG_YAMLS['path2'][0]['special_payloads']['file.cpp'],
            },
            'extensions': {
                '.donut': PAYLOAD_CONFIG_YAMLS['path1'][0]['extensions']['.donut'],
            }
        }
        mock_apply_config.assert_called_once_with(name='payloads', config=expected_config_part1)

        with patch.object(glob, 'iglob', return_value=['path3']) as mock_iglob2:
            with patch.object(BaseWorld, 'apply_config', wraps=_mock_apply_payload_config) as mock_apply_config2:
                with patch.object(DataService, 'get_config', return_value=self.mock_payload_config):
                    event_loop.run_until_complete(data_svc._load_payloads(test_plugin2))
        mock_iglob2.assert_called_once_with('test_plugin2/data/payloads/*.yml', recursive=False)
        mock_strip_yml.assert_called_with('path3')
        mock_payload_hooks.assert_called_with(PAYLOAD_CONFIG_YAMLS['path3'][0]['special_payloads'])
        mock_ext_hooks.assert_called_with(PAYLOAD_CONFIG_YAMLS['path3'][0]['extensions'])

        expected_config_part2 = {
            'standard_payloads': {
                'file.exe': PAYLOAD_CONFIG_YAMLS['path1'][0]['standard_payloads']['file.exe'],
                'file.ps1': PAYLOAD_CONFIG_YAMLS['path1'][0]['standard_payloads']['file.ps1'],
                'file.py': PAYLOAD_CONFIG_YAMLS['path2'][0]['standard_payloads']['file.py'],
                'file.txt': PAYLOAD_CONFIG_YAMLS['path2'][0]['standard_payloads']['file.txt'],
            },
            'special_payloads': {
                'file.go': PAYLOAD_CONFIG_YAMLS['path1'][0]['special_payloads']['file.go'],
                # test override
                'file.cpp': PAYLOAD_CONFIG_YAMLS['path3'][0]['special_payloads']['file.cpp'],
                'special.py': PAYLOAD_CONFIG_YAMLS['path3'][0]['special_payloads']['special.py'],
            },
            'extensions': {
                '.donut': PAYLOAD_CONFIG_YAMLS['path1'][0]['extensions']['.donut'],
                '.testext': PAYLOAD_CONFIG_YAMLS['path3'][0]['extensions']['.testext'],
            }
        }
        mock_apply_config2.assert_called_once_with(name='payloads', config=expected_config_part2)

    @mock.patch.object(logging.Logger, 'warn')
    @mock.patch.object(BaseWorld, 'strip_yml', wraps=strip_ability_yaml)
    async def test_load_ability_file(self, mock_strip_yml, mock_warn, data_svc):
        want_executors = [
            Executor(name='sh', platform='darwin', command='testcommand',
                     code=None, language=None, build_target=None,
                     payloads=None, uploads=None, timeout=60,
                     parsers=[], cleanup=None, variations=[]),
            Executor(name='sh', platform='linux', command='testcommand',
                     code=None, language=None, build_target=None,
                     payloads=None, uploads=None, timeout=60,
                     parsers=[], cleanup=None, variations=[])
        ]
        with patch.object(DataService, '_create_ability', return_value=None) as mock_create_ability:
            await data_svc.load_ability_file('plugins/testing/data/discovery/764efa883dda1e11db47671c4a3bbd9e.yml', BaseWorld.Access.RED)
            mock_create_ability.assert_called_once_with(ability_id='764efa883dda1e11db47671c4a3bbd9e', name='Find deletable dirs (per user)',
                                                        description='Discover all directories containing deletable files by user',
                                                        tactic='discovery', technique_id='T1082', technique_name='System Information Discovery',
                                                        executors=want_executors, requirements=[], privilege=None,
                                                        repeatable=False, buckets=['discovery'], access=BaseWorld.Access.RED, singleton=False, plugin='testing')

        with patch.object(DataService, '_create_ability', return_value=None) as mock_create_ability:
            await data_svc.load_ability_file('plugins/testing/data/discovery/848aa201-4b00-4f08-ae3a-3e84dfb5065c.yml', BaseWorld.Access.RED)
            mock_create_ability.assert_called_once_with(ability_id='848aa201-4b00-4f08-ae3a-3e84dfb5065c', name='Find deletable dirs (per user)',
                                                        description='Discover all directories containing deletable files by user',
                                                        tactic='discovery', technique_id='T1082', technique_name='System Information Discovery',
                                                        executors=want_executors, requirements=[], privilege=None,
                                                        repeatable=False, buckets=['discovery'], access=BaseWorld.Access.RED, singleton=False, plugin='testing')

        with patch.object(DataService, '_create_ability', return_value=None) as mock_create_ability:
            await data_svc.load_ability_file('plugins/testing/data/discovery/101.yml', BaseWorld.Access.RED)
            mock_warn.assert_any_call('Tactic for ability=101 is not in the ability file path plugins/testing/data/discovery/101.yml.')
            mock_warn.assert_called_with('Please check that the ability is labeled with the correct tactic and is in the correct location.')
            mock_create_ability.assert_called_once_with(ability_id='101', name='Find deletable dirs (per user)',
                                                        description='Discover all directories containing deletable files by user',
                                                        tactic='purposefullywrongtactic', technique_id='T1082', technique_name='System Information Discovery',
                                                        executors=want_executors, requirements=[], privilege=None,
                                                        repeatable=False, buckets=['purposefullywrongtactic'], access=BaseWorld.Access.RED, singleton=False, plugin='testing')

        with patch.object(DataService, '_create_ability', return_value=None) as mock_create_ability:
            with patch.object(logging.Logger, 'error') as mock_error:
                await data_svc.load_ability_file('plugins/testing/data/discovery/102.yml', BaseWorld.Access.RED)
                mock_create_ability.assert_not_called()
                assert mock_error.called

        # Test exception
        with patch.object(DataService, '_create_ability', side_effect=Exception('mockexception')):
            with patch.object(logging.Logger, 'exception') as mock_exception:
                await data_svc.load_ability_file('plugins/testing/data/discovery/101.yml', BaseWorld.Access.RED)
                mock_exception.assert_called_once_with(mock.ANY)
                assert 'Failed to load ability file plugins/testing/data/discovery/101.yml' in mock_exception.call_args.args[0]

    def test_save_state_does_not_mutate_self_ram(self, data_svc):
        """save_state() must not mutate self.ram (#3158)."""
        # Snapshot all keys we will mutate to prevent fixture state leaking
        mutated_keys = ('plugins', 'obfuscators', 'data_encoders', 'agents')
        original_values = {k: data_svc.ram[k] for k in mutated_keys if k in data_svc.ram}
        absent_keys = [k for k in mutated_keys if k not in data_svc.ram]
        try:
            data_svc.ram['plugins'] = ['sentinel']
            data_svc.ram['obfuscators'] = ['sentinel']
            data_svc.ram['data_encoders'] = ['sentinel']
            data_svc.ram['agents'] = ['agent_sentinel']

            ram_copy = dict(data_svc.ram)
            data_svc._prune_non_critical_data(ram_copy)

            assert 'plugins' not in ram_copy
            assert 'obfuscators' not in ram_copy
            assert 'data_encoders' not in ram_copy
            assert ram_copy['agents'] == ['agent_sentinel']

            # self.ram is untouched
            assert 'plugins' in data_svc.ram
            assert 'obfuscators' in data_svc.ram
            assert 'data_encoders' in data_svc.ram
        finally:
            # Restore all mutated keys to their original state
            for k, v in original_values.items():
                data_svc.ram[k] = v
            for k in absent_keys:
                data_svc.ram.pop(k, None)

    def test_prune_non_critical_data_safe_when_keys_missing(self, data_svc):
        """_prune_non_critical_data() must not raise if keys are absent."""
        ram_copy = {'agents': ['a']}
        data_svc._prune_non_critical_data(ram_copy)
        assert ram_copy == {'agents': ['a']}

    def test_get_plugin_name(self, data_svc):
        assert 'test' == data_svc._get_plugin_name('plugins/test')
        assert 'test' == data_svc._get_plugin_name('plugins/test/')
        assert 'test' == data_svc._get_plugin_name('plugins/test/data')
        assert 'test' == data_svc._get_plugin_name('plugins/test/data/abilities')
        assert 'test' == data_svc._get_plugin_name('plugins/test/data/abilities/collection/123.yml')
        assert 'test' == data_svc._get_plugin_name('/full/path/to/plugins/test/data/abilities/collection/123.yml')
        assert '' == data_svc._get_plugin_name('test')
        assert '' == data_svc._get_plugin_name('plugins')
        assert '' == data_svc._get_plugin_name('plugins/')
        assert '' == data_svc._get_plugin_name('/full/path/to/plugins')
        assert '' == data_svc._get_plugin_name('/full/path/to/plugins/')
        assert '' == data_svc._get_plugin_name('plugin/test')
        assert '' == data_svc._get_plugin_name('plugin/test/')
        assert '' == data_svc._get_plugin_name('plugin/test/data')
        assert '' == data_svc._get_plugin_name('plugin/test/data/abilities')
        assert '' == data_svc._get_plugin_name('plugin/test/data/abilities/collection/123.yml')
        assert '' == data_svc._get_plugin_name('/full/path/to/plugin/test/data/abilities/collection/123.yml')
