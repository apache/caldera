import subprocess
import sys

from pathlib import Path

from app.utility.payload_encoder import DEFAULT_KEY, xor_bytes


ENCODER = Path(__file__).parents[2] / 'app' / 'utility' / 'payload_encoder.py'


def _run_encoder(args):
    return subprocess.run([sys.executable, str(ENCODER)] + args, capture_output=True, text=True)


def test_xor_bytes_accepts_a_byte_string_key():
    assert xor_bytes(b'\x00\x01', key=b'\x0f\x0f') == b'\x0f\x0e'


def test_command_line_uses_the_default_key(tmp_path):
    payload = tmp_path / 'payload'
    payload.write_bytes(b'hello payload')
    encoded = tmp_path / 'payload.xored'

    result = _run_encoder([str(payload), str(encoded)])

    assert result.returncode == 0, result.stderr
    assert encoded.read_bytes() == xor_bytes(b'hello payload', key=DEFAULT_KEY)


def test_command_line_key_argument_is_usable(tmp_path):
    payload = tmp_path / 'payload'
    payload.write_bytes(b'hello payload')
    encoded = tmp_path / 'payload.xored'

    result = _run_encoder(['-key', 'somekey', str(payload), str(encoded)])

    assert result.returncode == 0, result.stderr
    assert encoded.read_bytes() == xor_bytes(b'hello payload', key=b'somekey')
