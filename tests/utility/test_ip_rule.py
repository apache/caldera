from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_rule import Rule
from app.utility.rule_set import RuleAction
from app.utility.rule_set import RuleSet


class TestIPRule:
    host1 = '127.0.0.1'
    host2 = '127.0.1.0'
    host3 = '128.0.0.1'
    host4 = '127.0.0.0/23'
    host5 = '127.0.0.0/25'
    subnet1 = '127.0.0.0/24'
    fact1 = Fact(trait='host.ip.address', value=host1)
    fact2 = Fact(trait='host.ip.address', value=host2)
    fact3 = Fact(trait='host.ip.address', value=host3)
    fact4 = Fact(trait='host.ip.address', value=host4)
    fact5 = Fact(trait='host.ip.address', value=host5)
    fact6 = Fact(trait='host.ip.address', value=subnet1)
    rule = Rule(trait='host.ip.address', action=RuleAction.DENY, match=subnet1)
    rs = RuleSet(rules=[rule])

    def test_rule_serialize(self):
        rule_display = self.rule.display
        assert rule_display['trait'] == 'host.ip.address'
        assert rule_display['action'] == 'DENY'
        assert rule_display['match'] == self.subnet1

    def test_rule_deserialize(self):
        rule_serialized = {
            "trait": "host.ip.address",
            "action": "DENY",
            "match": self.subnet1,
        }
        test_rule = Rule.load(rule_serialized)
        assert test_rule.trait == 'host.ip.address'
        assert test_rule.action == RuleAction.DENY
        assert test_rule.match == self.subnet1

    async def test_is_ip_rule_match(self):
        assert await self.rs._is_ip_rule_match(self.rule, self.fact1)
        assert (not await self.rs._is_ip_rule_match(self.rule, self.fact2))
        assert (not await self.rs._is_ip_rule_match(self.rule, self.fact3))

    async def test_is_fact_allowed(self):
        assert (not await self.rs.is_fact_allowed(self.fact1))
        assert await self.rs.is_fact_allowed(self.fact2)
        assert await self.rs.is_fact_allowed(self.fact3)

    async def test_smaller_subnet(self):
        assert (not await self.rs._is_ip_rule_match(self.rule, self.fact4))
        assert await self.rs.is_fact_allowed(self.fact4)

    async def test_larger_subnet(self):
        assert (not await self.rs._is_ip_rule_match(self.rule, self.fact5))
        assert await self.rs.is_fact_allowed(self.fact5)

    async def test_same_subnet(self):
        assert await self.rs._is_ip_rule_match(self.rule, self.fact6)
        assert (not await self.rs.is_fact_allowed(self.fact6))

    async def test_address_rule_does_not_match_longer_address(self):
        rs = RuleSet(rules=[Rule(trait='host.ip.address', action=RuleAction.DENY, match='127.0.0.1')])
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='127.0.0.1'))
        assert await rs.is_fact_allowed(Fact(trait='host.ip.address', value='127.0.0.10'))
        assert await rs.is_fact_allowed(Fact(trait='host.ip.address', value='127.0.0.123'))

    async def test_allow_single_address_after_deny_all(self):
        rs = RuleSet(rules=[Rule(trait='host.ip.address', action=RuleAction.DENY, match='.*'),
                            Rule(trait='host.ip.address', action=RuleAction.ALLOW, match='10.0.0.1')])
        assert await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.1'))
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.10'))
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.100'))

    async def test_address_rule_still_denies_network_facts(self):
        rs = RuleSet(rules=[Rule(trait='host.ip.address', action=RuleAction.DENY, match='10.0.0.1')])
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.1/32'))
        rs = RuleSet(rules=[Rule(trait='host.ip.address', action=RuleAction.DENY, match='10.0.0.0')])
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.0/24'))

    async def test_regex_rule_still_applies_to_ip_facts(self):
        rs = RuleSet(rules=[Rule(trait='host.ip.address', action=RuleAction.DENY, match='.*'),
                            Rule(trait='host.ip.address', action=RuleAction.ALLOW, match=r'10\.0\.0\.\d+$')])
        assert await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.0.10'))
        assert not await rs.is_fact_allowed(Fact(trait='host.ip.address', value='10.0.1.10'))
