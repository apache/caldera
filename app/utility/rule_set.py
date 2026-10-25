import re
from enum import Enum
import ipaddress


class RuleAction(Enum):
    ALLOW = 1
    DENY = 0


class RuleSet:
    def __init__(self, rules):
        self.rules = rules

    async def is_fact_allowed(self, fact):
        allowed = True
        for rule in await self._applicable_rules(fact):
            if await self._is_ip_rule(rule, fact):
                # An IP rule must not fall back to regex matching: as a regex, '10.0.0.1' also matches '10.0.0.10'.
                if await self._is_ip_rule_match(rule, fact):
                    allowed = await self._rule_judgement(rule.action)
                continue
            if await self._is_regex_rule_match(rule, fact):
                allowed = await self._rule_judgement(rule.action)
        return allowed

    async def _applicable_rules(self, fact):
        applicable_rules = []
        for rule in self.rules:
            if rule.trait == fact.trait:
                applicable_rules.append(rule)
        return applicable_rules

    async def apply_rules(self, facts):
        if await self._has_rules():
            valid_facts = []
            for fact in facts:
                if await self.is_fact_allowed(fact):
                    valid_facts.append(fact)
            return [valid_facts]
        else:
            return [facts]

    async def _has_rules(self):
        return len(self.rules)

    @staticmethod
    async def _rule_judgement(action):
        if action.value == RuleAction.DENY.value:
            return False
        return True

    @staticmethod
    async def _is_ip_network(value):
        try:
            ipaddress.IPv4Network(value)
            return True
        except (ValueError, ipaddress.AddressValueError):
            pass
        return False

    @staticmethod
    async def _is_ip_address(value):
        try:
            ipaddress.IPv4Address(value)
            return True
        except (ValueError, ipaddress.AddressValueError):
            pass
        return False

    @staticmethod
    async def _is_regex_rule_match(rule, fact):
        return re.match(rule.match, fact.value)

    async def _is_ip_rule(self, rule, fact):
        """True when the rule match is an IPv4 address or network and the fact value is an IPv4 address.

        Network-valued facts keep the regex fallback: _is_ip_rule_match never matches a rule address against a
        fact network, and deciding on that alone would let a fact covering a denied address pass a DENY rule.
        """
        if rule.match == '.*':
            return False
        is_rule_ip = await self._is_ip_address(rule.match) or await self._is_ip_network(rule.match)
        if not is_rule_ip:
            return False
        return await self._is_ip_address(fact.value)

    async def _is_ip_rule_match(self, rule, fact):
        """ We only match string-equivalent IP addresses, string-equivalent subnets in CIDR notation, and IP address
        facts to the subnet rules where the address is a member of the subnet. Matching non-equivalent subnets is
        complicated, because of the following general case:
                                    _____________________________________
                                    |         Rule       |     Fact     |
                                    -------------------------------------
                                    | DENY: 127.0.0.0/24 | 127.0.0.0/23 |
                                    -------------------------------------
        In the above case, we do not match on this fact, since the fact is a supernet of the rule (it "contains" the
        rule subnet). Therefore, the rule subnet is only a portion of the fact subnet. Thus, Caldera would ignore the
        DENY rule and scan /23 anyway. But this would include a denied subnet range, which is undesired behavior.
        This being the case, Caldera does not match on non-equivalent subnets.
        """
        if rule.match != '.*':
            is_fact_address = await self._is_ip_address(fact.value)
            is_fact_network = await self._is_ip_network(fact.value)
            is_rule_address = await self._is_ip_address(rule.match)
            is_rule_network = await self._is_ip_network(rule.match)

            if is_fact_address and is_rule_address:
                return fact.value == rule.match
            elif is_fact_network and is_rule_address:
                return False
            elif is_fact_address and is_rule_network:
                return ipaddress.IPv4Address(fact.value) in ipaddress.IPv4Network(rule.match)
            elif is_fact_network and is_rule_network:
                return fact.value == rule.match
        return False
