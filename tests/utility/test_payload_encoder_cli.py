import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from app.utility import payload_encoder
from app.utility.payload_encoder import DEFAULT_KEY, parse_key, xor_bytes

ENCODER_SCRIPT = Path(payload_encoder.__file__)


class TestParseKey:

    def test_parses_hex_string(self):
        assert parse_key('324532ca') == DEFAULT_KEY

    def test_rejects_non_hex(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_key('not-hex')

    def test_rejects_empty(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_key('')


class TestCommandLine:

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ENCODER_SCRIPT), *args],
                              capture_output=True, text=True)

    def test_default_key(self, tmp_path):
        src, dst = tmp_path / 'in', tmp_path / 'out'
        src.write_bytes(b'payload')
        assert self.run_cli(str(src), str(dst)).returncode == 0
        assert dst.read_bytes() == xor_bytes(b'payload')

    def test_custom_hex_key(self, tmp_path):
        src, dst = tmp_path / 'in', tmp_path / 'out'
        src.write_bytes(b'\x01\x02')
        assert self.run_cli('-key', 'ff', str(src), str(dst)).returncode == 0
        assert dst.read_bytes() == b'\xfe\xfd'

    def test_invalid_key_is_rejected_cleanly(self, tmp_path):
        src, dst = tmp_path / 'in', tmp_path / 'out'
        src.write_bytes(b'x')
        result = self.run_cli('-key', 'zz', str(src), str(dst))
        assert result.returncode != 0
        assert 'hex' in result.stderr
