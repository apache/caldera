from app.objects.secondclass.c_fact import Fact, OriginType
from app.objects.secondclass.c_relationship import Relationship
from app.objects.secondclass.c_rule import Rule
from app.objects.secondclass.c_link import Link


class TestKnowledgeService:

    async def test_no_duplicate_fact(self, knowledge_svc, fire_event_mock):
        await knowledge_svc.add_fact(Fact(trait='test', value='demo', score=1,
                                     collected_by=['thin_air'], technique_id='T1234'))
        await knowledge_svc.add_fact(Fact(trait='test', value='demo', score=1,
                                     collected_by=['thin_air'], technique_id='T1234'))
        facts = await knowledge_svc.get_facts(dict(trait='test'))
        assert len(facts) == 1

    async def test_no_duplicate_rules(self, knowledge_svc):
        await knowledge_svc.add_rule(Rule(action='BLOCK', trait='a.c', match='.*'))
        await knowledge_svc.add_rule(Rule(action='BLOCK', trait='a.c', match='.*'))
        rules = await knowledge_svc.get_rules(dict(trait='a.c'))
        assert len(rules) == 1

    async def test_no_duplicate_relationship(self, knowledge_svc):
        dummy = Fact(trait='test', value='demo', score=1, collected_by=['thin_air'], technique_id='T1234')
        await knowledge_svc.add_relationship(Relationship(source=dummy, edge='potato', target=dummy))
        await knowledge_svc.add_relationship(Relationship(source=dummy, edge='potato', target=dummy))
        relationships = await knowledge_svc.get_relationships(dict(edge='potato'))
        assert len(relationships) == 1

    async def test_remove_fact(self, knowledge_svc, event_svc, fire_event_mock):
        await knowledge_svc.add_fact(Fact(trait='rtest', value='rdemo', score=1,
                                     collected_by=['thin_air'], technique_id='T1234'),
                                     constraints=dict(test_field='test_value'))
        await knowledge_svc.add_fact(Fact(trait='ktest', value='rdemo', score=1,
                                          collected_by=['thin_air'], technique_id='T1234'))
        await knowledge_svc.delete_fact(dict(trait='rtest'))
        facts = await knowledge_svc.get_facts(dict(value='rdemo'))
        assert len(facts) == 1
        assert len(knowledge_svc._KnowledgeService__loaded_knowledge_module.fact_ram['constraints']) == 0

    async def test_remove_rules(self, knowledge_svc):
        await knowledge_svc.add_rule(Rule(action='rBLOCK', trait='ra.c', match='.*'),
                                     constraints=dict(test_field='test_value'))
        await knowledge_svc.delete_rule(dict(trait='ra.c'))
        rules = await knowledge_svc.get_rules(dict(trait='ra.c'))
        assert len(rules) == 0
        assert len(knowledge_svc._KnowledgeService__loaded_knowledge_module.fact_ram['constraints']) == 0

    async def test_remove_relationship(self, knowledge_svc):
        dummy = Fact(trait='rtest', value='rdemo', score=1, collected_by=['thin_air'], technique_id='T1234')
        await knowledge_svc.add_relationship(Relationship(source=dummy, edge='rpotato', target=dummy),
                                             constraints=dict(test_field='test_value'))
        await knowledge_svc.delete_relationship(dict(edge='rpotato'))
        relationships = await knowledge_svc.get_relationships(dict(edge='rpotato'))
        assert len(relationships) == 0
        assert len(knowledge_svc._KnowledgeService__loaded_knowledge_module.fact_ram['constraints']) == 0

    async def test_update_fact(self, knowledge_svc, fire_event_mock):
        await knowledge_svc.add_fact(Fact(trait='utest', value='udemo', score=1,
                                     collected_by=['thin_air'], technique_id='T1234'))
        await knowledge_svc.update_fact(criteria=dict(trait='utest'),
                                        updates=dict(trait='utest2', value='udemo2'))
        facts = await knowledge_svc.get_facts(dict(trait='utest2'))
        assert len(facts) == 1
        assert facts[0].value == 'udemo2'

    async def test_update_fact_with_falsy_current_value(self, knowledge_svc, fire_event_mock):
        await knowledge_svc.add_fact(Fact(trait='ftest', value='fdemo', score=0))
        await knowledge_svc.update_fact(criteria=dict(trait='ftest'),
                                        updates=dict(score=2, technique_id='T1234', not_a_field='ignored'))
        facts = await knowledge_svc.get_facts(dict(trait='ftest'))
        assert len(facts) == 1
        assert facts[0].score == 2
        assert facts[0].technique_id == 'T1234'
        assert not hasattr(facts[0], 'not_a_field')

    async def test_update_relationship(self, knowledge_svc):
        dummy = Fact(trait='utest', value='udemo', score=1, collected_by=['thin_air'], technique_id='T1234')
        dummy2 = Fact(trait='utest2', value='udemo2', score=1, collected_by=['thin_air'], technique_id='T4321')
        await knowledge_svc.add_relationship(Relationship(source=dummy, edge='upotato', target=dummy))
        await knowledge_svc.update_relationship(criteria=dict(edge='upotato'),
                                                updates=dict(source=dummy2, edge='ubacon'))
        relationships = await knowledge_svc.get_relationships(dict(edge='ubacon'))
        assert len(relationships) == 1
        assert relationships[0].source == dummy2

    async def test_retrieve_fact(self, knowledge_svc, fire_event_mock):
        await knowledge_svc.add_fact(Fact(trait='ttestA', value='tdemoB', score=24,
                                     collected_by=['thin_airA'], technique_id='T1234'))
        await knowledge_svc.add_fact(Fact(trait='ttestB', value='tdemoA', score=42,
                                          collected_by=['thin_airB'], technique_id='T4321'))
        facts = await knowledge_svc.get_facts(dict(trait='ttestB'))
        assert len(facts) == 1
        readable = facts[0].display
        assert readable['value'] == 'tdemoA'
        assert readable['score'] == 42

    async def test_retrieve_relationship(self, knowledge_svc):
        dummy = Fact(trait='ttest', value='tdemo', score=1, collected_by=['thin_air'], technique_id='T1234')
        dummy2 = Fact(trait='ttest2', value='tdemo2', score=1, collected_by=['thin_air'], technique_id='T1234')
        await knowledge_svc.add_relationship(Relationship(source=dummy, edge='tpotato', target=dummy2))
        await knowledge_svc.add_relationship(Relationship(source=dummy2, edge='tpotato', target=dummy))
        relationships = await knowledge_svc.get_relationships(dict(edge='tpotato'))
        assert len(relationships) == 2
        specific = await knowledge_svc.get_relationships(dict(source=dummy))
        assert len(specific) == 1
        readable = specific[0].display
        assert readable['edge'] == 'tpotato'
        assert readable['target'].trait == 'ttest2'

    async def test_retrieve_rule(self, knowledge_svc):
        await knowledge_svc.add_rule(Rule(action='tBLOCK', trait='ta.d', match='4.5.*'))
        await knowledge_svc.add_rule(Rule(action='tALLOW', trait='ta.d', match='*.5.*'))
        rules = await knowledge_svc.get_rules(dict(trait='ta.d'))
        assert len(rules) == 2

        fuzzy1 = await knowledge_svc.get_rules(dict(trait='ta.d', match='4.5.6'))
        assert len(fuzzy1) == 2
        fuzzy2 = await knowledge_svc.get_rules(dict(trait='ta.d', match='6.5.4'))
        assert len(fuzzy2) == 1
        assert fuzzy2[0].action == 'tALLOW'
        fuzzy3 = await knowledge_svc.get_rules(dict(trait='ta.d', match='5.*'))
        assert len(fuzzy3) == 2

    async def test_fact_origin(self, knowledge_svc, ability, executor):
        texecutor = executor(name='sh', platform='darwin', command='mkdir test', cleanup='rm -rf test')
        tability = ability(ability_id='123', executors=[texecutor], repeatable=True, buckets=['test'])
        link = Link.load(dict(command='', paw='n1234', ability=tability, executor=next(tability.executors), status=0,
                              id='ganymede'))
        type1_fact = Fact(trait='t1', value='d1', score=1, collected_by=['thin_air'], technique_id='T1234',
                          links=[link.id], origin_type=OriginType.LEARNED)
        type2_fact = Fact(trait='t2', value='d2', score=1, collected_by=['thin_air'], technique_id='T1234',
                          links=[link.id], origin_type=OriginType.LEARNED)
        type3_fact = Fact(trait='t3', value='d3', score=1, collected_by=['tiny_lightning_bolts_running_through_sand'],
                          technique_id='T1234', origin_type=OriginType.SEEDED, source="Europa")
        await knowledge_svc.add_fact(type1_fact)
        await knowledge_svc.add_fact(type2_fact)
        await knowledge_svc.add_fact(type3_fact)
        origin_1, type_1 = await knowledge_svc.get_fact_origin(type1_fact)
        origin_2, type_2 = await knowledge_svc.get_fact_origin(type2_fact.trait)
        origin_3, type_3 = await knowledge_svc.get_fact_origin(type3_fact.trait)
        assert origin_1 == link.id
        assert origin_2 == link.id
        assert origin_3 == 'Europa'
        assert type_1 == OriginType.LEARNED
        assert type_2 == OriginType.LEARNED
        assert type_3 == OriginType.SEEDED
