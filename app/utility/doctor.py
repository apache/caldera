"""Read-only installation diagnostics: python3 -m app.utility.doctor.

Keep server and plugin imports out of this module: it must run even when the
installation is incomplete. PyYAML and packaging are loaded only for checks
which need them, and missing dependencies are reported rather than raised.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALL_CORE = 'Install core dependencies with: python3 -m pip install -r requirements.txt'


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    remedy: str = ''


def check_requirements(path, root, required=True):
    """Inspect installed distribution metadata without importing dependencies.

    Only PEP 508 requirement lines are evaluated. pip-specific directives are
    reported as unchecked; the doctor never installs packages or follows URLs.
    """
    severity = 'FAIL' if required else 'WARN'
    label = path.relative_to(root).as_posix()
    remedy = f'Install dependencies with: python3 -m pip install -r {label}'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        return [Check(label, severity, 'Cannot read the requirements file.', remedy)]
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:
        return [Check(label, severity, 'Cannot check package versions: packaging is not installed.', INSTALL_CORE)]

    checks = []
    for number, line in enumerate(lines, 1):
        requirement = re.split(r'\s+#', line, maxsplit=1)[0].strip()
        if not requirement or requirement.startswith('#'):
            continue
        name = f'{label}:{number}'
        try:
            spec = Requirement(requirement)
        except InvalidRequirement:
            checks.append(Check(name, 'WARN', 'Requirement syntax is not checked by doctor.',
                                'Review this line with pip; included files and pip options are not evaluated.'))
            continue
        if spec.marker and not spec.marker.evaluate({'extra': ''}):
            continue
        if spec.url or spec.extras:
            checks.append(Check(name, 'WARN', f'URL or extras requirement for {spec.name} is not checked.',
                                'Verify this dependency with pip.'))
            continue
        try:
            installed = metadata.version(spec.name)
        except metadata.PackageNotFoundError:
            checks.append(Check(name, severity, f'{spec.name} is not installed.', remedy))
            continue
        except (OSError, ValueError):
            checks.append(Check(name, severity, f'Cannot read installed metadata for {spec.name}.', remedy))
            continue
        try:
            # As with pip check, validate installed prereleases against the
            # bounds rather than applying an install-time candidate preference.
            matches = spec.specifier.contains(installed, prereleases=True)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            checks.append(Check(name, severity, f'{spec.name} {installed} does not satisfy {spec.specifier}.', remedy))
    if not checks:
        checks.append(Check(label, 'PASS', 'Installed packages satisfy the checked requirements.'))
    return checks


def load_config(root, environment):
    """Read the selected config without generating local credentials or files."""
    path = root / 'conf' / f'{environment}.yml'
    checks = []
    if environment == 'local' and not path.exists():
        path = root / 'conf' / 'default.yml'
        checks.append(Check('configuration', 'WARN', 'conf/local.yml is absent; checking conf/default.yml.',
                            'Normal server startup generates local.yml; doctor does not create it.'))
    try:
        import yaml
    except ImportError:
        return None, checks + [Check('configuration', 'FAIL', 'Cannot read configuration: PyYAML is not installed.', INSTALL_CORE)]
    try:
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError):
        # YAML exception messages may contain passwords or tokens from the file.
        checks.append(Check('configuration', 'FAIL', 'Cannot read the selected YAML configuration.',
                            'Check the selected conf/<environment>.yml file and its read permissions.'))
        return None, checks
    if not isinstance(config, dict):
        return None, checks + [Check('configuration', 'FAIL', 'Configuration must be a YAML mapping.')]
    plugins = config.get('plugins', [])
    if not isinstance(plugins, list) or any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in plugins):
        return None, checks + [Check('configuration', 'FAIL', 'plugins must be a list of plugin directory names.')]
    checks.append(Check('configuration', 'PASS', f'Read conf/{path.name}; credentials are not displayed.'))
    return config, checks


def version_satisfies(version, constraints):
    """Evaluate numeric runtime ranges; return None for unsupported syntax.

    Supports comparator conjunctions, || alternatives and npm caret ranges.
    Wildcards, prereleases and other npm syntax are deliberately unchecked.
    """
    def numeric(value):
        if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
            raise ValueError
        return tuple(int(p) for p in value.split('.')) + ((0,) if value.count('.') == 1 else ())

    try:
        installed = numeric(version)
        alternatives = []
        for alternative in constraints.split('||'):
            matches = []
            for token in alternative.split():
                match = re.fullmatch(r'(\^|>=|<=|>|<|=)?(\d+\.\d+(?:\.\d+)?)', token)
                if not match:
                    return None
                operator, target = match.groups()
                bound = numeric(target)
                if operator == '^':
                    if target.count('.') != 2:
                        return None
                    index = next((i for i, part in enumerate(bound) if part), 2)
                    upper = bound[:index] + (bound[index] + 1,) + (0,) * (2 - index)
                    matches.append(bound <= installed < upper)
                else:
                    matches.append({'>=': installed >= bound, '>': installed > bound,
                                    '<=': installed <= bound, '<': installed < bound,
                                    '=': installed == bound, None: installed == bound}[operator])
            if not matches:
                return None
            alternatives.append(all(matches))
        return any(alternatives)
    except (ValueError, AttributeError, TypeError):
        return None


def node_constraints(root):
    """Read requirements of the checked-out UI, rather than hardcoding Vite versions."""
    try:
        path = root / 'plugins' / 'magma'
        package = json.loads((path / 'package.json').read_text(encoding='utf-8'))
        lock = json.loads((path / 'package-lock.json').read_text(encoding='utf-8'))
        vite = lock['packages']['node_modules/vite']['engines']['node']
        declared = package.get('engines', {}).get('node')
        ranges = [vite] + ([declared] if declared is not None else [])
        return ranges if all(isinstance(r, str) and r for r in ranges) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def go_constraints(root):
    try:
        module = (root / 'plugins' / 'sandcat' / 'gocat' / 'go.mod').read_text(encoding='utf-8')
        match = re.search(r'^go\s+(\d+\.\d+(?:\.\d+)?)\s*$', module, re.MULTILINE)
        return [f'>={match.group(1)}'] if match else None
    except (OSError, ValueError):
        return None


def check_tool(name, command, remedy, constraints=(), required=False):
    """Probe only fixed version commands, without a shell or network requests."""
    executable = shutil.which(command[0])
    if executable is None:
        return Check(name, 'WARN', f'{name} is not available on PATH.', remedy)
    try:
        environment = os.environ.copy()
        if command[0] == 'go':
            environment.update(GOTOOLCHAIN='local', GOTELEMETRY='off')
        result = subprocess.run(
            [executable, *command[1:]], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=5, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check(name, 'WARN', f'Could not query the {name} version.', remedy)
    pattern = r'go version go(\d+\.\d+(?:\.\d+)?)(?=\s|$)' if command[0] == 'go' else r'^v?(\d+\.\d+(?:\.\d+)?)(?=\s|$)'
    match = re.search(pattern, result.stdout)
    if result.returncode or not match:
        return Check(name, 'WARN', f'Could not determine the {name} version.', remedy)
    installed = match.group(1)
    if constraints is None:
        return Check(name, 'WARN', f'{name} {installed}: version requirements could not be read.', remedy)
    outcomes = [version_satisfies(installed, constraint) for constraint in constraints]
    if None in outcomes:
        return Check(name, 'WARN', f'{name} {installed}: runtime range syntax is not supported by doctor.', remedy)
    if not all(outcomes):
        return Check(name, 'FAIL' if required else 'WARN', f'{name} {installed} does not satisfy {" AND ".join(constraints)}.', remedy)
    return Check(name, 'PASS', f'{name} {installed} is available' + (f' and satisfies {" AND ".join(constraints)}.' if constraints else '.'))


def diagnose(root, environment='local'):
    checks = []
    supported = platform.system() in ('Linux', 'Darwin')
    checks.append(Check('platform', 'PASS' if supported else 'FAIL',
                        'Supported server platform.' if supported else 'The Caldera server requires Linux or macOS.',
                        '' if supported else 'On Windows, run Caldera and doctor inside WSL or a Linux container.'))
    supported_python = sys.version_info >= (3, 11)
    checks.append(Check('python', 'PASS' if supported_python else 'FAIL', f'Python {platform.python_version()}; required >=3.11.',
                        '' if supported_python else 'Use Python 3.11 or later.'))
    checks.extend(check_requirements(root / 'requirements.txt', root))
    config, config_checks = load_config(root, environment)
    checks.extend(config_checks)

    # server.py attempts to load every plugin directory by default, including
    # plugins not enabled in the config. Check their entrypoints without imports.
    plugin_dir = root / 'plugins'
    try:
        plugins = {p.name for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith(('.', '__'))}
    except OSError:
        plugins = set()
        checks.append(Check('plugins', 'FAIL', 'Cannot read the plugins directory.'))
    enabled = set(config.get('plugins', [])) if config is not None else set()
    for name in sorted(plugins | enabled | {'magma'}):
        path = plugin_dir / name
        if not (path / 'hook.py').is_file():
            checks.append(Check(f'plugin:{name}', 'FAIL', 'Plugin entrypoint hook.py is missing.',
                                'For a Git clone, run: git submodule update --init --recursive. '
                                'For a source archive, follow the README plugin-revisions.txt instructions.'))
            continue
        checks.append(Check(f'plugin:{name}', 'PASS', 'Plugin entrypoint is present; plugin code was not executed.'))
        requirements = path / 'requirements.txt'
        if requirements.exists():
            checks.extend(check_requirements(requirements, root, required=name in enabled or name == 'magma'))

    # These optional dependencies live outside core requirements in 5.4.0.
    if config is not None:
        optional = []
        if 'debrief' in enabled:
            optional.append('requirements-debrief.txt')
        if config.get('ldap'):
            optional.append('requirements-ldap.txt')
        for filename in optional:
            if (root / filename).is_file():
                checks.extend(check_requirements(root / filename, root, required=False))

    if (plugin_dir / 'magma' / 'dist' / 'index.html').is_file():
        checks.append(Check('web-ui', 'PASS', 'Magma dist/index.html is present.'))
    else:
        checks.append(Check('web-ui', 'FAIL', 'The built Magma web interface is missing.',
                            'After installing dependencies and plugins, run: python3 server.py --build'))
        checks.append(check_tool('Node.js', ['node', '--version'], 'Install a Node.js version supported by plugins/magma.', node_constraints(root), True))
        checks.append(check_tool('npm', ['npm', '--version'], 'Install npm to build the Magma web interface.'))
    checks.append(check_tool('Go', ['go', 'version'], 'Install Go meeting the go directive in plugins/sandcat/gocat/go.mod.', go_constraints(root)))

    for directory in ('conf', 'data', 'data/results', 'data/payloads'):
        path = root / directory
        accessible = path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)
        checks.append(Check(directory, 'PASS' if accessible else 'FAIL',
                            'Directory access check passed.' if accessible else 'Directory is missing or not accessible for reading/writing.',
                            '' if accessible else 'Restore this directory and grant the server user access.'))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description='Diagnose a Caldera installation without starting the server or modifying its files.')
    parser.add_argument('-E', '--environment', default='local', help='Configuration name in conf/ (default: local, falling back to default).')
    parser.add_argument('--json', action='store_true', help='Print a JSON report instead of text.')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.environment):
        parser.error('environment must be a configuration name, not a path')
    checks = diagnose(ROOT, args.environment)
    summary = {status: sum(c.status == status for c in checks) for status in ('PASS', 'WARN', 'FAIL')}
    if args.json:
        print(json.dumps({'checks': [asdict(check) for check in checks], 'summary': summary}, indent=2))
    else:
        print('Caldera installation doctor')
        for check in checks:
            print(f'[{check.status}] {check.name}: {check.message}')
            if check.remedy:
                print(f'       {check.remedy}')
        print(f"\n{summary['PASS']} passed, {summary['WARN']} warnings, {summary['FAIL']} failed.")
    return int(summary['FAIL'] > 0)


if __name__ == '__main__':
    sys.exit(main())
