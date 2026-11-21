import yaml
import pytest
from app.utility.base_world import BaseWorld


class TestYamlSafeLoader:
    def test_loads_basic_yaml(self, tmp_path):
        f = tmp_path / 'basic.yml'
        f.write_text('key: value\nnumber: 42\n')
        result = BaseWorld.strip_yml(str(f))
        assert result == [{'key': 'value', 'number': 42}]

    def test_loads_list_yaml(self, tmp_path):
        f = tmp_path / 'list.yml'
        f.write_text('items:\n- one\n- two\n- three\n')
        result = BaseWorld.strip_yml(str(f))
        assert result == [{'items': ['one', 'two', 'three']}]

    def test_none_path_returns_empty(self):
        result = BaseWorld.strip_yml(None)
        assert result == []

    def test_rejects_unsafe_yaml_construct(self, tmp_path):
        """SafeLoader should reject Python-specific YAML tags."""
        f = tmp_path / 'unsafe.yml'
        f.write_text('!!python/object/apply:os.system ["echo pwned"]\n')
        with pytest.raises(yaml.YAMLError):
            BaseWorld.strip_yml(str(f))
