"""Preview or install Python dependencies for a selected Caldera plugin set."""

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY = {'training': 'requirements-training.txt', 'fieldmanual': 'requirements-fieldmanual.txt'}


@dataclass(frozen=True)
class Plan:
    plugins: tuple
    files: tuple
    without_manifest: tuple


def configured_plugins(path):
    try:
        import yaml
    except ImportError as error:
        raise ValueError('Reading YAML needs PyYAML. Install PyYAML or use --plugins for initial setup.') from error
    try:
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError('Cannot read the selected YAML configuration. Check its path, permissions and syntax.') from error
    if not isinstance(config, dict) or 'plugins' not in config:
        raise ValueError('Configuration must contain a plugins list. Use plugins: [] for core and Magma only.')
    return config['plugins']


def selection(names):
    if not isinstance(names, list) or any(not isinstance(p, str) or not re.fullmatch(r'\w[\w-]*', p, flags=re.ASCII) for p in names):
        raise ValueError('Plugin selection must be a list of directory names, without paths or empty names.')
    return tuple(sorted(set(names) | {'magma'}))


def manifest(root, name):
    directory = root / 'plugins' / name
    if not (directory / 'hook.py').is_file():
        raise ValueError(f'Plugin {name} is not checked out. Run git submodule update --init --recursive or restore the plugin.')
    declared = directory / 'requirements.txt'
    if declared.is_file():
        return declared.relative_to(root).as_posix()
    return COMPATIBILITY.get(name)


def make_plan(root, names):
    plugins = selection(names)
    files = ['requirements-core.txt']
    without_manifest = []
    for name in plugins:
        requirement = manifest(root, name)
        if requirement is None:
            without_manifest.append(name)
        elif requirement not in files:
            files.append(requirement)
    for requirement in files:
        if not (root / requirement).is_file():
            raise ValueError(f'Requirements file is missing: {requirement}')
    return Plan(plugins, tuple(files), tuple(without_manifest))


def pip_command(plan):
    command = [sys.executable, '-m', 'pip', 'install']
    for requirement in plan.files:
        command.extend(['-r', requirement])
    return command


def install(plan, root):
    if sys.prefix == sys.base_prefix:
        raise ValueError('Activate a Python virtual environment before using --install.')
    # One pip invocation resolves core and all selected plugin constraints
    # together. Execute only pip, never plugin hooks or configured commands.
    return subprocess.run(pip_command(plan), cwd=root, check=False).returncode


def requested_plugins(args):
    if args.plugins is not None:
        return args.plugins.split(',') if args.plugins else []
    config = args.config if args.config is not None else ROOT / 'conf' / 'default.yml'
    return configured_plugins(config)


def display_plan(plan):
    print('Selected plugins (Magma is automatic): ' + ', '.join(plan.plugins))
    print('Requirements files:\n  ' + '\n  '.join(plan.files))
    if plan.without_manifest:
        print('No Python requirements manifest: ' + ', '.join(plan.without_manifest))
    print('Command (run from the Caldera directory): ' + shlex.join(pip_command(plan)))
    print('Use the same configuration and --plugins ' + ','.join(plan.plugins) + ' when starting server.py.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Preview Python dependencies for enabled plugins; add --install to run pip in a virtual environment.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--config', type=Path, help='Read the plugins list from this YAML file (default: conf/default.yml).')
    group.add_argument('--plugins', help='Comma-separated plugin names; an empty string selects only core and automatic Magma.')
    parser.add_argument('--install', action='store_true', help='Install the displayed plan using this interpreter (virtual environment required).')
    parser.add_argument('--json', action='store_true', help='Print the preview as JSON; incompatible with --install.')
    args = parser.parse_args(argv)
    if args.json and args.install:
        parser.error('--json is preview-only and cannot be combined with --install')
    try:
        plan = make_plan(ROOT, requested_plugins(args))
        if args.json:
            print(json.dumps(asdict(plan), indent=2))
            return 0
        display_plan(plan)
        if args.install:
            return install(plan, ROOT)
        print('Preview only. Add --install to install in your active virtual environment.')
        return 0
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
