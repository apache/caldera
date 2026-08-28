import pytest
import asyncio
import base64
from unittest.mock import MagicMock, AsyncMock

from app.objects.c_adversary import Adversary
from app.objects.c_obfuscator import Obfuscator
from app.objects.c_source import Source
from app.objects.c_agent import Agent
from app.objects.secondclass.c_link import Link
from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_requirement import Requirement
from app.utility.base_world import BaseWorld


stop_bucket_exhaustion_params = [
    {'stopping_condition_met': False, 'operation_state': 'RUNNING', 'condition_stop': True, 'assert_value': False},
    {'stopping_condition_met': True, 'operation_state': 'RUNNING', 'condition_stop': True, 'assert_value': True},
    {'stopping_condition_met': False, 'operation_state': 'FINISHED', 'condition_stop': True, 'assert_value': True},
    {'stopping_condition_met': True, 'operation_state': 'FINISHED', 'condition_stop': True, 'assert_value': True},
    {'stopping_condition_met': True, 'operation_state': 'RUNNING', 'condition_stop': False, 'assert_value': False}
]

test_string = '#{1_2_3} - #{a.b.c} - #{a.b.d} - #{a.b.e[filters(max=3)]}'
target_string = '0 - 1 - 2 - 3'


class PlannerFake:
    def __init__(self, operation):
        self.state_machine = ['one', 'two', 'three', 'four']
        self.next_bucket = 'one'
        self.stopping_condition_met = False
        self.stopping_conditions = [Fact(trait='j.g.b', value='good')]
        self.calls = []
        self.operation = operation

    async def one(self):
        self.calls.append('one')
        self.next_bucket = 'two'

    async def two(self):
        self.calls.append('two')
        self.next_bucket = 'three'

    async def three(self):
        self.calls.append('three')
        self.next_bucket = None

    async def four(self):
        self.calls.append('four')
        self.next_bucket = None


class RequirementFake:
    """Fake requirement used to test trim links by missing requirements."""
    async def enforce(self, link, operation):
        for uf in link.used:
            if uf.value == '0':
                return True
        return False


def planner_stub(**kwargs):
    """Creates Planner stub with supplied properties."""
    class PlannerStub:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    return PlannerStub(**kwargs)


@pytest.fixture
async def setup_planning_test(executor, ability, agent, operation, data_svc, event_svc, init_base_world):
    texecutor = executor(name='sh', platform='darwin', command='mkdir test', cleanup='rm -rf test')
    tability = ability(ability_id='123', executors=[texecutor], repeatable=True, buckets=['test'], name='test1')
    tagent = agent(sleep_min=1, sleep_max=2, watchdog=0, executors=['sh'], platform='darwin',
                   server='http://127.0.0.1:8000')
    tsource = Source(id='123', name='test', facts=[], adjustments=[])
    toperation = operation(name='test1', agents=[tagent],
                           adversary=Adversary(name='test', description='test',
                                               atomic_ordering=[],
                                               adversary_id='XYZ'),
                           source=tsource)

    cexecutor = executor(name='sh', platform='darwin', command=test_string, cleanup='whoami')
    cability = ability(ability_id='321', executors=[cexecutor], singleton=True, name='test2')

    await data_svc.store(tability)
    await data_svc.store(cability)

    await data_svc.store(
        Obfuscator(name='plain-text',
                   description='Does no obfuscation to any command, instead running it in plain text',
                   module='app.obfuscators.plain_text')
    )

    yield tability, tagent, toperation, cability


@pytest.fixture(params=stop_bucket_exhaustion_params)
def stop_bucket_exhaustion_setup(request, setup_planning_test):
    """
    Provides setup objects for tests of _stop_bucket_exhaustion().
    """
    _, _, operation, _ = setup_planning_test
    planner = planner_stub(stopping_condition_met=request.param['stopping_condition_met'])
    operation.state = operation.states[request.param['operation_state']]
    return planner, operation, request.param['condition_stop'], request.param['assert_value']


