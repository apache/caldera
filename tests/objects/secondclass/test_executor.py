import pytest

from app.objects.secondclass.c_executor import Executor, get_variations
from app.objects.secondclass.c_variation import Variation
from app.utility.base_world import BaseWorld


@pytest.fixture(scope='session')
def executors_base_world():
    BaseWorld.apply_config(name='main', config={'app.contact.http': 'http://0.0.0.0:8888',
                                                'host': '0.0.0.0',
                                                'port': '8888',
                                                'crypt_salt': 'BLAH',
                                                'api_key': 'ADMIN123',
                                                'encryption_key': 'ADMIN123',
                                                'exfil_dir': '/tmp'})


@pytest.fixture
def test_executor(executor):
    return executor(name='sh', platform='linux')


@pytest.mark.usefixtures(
    'executors_base_world'
)
class TestExecutor:
    def test_is_global_variable(self):
        assert not Executor.is_global_variable('notaglobalvariable')
        assert Executor.is_global_variable('payload')

    def test_replace_cleanup(self, test_executor):
        assert 'this has been replaced' == test_executor.replace_cleanup('this has been replaced', 'somepayload')
        assert 'this has been replaced: somepayload' == test_executor.replace_cleanup('this has been replaced: #{payload}', 'somepayload')

    def test_get_variations(self):
        assert [] == get_variations([])
        want_variation = Variation('test description', 'test command')
        second_variation = Variation('test description', 'test command')
        diff_variation = Variation('test description 2', 'test command 2')
        assert want_variation == second_variation
        assert not diff_variation == want_variation
        var_dict = dict(description='test description', command='test command')
        assert [want_variation] == get_variations([var_dict])
        result = get_variations([var_dict, want_variation])
        second_want = Variation('test description', Variation.encode_string('test command'))
        assert [want_variation, second_want] == result
