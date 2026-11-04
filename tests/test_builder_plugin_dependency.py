import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _declared_requirements():
    names = {}
    with open(os.path.join(REPO_ROOT, 'requirements.txt')) as f:
        for line in f:
            stripped = line.split('#', 1)[0].strip()
            if not stripped:
                continue
            match = re.match(r'^([A-Za-z0-9_.-]+)', stripped)
            if match:
                names[match.group(1).lower().replace('_', '-')] = stripped
    return names


def test_docker_sdk_declared_in_requirements():
    """Regression test for the builder plugin import failure (issue #3424).

    The builder plugin imports the Python Docker SDK at module load
    (``import docker`` in plugins/builder/app/build_svc.py) and pins
    ``docker~=6.1.0`` in plugins/builder/requirements.txt, but the core
    runtime environment never installed it: the Dockerfile runtime stage
    installs only requirements.txt. The plugin therefore failed to load
    with "No module named 'docker'". The SDK must be declared in the
    core requirements so every runtime install path provides it.
    """
    reqs = _declared_requirements()
    assert 'docker' in reqs, (
        'docker SDK is not declared in requirements.txt; '
        'the builder plugin will fail to import'
    )


def test_docker_sdk_importable():
    """The environment built from requirements.txt must provide `docker`."""
    import docker

    assert hasattr(docker, 'from_env')
