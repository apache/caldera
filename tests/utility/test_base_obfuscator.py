from app.objects.c_agent import Agent
from app.objects.secondclass.c_link import Link
from app.utility.base_obfuscator import BaseObfuscator


class MockObfuscator(BaseObfuscator):
    def __init__(self, agent):
        self.supported_platforms = dict(linux=['sh'])
        super().__init__(agent)

    def sh(self, link, arg):
        return f'obfuscated sh command {arg}'


class TestBaseObfuscator:
    def test_run_obfuscator(self, ability, executor):
        test_agent = Agent(paw='testpaw', sleep_min=5, sleep_max=5, watchdog=0, executors=['sh', 'proc'], platform='linux')
        test_executor = executor(name='sh', platform='linux')
        test_ability = ability(ability_id='123', executors=[test_executor])
        test_link = Link(command='original test command', paw='testpaw', ability=test_ability, id=111111, executor=test_executor)
        want = 'obfuscated sh command val'
        obf = MockObfuscator(test_agent)
        assert want == obf.run(test_link, arg='val')
        assert test_link.command_hash == 'eaa27692ee501903114ca1bde590e2ea80a14a615f965b5f4505aa4d73c0555e'
