"""Runtime compatibility checks for the standalone installation doctor."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utility import doctor


class TestRuntimeVersions(unittest.TestCase):
    def test_node_vite_boundaries(self):
        for version, expected in [('20.18.9', False), ('20.19.0', True), ('20.20.0', True),
                                  ('21.0.0', False), ('22.11.9', False), ('22.12.0', True), ('24.0.0', True)]:
            with self.subTest(version=version):
                self.assertIs(doctor.version_satisfies(version, '^20.19.0 || >=22.12.0'), expected)

    def test_comparators_and_caret_zero_major(self):
        for version, constraint, expected in [('1.24.0', '>=1.24', True), ('1.23.9', '>=1.24.0', False),
                                              ('22.12.0', '>=22.0.0 <23.0.0', True), ('23.0.0', '>=22.0.0 <23.0.0', False),
                                              ('0.3.0', '^0.2.0', False), ('0.2.9', '^0.2.0', True),
                                              ('0.0.2', '^0.0.1', False), ('1.2.0', '=1.2', True)]:
            with self.subTest(version=version, constraint=constraint):
                self.assertIs(doctor.version_satisfies(version, constraint), expected)

    def test_unknown_syntax_never_silently_passes(self):
        for constraint in ['', '*', '>=20', '20.x', '^0.0', '~20.1.0', '>=20.0.0 || *', '>=20.0.0 ||']:
            with self.subTest(constraint=constraint):
                self.assertIsNone(doctor.version_satisfies('24.0.0', constraint))

    def test_constraints_follow_checkout_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(doctor.node_constraints(root))
            self.assertIsNone(doctor.go_constraints(root))
            magma = root / 'plugins/magma'
            magma.mkdir(parents=True)
            (magma / 'package.json').write_text(json.dumps({'engines': {'node': '>=24.0.0'}}))
            lock = {'packages': {'node_modules/vite': {'engines': {'node': '^20.19.0 || >=22.12.0'}}}}
            (magma / 'package-lock.json').write_text(json.dumps(lock))
            self.assertEqual(doctor.node_constraints(root), ['^20.19.0 || >=22.12.0', '>=24.0.0'])
            gocat = root / 'plugins/sandcat/gocat'
            gocat.mkdir(parents=True)
            (gocat / 'go.mod').write_text('module example\n\ngo 1.25.0\ntoolchain go1.25.4\n')
            self.assertEqual(doctor.go_constraints(root), ['>=1.25.0'])
            (magma / 'package-lock.json').write_text('[]')
            self.assertIsNone(doctor.node_constraints(root))

    def probe(self, name, output, constraints, required=False):
        result = subprocess.CompletedProcess([], 0, output, '')
        command = ['go', 'version'] if name == 'Go' else ['node', '--version']
        with patch.object(doctor.shutil, 'which', return_value='/usr/bin/tool'), patch.object(doctor.subprocess, 'run', return_value=result) as run:
            check = doctor.check_tool(name, command, 'Install compatible runtime.', constraints, required)
        return check, run.call_args.kwargs

    def test_runtime_results_and_go_probe_environment(self):
        check, _ = self.probe('Node.js', 'v22.11.0\n', ['^20.19.0 || >=22.12.0'], True)
        self.assertEqual(check.status, 'FAIL')
        check, _ = self.probe('Node.js', 'v24.0.0\n', ['>=22.12.0', '<24.0.0'], True)
        self.assertEqual(check.status, 'FAIL')
        check, _ = self.probe('Node.js', 'v24.0.0\n', None, True)
        self.assertEqual(check.status, 'WARN')
        check, _ = self.probe('Node.js', 'v24.0.0-rc.1\n', ['>=22.12.0'], True)
        self.assertEqual(check.status, 'WARN')
        check, kwargs = self.probe('Go', 'go version go1.23.9 linux/amd64\n', ['>=1.24.0'])
        self.assertEqual(check.status, 'WARN')
        self.assertEqual(kwargs['env']['GOTOOLCHAIN'], 'local')
        self.assertEqual(kwargs['env']['GOTELEMETRY'], 'off')
        check, _ = self.probe('Go', 'go version go1.24.0 linux/amd64\n', ['>=1.24.0'])
        self.assertEqual(check.status, 'PASS')
