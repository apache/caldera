from unittest import mock

from app.utility.payload_encoder import xor_bytes, xor_file


input = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef])


class TestPayloadEncoder:
    def test_xor_bytes(self):
        # Default key
        want = bytes([0x33, 0x66, 0x77, 0xad, 0xbb, 0xee, 0xff, 0x25])
        assert xor_bytes(input) == want
        assert xor_bytes(xor_bytes(input)) == input

        # Specific key
        key = [0x65, 0x43, 0x21]
        want = bytes([0x64, 0x60, 0x64, 0x02, 0xca, 0x8a, 0xa8, 0xac])
        assert xor_bytes(input, key) == want
        assert xor_bytes(xor_bytes(input)) == input

    def test_xor_file(self):
        # Without output file, use default key
        want = bytes([0x33, 0x66, 0x77, 0xad, 0xbb, 0xee, 0xff, 0x25])
        m = mock.mock_open(read_data=input)
        with mock.patch('builtins.open', m):
            assert want == xor_file('test_file')
            m.assert_called_once_with('test_file', 'rb')
            m().write.assert_not_called()

        # With output file, specific key
        key = [0x65, 0x43, 0x21]
        want = bytes([0x64, 0x60, 0x64, 0x02, 0xca, 0x8a, 0xa8, 0xac])
        m = mock.mock_open(read_data=input)
        with mock.patch('builtins.open', m):
            assert want == xor_file('test_file', 'test_output', key)
            m.assert_any_call('test_file', 'rb')
            m.assert_called_with('test_output', 'wb')
            m().write.assert_called_once_with(want)
