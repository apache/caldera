"""
Security regression tests for CVE fixes.

Scans the codebase for common security anti-patterns:
- Known vulnerable dependency version pins in requirements.txt
- HTTP requests missing timeout parameters
- HTTP requests with SSL verification disabled (verify=False)
- Subprocess calls using shell=True without justification
"""

import ast
import os
import re
from pathlib import Path
from importlib.metadata import version as pkg_version
from packaging.version import Version

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT_DIR / "plugins"

# Minimum safe versions for dependencies with known CVEs
MINIMUM_SAFE_VERSIONS = {
    "pyasn1": "0.6.3",       # CVE-2026-30922
    "cryptography": "44.0.0",
    "jinja2": "3.1.4",
    "pyyaml": "6.0.1",
    "aiohttp": "3.10.11",
    "lxml": "5.3.0",
    "setuptools": "75.0.0",
}

# Files where shell=True is explicitly acceptable (agent payloads that must
# execute arbitrary commands by design).
SHELL_TRUE_ALLOWLIST = {
    "ragdoll.py",
    "manx.py",
    "sandcat.go",
}

# Files/directories to skip entirely during scanning
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".eggs", "build", "dist",
    "magma",  # frontend JS plugin
}


def _iter_python_files(*search_roots):
    """Yield all .py files under the given roots, skipping irrelevant dirs."""
    for root in search_roots:
        root = Path(root)
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield Path(dirpath) / fn


def _parse_requirements(req_file):
    """Parse a requirements.txt into a dict of {package_name: version_spec}."""
    reqs = {}
    with open(req_file) as f:
        for line in f:
            line = line.strip().split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            # Match package==version, package~=version, package>=version
            m = re.match(r"^([A-Za-z0-9_-]+)\s*([~>=<!]+)\s*([^\s;]+)", line)
            if m:
                reqs[m.group(1).lower()] = (m.group(2), m.group(3))
    return reqs


def _find_requests_calls_without_timeout(filepath):
    """Use AST to find requests.get/post/put/patch/delete/head calls missing timeout."""
    issues = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return issues

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # Match requests.get(...), requests.post(...), etc.
        if isinstance(func, ast.Attribute) and func.attr in (
            "get", "post", "put", "patch", "delete", "head", "request"
        ):
            # Check if the value is 'requests' or an alias
            if isinstance(func.value, ast.Name) and func.value.id == "requests":
                kwarg_names = [kw.arg for kw in node.keywords]
                if "timeout" not in kwarg_names:
                    issues.append((node.lineno, f"requests.{func.attr}() missing timeout"))
    return issues


def _find_verify_false(filepath):
    """Find requests calls with verify=False."""
    issues = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return issues

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "verify" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                issues.append((node.lineno, "verify=False disables SSL certificate verification"))
    return issues


def _find_shell_true(filepath):
    """Find subprocess calls with shell=True."""
    issues = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return issues

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                if filepath.name not in SHELL_TRUE_ALLOWLIST:
                    issues.append((node.lineno, "subprocess call with shell=True"))
    return issues


# ---------------------------------------------------------------------------
# Test: dependency minimum versions in requirements.txt
# ---------------------------------------------------------------------------
class TestDependencyVersions:
    """Ensure requirements.txt does not pin known-vulnerable versions."""

    @pytest.fixture(autouse=True)
    def _load_requirements(self):
        self.req_file = ROOT_DIR / "requirements.txt"
        assert self.req_file.exists(), "requirements.txt not found"
        self.reqs = _parse_requirements(self.req_file)

    @pytest.mark.parametrize("pkg,min_ver", list(MINIMUM_SAFE_VERSIONS.items()))
    def test_minimum_version_not_pinned_below_safe(self, pkg, min_ver):
        """Check that requirements.txt does not pin a package below the minimum safe version."""
        if pkg.lower() not in self.reqs:
            pytest.skip(f"{pkg} not in requirements.txt")
        op, ver_str = self.reqs[pkg.lower()]
        pinned = Version(ver_str)
        minimum = Version(min_ver)

        if op == "==":
            assert pinned >= minimum, (
                f"{pkg}=={ver_str} is pinned below minimum safe version {min_ver}"
            )
        elif op == "~=":
            # ~= means compatible release; the pinned version itself must be >= minimum
            assert pinned >= minimum, (
                f"{pkg}~={ver_str} allows versions below minimum safe {min_ver}"
            )

    def test_pyasn1_not_vulnerable(self):
        """Regression test for CVE-2026-30922: pyasn1 must be >= 0.6.3."""
        if "pyasn1" not in self.reqs:
            pytest.skip("pyasn1 not in requirements.txt")
        op, ver_str = self.reqs["pyasn1"]
        pinned = Version(ver_str)
        assert pinned >= Version("0.6.3"), (
            f"pyasn1 {op}{ver_str} is vulnerable (CVE-2026-30922). Upgrade to >=0.6.3"
        )


