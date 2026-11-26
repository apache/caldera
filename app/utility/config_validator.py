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


def validate(path):
    """Return actionable diagnostics without including YAML values or excerpts.

    Unknown keys are accepted for plugins and custom login handlers. This is a
    main-config schema check, not validation of every plugin's configuration.
    """
    problems = []

    def report(field, message, remedy, node=None):
        mark = getattr(node, 'start_mark', None)
        problems.append(Problem(str(path), mark.line + 1 if mark else 1,
                                mark.column + 1 if mark else 1, field, message, remedy))

    try:
        import yaml
    except ImportError:
        report('configuration', 'PyYAML is not installed.', 'Run: python3 -m pip install -r requirements.txt')
        return problems
    try:
        with path.open('rb') as source:
            contents = source.read(1024 * 1024 + 1)
        if len(contents) > 1024 * 1024:
            report('configuration', 'Configuration exceeds the 1 MiB validation limit.', 'Use a smaller main configuration file.')
            return problems
        loader = yaml.SafeLoader(contents.decode('utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        report('configuration', 'Cannot read the configuration as UTF-8 YAML.', 'Check that the selected file exists and is readable UTF-8.')
        return problems
    try:
        root = loader.get_single_node()
        visited = set()

        def duplicates(node):
            if node is None or id(node) in visited:
                return
            visited.add(id(node))
            if isinstance(node, yaml.MappingNode):
                keys = set()
                for key, value in node.value:
                    if isinstance(key, yaml.ScalarNode) and key.tag != 'tag:yaml.org,2002:merge':
                        identity = loader.construct_object(key)
                        if identity in keys:
                            report('mapping', 'Duplicate YAML key.', 'Keep one explicit occurrence of this key in this mapping.', key)
                        keys.add(identity)
                    duplicates(value)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    duplicates(child)

        duplicates(root)
        if problems:
            return problems
        config = loader.construct_document(root) if root is not None else None
        if not isinstance(config, dict):
            report('configuration', 'Main configuration must be a YAML mapping.', 'Use key: value entries at the top level.', root)
            return problems
        marks = {key.value: value for key, value in root.value if isinstance(key, yaml.ScalarNode)}

        def invalid(field, valid, message, remedy):
            if not valid:
                report(field, message, remedy, marks.get(field))

        for field in ('host', 'port', 'plugins', 'users'):
            invalid(field, field in config, 'Required field is missing.', 'Add this field; see conf/default.yml for its structure.')
        strings = ('host', 'api_key_red', 'api_key_blue', 'crypt_salt', 'encryption_key',
                   'reports_dir', 'exfil_dir', 'auth.login.handler.module')
        for field in strings:
            if field in config:
                value = config[field]
                invalid(field, isinstance(value, str) and bool(value.strip()), 'Expected a non-empty string.', 'Provide a non-empty YAML string.')
        integers = {'port': (1, 65535), 'ability_refresh': (1, None), 'session_expiration_days': (1, None),
                    'client_max_size_mb': (1, None), 'api_upload_max_size_mb': (1, None)}
        for field, (minimum, maximum) in integers.items():
            if field in config:
                value = config[field]
                valid = type(value) is int and value >= minimum and (maximum is None or value <= maximum)
                remedy = f'Use an integer from {minimum} to {maximum}.' if maximum else f'Use an integer of at least {minimum}.'
                invalid(field, valid, 'Invalid integer setting (booleans are not integers here).', remedy)
        if 'plugins' in config:
            value = config['plugins']
            valid = isinstance(value, list) and all(isinstance(p, str) and re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in value)
            invalid('plugins', valid, 'Expected a list of plugin directory names.', 'Use a YAML list of names such as stockpile and sandcat.')
            if valid:
                invalid('plugins', len(value) == len(set(value)), 'A plugin is listed more than once.', 'List each plugin once.')
        if 'reachable_host_traits' in config:
            value = config['reachable_host_traits']
            invalid('reachable_host_traits', isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
                    'Expected a list of non-empty strings.', 'Use a YAML list of fact trait names.')
        if 'users' in config:
            users = config['users']
            valid = isinstance(users, dict) and bool(users)
            if valid:
                valid = all(isinstance(group, str) and isinstance(accounts, dict) and
                            all(isinstance(name, str) and bool(name.strip()) and isinstance(password, str) and bool(password.strip())
                                for name, password in accounts.items()) for group, accounts in users.items())
            invalid('users', valid, 'Expected user groups containing username: password string mappings.',
                    'Use the users structure in conf/default.yml; quote passwords that YAML could interpret as other types.')
        return problems
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        mark = getattr(error, 'problem_mark', None)
        problems.append(Problem(str(path), mark.line + 1 if mark else 1, mark.column + 1 if mark else 1,
                                'configuration', 'Invalid or unsupported YAML structure.',
                                'Check indentation, delimiters and key types; use standard safe YAML tags.'))
        return problems
    finally:
        loader.dispose()


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