class TestPlanningService:
    async def test_wait_for_links_and_monitor(self, planning_svc, fact, setup_planning_test):
        # PART A:
        ability, agent, operation, _ = setup_planning_test
        # Add a link to operation.chain
        operation.add_link(Link.load(
            dict(command='', paw=agent.paw, ability=ability, executor=next(ability.executors), status=0)))
        # Set id to match planner.operation.chain[0].id
        operation.chain[0].id = "123"
        planner = PlannerFake(operation)
        # Create a list containing only the id used above
        link_ids = ["123"]
        # Make sure program doesn't hang in wait_for_links_completion()
        planner.operation.chain[0].finish = True
        assert await planning_svc.wait_for_links_and_monitor(
            planner, operation, link_ids, condition_stop=True) is False

        # PART B:
        # Make sure program hangs in wait_for_links_completion()
        planner.operation.chain[0].finish = False
        timeout = False
        try:
            await asyncio.wait_for(
                planning_svc.wait_for_links_and_monitor(planner, operation,
                                                        link_ids,
                                                        condition_stop=True),
                timeout=5.0)
        except asyncio.TimeoutError:
            timeout = True
        assert timeout is True

    async def test_get_links(self, setup_planning_test, planning_svc, data_svc, knowledge_svc, fire_event_mock):
        # PART A: Don't fill in facts for "cability" so only "tability"
        #   is returned in "links"
        tability, agent, operation, cability = setup_planning_test
        operation.adversary.atomic_ordering = ["123", "321"]
        links = await planning_svc.get_links(operation=operation, buckets=None, agent=agent)
        assert links[0].ability.ability_id == tability.ability_id

        # PART B: Fill in facts to allow "cability" to be returned in "links"
        #   in addition to "tability"
        operation.add_link(Link.load(
            dict(command='', paw=agent.paw, ability=tability, executor=next(tability.executors), status=0)))

        await knowledge_svc.add_fact(Fact(trait='1_2_3', value='0', source=operation.id))
        await knowledge_svc.add_fact(Fact(trait='a.b.c', value='1', source=operation.id))
        await knowledge_svc.add_fact(Fact(trait='a.b.d', value='2', source=operation.id))
        await knowledge_svc.add_fact(Fact(trait='a.b.e', value='3', source=operation.id))

        links = await planning_svc.get_links(operation=operation, buckets=None, agent=agent)

        assert links[0].ability.ability_id == cability.ability_id
        assert links[1].ability.ability_id == tability.ability_id
        assert base64.b64decode(links[0].command).decode('utf-8') == target_string

    async def test_exhaust_bucket(self, setup_planning_test, planning_svc):
        ability, agent, operation, _ = setup_planning_test
        operation.adversary.atomic_ordering = ["123"]
        operation.add_link(Link.load(
            dict(command='', paw=agent.paw, ability=ability, executor=next(ability.executors), status=0)))
        operation.chain[0].finish = True
        planner = PlannerFake(operation)
        bucket = "test"
        timeout = False
        try:
            await asyncio.wait_for(planning_svc.exhaust_bucket(
                planner, bucket, operation, agent), timeout=5.0)
        except asyncio.TimeoutError:
            timeout = True
        assert timeout is True

    async def test_add_ability_to_bucket(self, setup_planning_test, planning_svc):
        b1 = 'salvador'
        b2 = 'hardin'
        a, _, _, _ = setup_planning_test
        await planning_svc.add_ability_to_bucket(a, b1)
        assert a.buckets == ['test', b1]
        await planning_svc.add_ability_to_bucket(a, b2)
        assert a.buckets == ['test', b1, b2]

    async def test_default_next_bucket(self, planning_svc):
        sm = ['alpha', 'bravo', 'charlie']
        assert await planning_svc.default_next_bucket(sm[0], sm) == sm[1]
        assert await planning_svc.default_next_bucket(sm[1], sm) == sm[2]
        assert await planning_svc.default_next_bucket(sm[2], sm) == sm[0]    # loops around

    async def test_stopping_condition_met(self, planning_svc, fact):
        facts = [
            fact(trait='m.b.k', value='michael'),
            fact(trait='l.r.k', value='laura')
        ]
        stopping_condition = fact(trait='c.p.k', value='cole')

        assert await planning_svc._stopping_condition_met(facts, stopping_condition) is False
        facts.append(stopping_condition)
        assert await planning_svc._stopping_condition_met(facts, stopping_condition) is True

    async def test_check_stopping_conditions(self, fact, link, setup_planning_test, planning_svc, fire_event_mock):
        ability, agent, operation, _ = setup_planning_test
        executor = next(ability.executors)
        operation.source.facts = []
        stopping_conditions = [fact(trait='s.o.f.', value='seldon')]

        # first verify stopping conditions not met
        assert await planning_svc.check_stopping_conditions(stopping_conditions, operation) is False
        # add stopping condition to a fact, then to a link, then the link to the operation
        l0 = link(command='test', paw='0', ability=ability, executor=executor)
        l1 = link(command='test1', paw='1', ability=ability, executor=executor)
        await l1.save_fact(operation, stopping_conditions[0], 1, "dummy_relationship_visual_string")
        operation.add_link(l0)
        operation.add_link(l1)
        # now verify stopping condition is met since we directly inserted fact that matches stopping condition
        assert await planning_svc.check_stopping_conditions(stopping_conditions, operation) is True

    async def test_update_stopping_condition_met(self, fact, link, setup_planning_test, planning_svc, fire_event_mock):
        ability, agent, operation, _ = setup_planning_test
        stopping_condition = fact(trait='t.c.t', value='boss')

        class PlannerStub():
            stopping_conditions = [stopping_condition]
            stopping_condition_met = False
        p = PlannerStub()

        # first call should not result in 'met' flag being flipped
        await planning_svc.update_stopping_condition_met(p, operation)
        assert p.stopping_condition_met is False
        # add stopping condition to a fact, then to a link, then the link to the operation
        l1 = link(command='test1', paw='1', ability=ability, executor=next(ability.executors))
        await l1.save_fact(operation, stopping_condition, 1, "dummy_relationship_visual_string")
        operation.add_link(l1)
        # now verify stopping condition is met since we directly inserted fact that matches stopping conidition
        await planning_svc.update_stopping_condition_met(p, operation)
        assert p.stopping_condition_met is True

    async def test_sort_links(self, link, planning_svc, setup_planning_test):
        a, _, _, _ = setup_planning_test
        executor = next(a.executors)
        l1 = link(command='m', paw='1', ability=a, executor=executor, score=1)
        l2 = link(command='a', paw='2', ability=a, executor=executor, score=2)
        l3 = link(command='l', paw='3', ability=a, executor=executor, score=3)
        sl = await planning_svc.sort_links([l2, l1, l3])
        assert sl[0] == l3
        assert sl[1] == l2
        assert sl[2] == l1

    async def test_stop_bucket_exhaustion(self, stop_bucket_exhaustion_setup, planning_svc):
        """
        NOTE: Test runs 5x, each with parameter sets found in 'stop_bucket_exhaustion_params'.
        """
        planner, operation, condition_stop, assert_value = stop_bucket_exhaustion_setup
        assert await planning_svc._stop_bucket_exhaustion(planner, operation, condition_stop) is assert_value

    async def test_execute_planner(self, setup_planning_test, planning_svc, monkeypatch):
        """
        Case 1 - let planner run until it stops itself after bucket 'three'.
        """
        ability, agent, operation, _ = setup_planning_test
        p = PlannerFake(operation)
        await planning_svc.execute_planner(p, publish_transitions=False)
        assert p.calls == ['one', 'two', 'three']

    async def test_execute_planner_2(self, monkeypatch, planning_svc, setup_planning_test):
        """
        Case 2 - start planner but then hijack operation after bucket 'two and flag that stopping condition
        been found, thus stopping the planner when it attempt to proceed to next bucket
        """
        ability, agent, operation, _ = setup_planning_test

        async def stub_update_stopping_condition_met(planner, operation):
            if planner.calls == ['one', 'two']:
                planner.stopping_condition_met = True
        monkeypatch.setattr(planning_svc, 'update_stopping_condition_met', stub_update_stopping_condition_met)
        p = PlannerFake(operation)
        await planning_svc.execute_planner(p, publish_transitions=False)
        assert p.calls == ['one', 'two']

    async def test_execute_planner_3(self, monkeypatch, planning_svc, setup_planning_test):
        """
        Case 3 - start planner but then hijack operation and set it to 'FINISH' state, thus
        stopping the planner when it attempts to proceed to next bucket
        """
        ability, agent, operation, _ = setup_planning_test

        async def stub_update_stopping_condition_met_1(planner, operation):
            if planner.calls == ['one']:
                operation.state = operation.states['FINISHED']
        monkeypatch.setattr(planning_svc, 'update_stopping_condition_met', stub_update_stopping_condition_met_1)
        p = PlannerFake(operation)
        await planning_svc.execute_planner(p, publish_transitions=False)
        assert p.calls == ['one']

    async def test_get_cleanup_links(self, setup_planning_test, planning_svc):
        ability, agent, operation, _ = setup_planning_test
        executor = next(ability.executors)
        operation.add_link(Link.load(dict(command='', paw=agent.paw, ability=ability, executor=executor, status=0)))
        links = await planning_svc.get_cleanup_links(operation=operation, agent=agent)
        link_list = list(links)
        assert len(link_list) == 1
        assert BaseWorld.decode_bytes(link_list[0].command) == executor.cleanup[0]

    async def test_generate_and_trim_links(self, setup_planning_test, planning_svc):
        ability, agent, operation, _ = setup_planning_test
        generated_links = await planning_svc.generate_and_trim_links(agent, operation, [ability])
        assert 1 == len(generated_links)

    async def test_link_fact_coverage(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        link = Link.load(dict(command=BaseWorld.encode_string(test_string), paw=agent.paw, ability=ability,
                              executor=next(ability.executors), status=0))

        f0 = Fact(trait='1_2_3', value='0')
        f1 = Fact(trait='a.b.c', value='1')
        f2 = Fact(trait='a.b.d', value='2')
        f3 = Fact(trait='a.b.e', value='3')

        gen = await planning_svc.add_test_variants([link], agent, facts=[f0, f1, f2, f3])
        assert len(gen) == 2
        assert gen[1].display['command'] == target_string

    async def test_trim_links(self, setup_planning_test, planning_svc):
        """
        This test covers both remove_links_with_unset_variables and remove_links_missing_requirements.
        It uses a fact set that causes add_test_variants to create three links. One of which is the original
        that has not been populated with facts, this one gets pruned off by remove_links_with_unset_variables.
        Of the remaining two links that are populated, one is pruned off by a requirement that requires that
        the character 0 is in the link's command. The tests show that only one link is returned by trim_links
        and that the returned link is the one that is populated and adheres to the requirement.
        """
        ability, agent, operation, _ = setup_planning_test

        link = Link.load(dict(command=BaseWorld.encode_string(test_string), paw=agent.paw, ability=ability,
                              executor=next(ability.executors), status=0))
        facts = [
            Fact(trait='1_2_3', value='0'),
            Fact(trait='1_2_3', value='4'),
            Fact(trait='a.b.c', value='1'),
            Fact(trait='a.b.d', value='2'),
            Fact(trait='a.b.e', value='3'),
        ]

        operation.all_facts = AsyncMock(return_value=facts)
        operation.planner = MagicMock()
        planning_svc.load_module = AsyncMock(return_value=RequirementFake())
        link.ability.requirements = [Requirement('fake_requirement', [{'fake': 'relationship'}])]

        trimmed_links = await planning_svc.trim_links(operation, [link], agent)

        assert len(trimmed_links) == 1
        assert trimmed_links[0].display['command'] == target_string

    async def test_filter_bs(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        link = Link.load(dict(command=BaseWorld.encode_string(test_string), paw=agent.paw, ability=ability,
                              executor=next(ability.executors), status=0))

        f0 = Fact(trait='1_2_3', value='0')
        f1 = Fact(trait='a.b.c', value='1')
        f2 = Fact(trait='a.b.d', value='2')
        f3 = Fact(trait='a.b.e', value='3')
        f4 = Fact(trait='a.b.e', value='4')
        f5 = Fact(trait='a.b.e', value='5')
        f6 = Fact(trait='a.b.e', value='6')

        gen = await planning_svc.add_test_variants([link], agent, facts=[f0, f1, f2, f3, f4, f5, f6])

        assert len(gen) == 4
        assert gen[1].display['command'] == target_string

    async def test_duplicate_lateral_filter(self, setup_planning_test, planning_svc, link, fact):
        ability, agent, operation, sability = setup_planning_test

        l0 = link(command='a0', paw='0', ability=ability, executor=next(ability.executors))
        l1 = link(command='a1', paw='0', ability=sability, executor=next(sability.executors))
        l2 = link(command='a0', paw='0', ability=ability, executor=next(ability.executors))
        l3 = link(command='a1', paw='0', ability=sability, executor=next(sability.executors))

        l0.status = l0.states['SUCCESS']
        l1.status = l1.states['SUCCESS']

        filtered = [x for x in [l0, l1, l2, l3] if x.ability.singleton]
        assert 2 == len(filtered)

        operation.chain = [l0, l1]

        # test historical filtering
        filt = await planning_svc.remove_completed_links(operation, agent, [l2, l3])
        assert 1 == len(filt)

        # test parallel filtering
        flat_fil = await planning_svc._remove_links_of_duplicate_singletons([[l0, l1, l2, l3],
                                                                            [l0, l1, l2, l3],
                                                                            [l0, l1, l2, l3]])
        assert 7 == len(flat_fil)

    async def test_trait_with_one_part(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test

        encoded_command = BaseWorld.encode_string('#{a}')
        link = Link.load(dict(command=encoded_command, paw=agent.paw, ability=ability, executor=next(ability.executors),
                              status=0))

        input_facts = [
            Fact(trait='a', value='1'),
            Fact(trait='a.b', value='2'),
            Fact(trait='a.b.c', value='3'),
            Fact(trait='server', value='5')
        ]

        new_links = await planning_svc.add_test_variants([link], agent, facts=input_facts)
        assert len(new_links) == 2

        found_commands = set(x.command for x in new_links)
        assert len(found_commands) == 2  # the original and the replaced
        assert encoded_command in found_commands
        assert BaseWorld.encode_string('1') in found_commands

    async def test_trait_with_two_parts(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        encoded_command = BaseWorld.encode_string('#{a.b}')
        link = Link.load(dict(command=encoded_command, paw=agent.paw, ability=ability, executor=next(ability.executors),
                              status=0))

        input_facts = [
            Fact(trait='a', value='1'),
            Fact(trait='a.b', value='2'),
            Fact(trait='a.b.c', value='3'),
            Fact(trait='server', value='5')
        ]

        new_links = await planning_svc.add_test_variants([link], agent, facts=input_facts)
        assert len(new_links) == 2

        found_commands = set(x.command for x in new_links)
        assert len(found_commands) == 2  # the original and the replaced
        assert encoded_command in found_commands
        assert BaseWorld.encode_string('2') in found_commands

    async def test_trait_with_three_parts(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        encoded_command = BaseWorld.encode_string('#{a.b.c}')
        link = Link.load(dict(command=encoded_command, paw=agent.paw, ability=ability, executor=next(ability.executors),
                              status=0))

        input_facts = [
            Fact(trait='a', value='1'),
            Fact(trait='a.b', value='2'),
            Fact(trait='a.b.c', value='3'),
            Fact(trait='server', value='5')
        ]

        new_links = await planning_svc.add_test_variants([link], agent, facts=input_facts)
        assert len(new_links) == 2

        found_commands = set(x.command for x in new_links)
        assert len(found_commands) == 2  # the original and the replaced
        assert encoded_command in found_commands
        assert BaseWorld.encode_string('3') in found_commands

    async def test_trait_with_multiple_variations_of_parts(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        encoded_command = BaseWorld.encode_string('#{a} #{a.b} #{a.b.c}')
        link = Link.load(dict(command=encoded_command, paw=agent.paw, ability=ability, executor=next(ability.executors),
                              status=0))

        input_facts = [
            Fact(trait='a', value='1'),
            Fact(trait='a.b', value='2'),
            Fact(trait='a.b.c', value='3'),
            Fact(trait='server', value='5')
        ]

        new_links = await planning_svc.add_test_variants([link], agent, facts=input_facts)
        assert len(new_links) == 2

        found_commands = set(x.command for x in new_links)
        assert len(found_commands) == 2  # the original and the replaced
        assert encoded_command in found_commands
        assert BaseWorld.encode_string('1 2 3') in found_commands

    async def test_global_variables_not_replaced_with_facts(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        encoded_command = BaseWorld.encode_string('#{server} #{origin_link_id}')
        link = Link.load(dict(command=encoded_command, paw=agent.paw, ability=ability, executor=next(ability.executors),
                              status=0))

        input_facts = [
            Fact(trait='server', value='bad.server'),
            Fact(trait='origin_link_id', value='bad.origin_link_id')
        ]

        planning_svc.add_global_variable_owner(Agent)  # handles #{server}
        planning_svc.add_global_variable_owner(Link)  # handles #{origin_link_id}

        new_links = await planning_svc.add_test_variants([link], agent, facts=input_facts)
        assert len(new_links) == 1
        assert new_links[0].raw_command == f'{agent.server} {link.id}'

    async def test_remove_links_missing_facts_keeps_link_without_facts(self, planning_svc, ability, executor):
        cmd = 'a -b --foo={bar}'  # almost includes a fact, but missing a '#' in front of '{bar}'
        links = [Link(command=BaseWorld.encode_string(cmd), paw='1', ability=ability(), executor=executor())]
        await planning_svc.remove_links_with_unset_variables(links)
        assert len(links) == 1
        assert links[0].raw_command == cmd

    async def test_remove_links_missing_facts_removes_one_part_fact(self, planning_svc, ability, executor):
        cmd = 'a -b --foo=#{bar}'
        links = [Link(command=BaseWorld.encode_string(cmd), paw='1', ability=ability(), executor=executor())]
        await planning_svc.remove_links_with_unset_variables(links)
        assert len(links) == 0

    async def test_remove_links_missing_facts_removes_two_part_fact(self, planning_svc, ability, executor):
        cmd = 'a -b --foo=#{foo.bar}'
        links = [Link(command=BaseWorld.encode_string(cmd), paw='1', ability=ability(), executor=executor())]
        await planning_svc.remove_links_with_unset_variables(links)
        assert len(links) == 0

    async def test_remove_links_missing_facts_removes_three_part_fact(self, planning_svc, ability, executor):
        cmd = 'a -b --foo=#{foo.bar.baz}'
        links = [Link(command=BaseWorld.encode_string(cmd), paw='1', ability=ability(), executor=executor())]
        await planning_svc.remove_links_with_unset_variables(links)
        assert len(links) == 0

    async def test_remove_links_does_not_ignore_global_variables(self, planning_svc, ability, executor):
        cmd = 'a -b --foo=#{server} --bar=#{origin_link_id}'
        links = [Link(command=BaseWorld.encode_string(cmd), paw='1', ability=ability(), executor=executor())]

        planning_svc.add_global_variable_owner(Agent)  # handles #{server}
        planning_svc.add_global_variable_owner(Link)  # handles #{origin_link_id}

        await planning_svc.remove_links_with_unset_variables(links)
        assert len(links) == 0

    async def test_link_host_presence(self, setup_planning_test, planning_svc):
        _, agent, operation, ability = setup_planning_test
        link = Link.load(dict(command=BaseWorld.encode_string(test_string), paw=agent.paw, ability=ability,
                              executor=next(ability.executors), status=0))

        f0 = Fact(trait='1_2_3', value='a')
        f1 = Fact(trait='a.b.c', value='b')
        f2 = Fact(trait='a.b.d', value='c')
        f3 = Fact(trait='a.b.e', value='d')

        handle = [link]
        gen = await planning_svc.add_test_variants(handle, agent, facts=[f0, f1, f2, f3])

        assert gen[0].host == handle[0].host

    async def test_call_ability_plugin_hooks_invokes_all_hooks(self, planning_svc, ability, executor):
        """All entries in Executor.HOOKS must be called, not just the language-keyed one."""
        call_log = []

        async def hook_a(ab, ex):
            call_log.append('a')

        async def hook_b(ab, ex):
            call_log.append('b')

        ex = executor(name='psh', platform='windows')
        ab = ability()
        ex.HOOKS = {'key_a': hook_a, 'key_b': hook_b}
        await planning_svc._call_ability_plugin_hooks(ab, ex)
        assert set(call_log) == {'a', 'b'}, 'All registered hooks must be invoked'

    async def test_call_ability_plugin_hooks_no_hooks(self, planning_svc, ability, executor):
        """_call_ability_plugin_hooks must be a no-op when HOOKS is absent or empty."""
        import types

        ex = executor(name='psh', platform='windows')
        ab = ability()
        ex.HOOKS = {}
        await planning_svc._call_ability_plugin_hooks(ab, ex)  # should not raise

        # Use SimpleNamespace to genuinely test absent HOOKS attribute;
        # real Executor instances always have a class-level HOOKS dict.
        executor_without_hooks = types.SimpleNamespace()
        await planning_svc._call_ability_plugin_hooks(ab, executor_without_hooks)  # should not raise