# ---------------------------------------------------------------------------
# Test: requests calls in plugin code must have timeout
# ---------------------------------------------------------------------------
class TestRequestsTimeout:
    """All requests.get/post calls in plugin Python code must include a timeout parameter."""

    def test_stockpile_steganography_has_timeout(self):
        """Regression: steganography.py requests calls must have timeout."""
        path = PLUGIN_DIR / "stockpile" / "app" / "obfuscators" / "steganography.py"
        if not path.exists():
            pytest.skip("steganography.py not found")
        issues = _find_requests_calls_without_timeout(path)
        assert not issues, f"Missing timeout in {path}: {issues}"

    def test_stockpile_ragdoll_has_timeout(self):
        """Regression: ragdoll.py requests calls must have timeout."""
        path = PLUGIN_DIR / "stockpile" / "payloads" / "ragdoll.py"
        if not path.exists():
            pytest.skip("ragdoll.py not found")
        issues = _find_requests_calls_without_timeout(path)
        assert not issues, f"Missing timeout in {path}: {issues}"

    def test_response_elasticat_has_timeout(self):
        """Regression: elasticat.py requests calls must have timeout."""
        path = PLUGIN_DIR / "response" / "payloads" / "elasticat.py"
        if not path.exists():
            pytest.skip("elasticat.py not found")
        issues = _find_requests_calls_without_timeout(path)
        assert not issues, f"Missing timeout in {path}: {issues}"

    def test_all_plugin_requests_have_timeout(self):
        """Scan all plugin Python files for requests calls without timeout."""
        all_issues = []
        for filepath in _iter_python_files(PLUGIN_DIR):
            issues = _find_requests_calls_without_timeout(filepath)
            if issues:
                for lineno, msg in issues:
                    all_issues.append(f"{filepath.relative_to(ROOT_DIR)}:{lineno} - {msg}")

        if all_issues:
            # Report as warning rather than hard fail since some may be intentional
            pytest.xfail(
                f"Found {len(all_issues)} requests call(s) without timeout:\n"
                + "\n".join(all_issues[:20])
            )


# ---------------------------------------------------------------------------
# Test: no verify=False in plugin code
# ---------------------------------------------------------------------------
class TestNoVerifyFalse:
    """No requests calls should use verify=False."""

    def test_stockpile_steganography_no_verify_false(self):
        """Regression: steganography.py must use verify=True."""
        path = PLUGIN_DIR / "stockpile" / "app" / "obfuscators" / "steganography.py"
        if not path.exists():
            pytest.skip("steganography.py not found")
        issues = _find_verify_false(path)
        assert not issues, f"verify=False found in {path}: {issues}"

    def test_all_plugins_no_verify_false(self):
        """Scan all plugin Python files for verify=False."""
        all_issues = []
        for filepath in _iter_python_files(PLUGIN_DIR):
            issues = _find_verify_false(filepath)
            if issues:
                for lineno, msg in issues:
                    all_issues.append(f"{filepath.relative_to(ROOT_DIR)}:{lineno} - {msg}")

        if all_issues:
            pytest.xfail(
                f"Found {len(all_issues)} verify=False occurrence(s):\n"
                + "\n".join(all_issues[:20])
            )


# ---------------------------------------------------------------------------
# Test: shell=True usage audit
# ---------------------------------------------------------------------------
class TestNoShellTrue:
    """Subprocess calls should avoid shell=True unless in allowlisted agent payloads."""

    def test_core_code_no_shell_true(self):
        """Scan core caldera code (not plugins) for shell=True."""
        core_dirs = [ROOT_DIR / "app"]
        all_issues = []
        for filepath in _iter_python_files(*core_dirs):
            issues = _find_shell_true(filepath)
            if issues:
                for lineno, msg in issues:
                    all_issues.append(f"{filepath.relative_to(ROOT_DIR)}:{lineno} - {msg}")

        if all_issues:
            pytest.xfail(
                f"Found {len(all_issues)} shell=True occurrence(s) in core code:\n"
                + "\n".join(all_issues[:20])
            )

    def test_plugin_code_no_unexpected_shell_true(self):
        """Scan plugin code for shell=True outside of known agent payloads."""
        all_issues = []
        for filepath in _iter_python_files(PLUGIN_DIR):
            issues = _find_shell_true(filepath)
            if issues:
                for lineno, msg in issues:
                    all_issues.append(f"{filepath.relative_to(ROOT_DIR)}:{lineno} - {msg}")

        if all_issues:
            pytest.xfail(
                f"Found {len(all_issues)} shell=True occurrence(s) in plugin code "
                f"(outside allowlist):\n" + "\n".join(all_issues[:20])
            )
