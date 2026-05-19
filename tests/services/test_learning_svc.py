import pytest
from unittest import mock

from app.objects.c_adversary import Adversary
from app.objects.secondclass.c_executor import Executor
from app.objects.secondclass.c_fact import Fact
from app.service.data_svc import DataService
from app.utility.base_world import BaseWorld


@pytest.fixture
async def setup_learning_service(data_svc, ability, operation, link):
    texecutor = Executor(name='sh', platform='darwin', command='whoami', payloads=['wifi.sh'])
    tability = ability(tactic='discovery', technique_id='T1033', technique_name='Find', name='test',
                       description='find active user', privilege=None, executors=[texecutor])
    await data_svc.store(tability)
    toperation = operation(name='sample', agents=None, adversary=Adversary(name='sample', adversary_id='XYZ',
                                                                           atomic_ordering=[], description='test'))
    await data_svc.store(toperation)
    tlink1 = link(ability=tability, command='', paw='1234', executor=texecutor)
    tlink2 = link(ability=tability, command='', paw='5678', executor=texecutor)
    yield toperation, tlink1, tlink2


class TestLearningSvc:

    async def test_learn(self, setup_learning_service, learning_svc, knowledge_svc):
        operation, link, _ = setup_learning_service
        operation.add_link(link)
        all_facts = await operation.all_facts()
        await learning_svc.learn(
            facts=all_facts,
            link=link,
            blob=BaseWorld.encode_string('i contain 1 ip address 192.168.0.1 and one file /etc/host.txt. that is all.'))
        knowledge_facts = await knowledge_svc.get_facts(dict(source=link.id))
        assert len(link.facts) == 2
        assert len(knowledge_facts) == 2

    async def test_same_fact_different_agents(self, setup_learning_service, learning_svc, knowledge_svc):
        operation, link1, link2 = setup_learning_service
        link1.id = 'link1'
        link2.id = 'link2'
        operation.add_link(link1)
        all_facts = await operation.all_facts()
        await learning_svc.learn(
            facts=all_facts,
            link=link1,
            blob=BaseWorld.encode_string('i contain 1 ip address 192.168.0.1 and one file /etc/host.txt. that is all.'),
            operation=operation)
        operation.add_link(link2)
        all_facts = await operation.all_facts()
        await learning_svc.learn(
            facts=all_facts,
            link=link2,
            blob=BaseWorld.encode_string('i contain 1 ip address 192.168.0.1 and one file /etc/host.txt. that is all.'),
            operation=operation)

        knowledge_facts = await knowledge_svc.get_facts(dict(source=operation.id))
        assert len(link1.facts) == 2
        assert len(link2.facts) == 2
        assert len(knowledge_facts) == 2
        assert len(knowledge_facts[0].collected_by) == 2

    async def test_build_relationships(self, setup_learning_service, learning_svc, knowledge_svc):
        _, link, _ = setup_learning_service
        learning_svc.model.add(frozenset({'host.user.name', 'target.org.name'}))
        learning_svc.model.add(frozenset({'host.file.extension', 'host.user.name', 'domain.user.name'}))
        facts = [
            Fact(trait='target.org.name', value='something'),
            Fact(trait='host.user.name', value='admin'),
            Fact(trait='host.user.name', value='root'),
            Fact(trait='domain.user.name', value='user'),
            Fact(trait='not.really.here', value='should never be found')
        ]
        await learning_svc._store_results(link, facts)
        assert len(link.relationships) == 4

    async def test_build_model(self, learning_svc, ability, executor, data_svc):
        test_executor = executor(name='sh', platform='linux', command='test command #{var1} #{var2}')
        test_ability = ability(ability_id='123123', executors=[test_executor])
        want = set()
        want.add(frozenset(['var1', 'var2']))
        learning_svc.model = set()
        with mock.patch.object(DataService, 'locate', return_value=[test_ability]):
            await learning_svc.build_model()
        assert want == learning_svc.model
