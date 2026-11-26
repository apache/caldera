"""Validate main server YAML without importing or starting the server."""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Problem:
    file: str
    line: int
    column: int
    field: str
    message: str
    remedy: str


class _Validator:
    def __init__(self, path, yaml):
        self.path = path
        self.yaml = yaml
        self.problems = []
        self.marks = {}

    def report(self, field, message, remedy, node=None):
        mark = getattr(node, 'start_mark', None)
        self.problems.append(Problem(str(self.path), mark.line + 1 if mark else 1,
                                     mark.column + 1 if mark else 1, field, message, remedy))

    def read(self):
        try:
            with self.path.open('rb') as source:
                contents = source.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                self.report('configuration', 'Configuration exceeds the 1 MiB validation limit.', 'Use a smaller main configuration file.')
                return None
            return contents.decode('utf-8')
        except (OSError, ValueError):
            self.report('configuration', 'Cannot read the configuration as UTF-8 YAML.', 'Check that the selected file exists and is readable UTF-8.')
            return None

    def mapping_duplicates(self, node, loader, visited):
        keys = set()
        for key, value in node.value:
            if isinstance(key, self.yaml.ScalarNode) and key.tag != 'tag:yaml.org,2002:merge':
                identity = loader.construct_object(key)
                if identity in keys:
                    self.report('mapping', 'Duplicate YAML key.', 'Keep one explicit occurrence of this key in this mapping.', key)
                keys.add(identity)
            self.duplicates(value, loader, visited)

    def duplicates(self, node, loader, visited):
        if node is None or id(node) in visited:
            return
        visited.add(id(node))
        if isinstance(node, self.yaml.MappingNode):
            self.mapping_duplicates(node, loader, visited)
        elif isinstance(node, self.yaml.SequenceNode):
            for child in node.value:
                self.duplicates(child, loader, visited)

    def invalid(self, field, valid, message, remedy):
        if not valid:
            self.report(field, message, remedy, self.marks.get(field))

    def document(self, root, loader):
        config = loader.construct_document(root) if root is not None else None
        if not isinstance(config, dict):
            self.report('configuration', 'Main configuration must be a YAML mapping.', 'Use key: value entries at the top level.', root)
            return
        self.marks = {key.value: value for key, value in root.value if isinstance(key, self.yaml.ScalarNode)}
        for field in ('host', 'port', 'plugins', 'users'):
            self.invalid(field, field in config, 'Required field is missing.', 'Add this field; see conf/default.yml for its structure.')
        self.strings(config)
        self.integers(config)
        self.plugins(config)
        self.traits(config)
        self.users(config)

    def strings(self, config):
        strings = ('host', 'api_key_red', 'api_key_blue', 'crypt_salt', 'encryption_key',
                   'reports_dir', 'exfil_dir', 'auth.login.handler.module')
        for field in strings:
            if field in config:
                value = config[field]
                self.invalid(field, isinstance(value, str) and bool(value.strip()), 'Expected a non-empty string.', 'Provide a non-empty YAML string.')

    def integers(self, config):
        integers = {'port': (1, 65535), 'ability_refresh': (1, None), 'session_expiration_days': (1, None),
                    'client_max_size_mb': (1, None), 'api_upload_max_size_mb': (1, None)}
        for field, (minimum, maximum) in integers.items():
            if field in config:
                value = config[field]
                valid = type(value) is int and value >= minimum and (maximum is None or value <= maximum)
                remedy = f'Use an integer from {minimum} to {maximum}.' if maximum else f'Use an integer of at least {minimum}.'
                self.invalid(field, valid, 'Invalid integer setting (booleans are not integers here).', remedy)

    def plugins(self, config):
        if 'plugins' in config:
            value = config['plugins']
            valid = isinstance(value, list) and all(isinstance(p, str) and re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in value)
            self.invalid('plugins', valid, 'Expected a list of plugin directory names.', 'Use a YAML list of names such as stockpile and sandcat.')
            if valid:
                self.invalid('plugins', len(value) == len(set(value)), 'A plugin is listed more than once.', 'List each plugin once.')

    def traits(self, config):
        if 'reachable_host_traits' in config:
            value = config['reachable_host_traits']
            self.invalid('reachable_host_traits', isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
                         'Expected a list of non-empty strings.', 'Use a YAML list of fact trait names.')

    @staticmethod
    def valid_account(name, password):
        return isinstance(name, str) and bool(name.strip()) and isinstance(password, str) and bool(password.strip())

    def valid_group(self, group, accounts):
        return isinstance(group, str) and isinstance(accounts, dict) and all(self.valid_account(name, password) for name, password in accounts.items())

    def users(self, config):
        if 'users' in config:
            users = config['users']
            valid = isinstance(users, dict) and bool(users)
            if valid:
                valid = all(self.valid_group(group, accounts) for group, accounts in users.items())
            self.invalid('users', valid, 'Expected user groups containing username: password string mappings.',
                         'Use the users structure in conf/default.yml; quote passwords that YAML could interpret as other types.')

    def yaml_error(self, error):
        mark = getattr(error, 'problem_mark', None)
        self.problems.append(Problem(str(self.path), mark.line + 1 if mark else 1, mark.column + 1 if mark else 1,
                                     'configuration', 'Invalid or unsupported YAML structure.',
                                     'Check indentation, delimiters and key types; use standard safe YAML tags.'))

    def run(self):
        contents = self.read()
        if contents is None:
            return self.problems
        loader = None
        try:
            loader = self.yaml.SafeLoader(contents)
            root = loader.get_single_node()
            self.duplicates(root, loader, set())
            if not self.problems:
                self.document(root, loader)
        except (self.yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
            self.yaml_error(error)
        finally:
            if loader is not None:
                loader.dispose()
        return self.problems


def validate(path):
    """Check main fields, allowing extension keys and suppressing source values."""
    try:
        import yaml
    except ImportError:
        return [Problem(str(path), 1, 1, 'configuration', 'PyYAML is not installed.',
                        'Run: python3 -m pip install -r requirements.txt')]
    return _Validator(path, yaml).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validate a main Caldera YAML configuration without starting the server.')
    parser.add_argument('file', nargs='?', type=Path, default=Path('conf/local.yml'), help='Main configuration file (default: conf/local.yml).')
    parser.add_argument('--json', action='store_true', help='Print structured diagnostics as JSON.')
    args = parser.parse_args(argv)
    problems = validate(args.file)
    if args.json:
        print(json.dumps({'file': str(args.file), 'valid': not problems, 'problems': [asdict(p) for p in problems]}, indent=2))
    else:
        for problem in problems:
            print(f'{problem.file}:{problem.line}:{problem.column}: {problem.field}: {problem.message}')
            print(f'  Fix: {problem.remedy}')
        if not problems:
            print(f'{args.file}: checked main configuration fields are valid.')
    return int(bool(problems))


if __name__ == '__main__':
    sys.exit(main())
