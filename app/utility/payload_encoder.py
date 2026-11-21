"""
This module contains helper functions for encoding and decoding payload files.

If AV is running on the server host, then it may sometimes flag, quarantine, or delete
Caldera payloads. To help prevent this, encoded payloads can be used to prevent AV
from breaking the server. The convention expected by the server is that
encoded payloads will be XOR'ed with the DEFAULT_KEY contained in the payload_encoder.py
module.

Additionally, payload_encoder.py can be used from the command-line to add a new encoded payload.

```
python /path/to/payload_encoder.py [-key KEY] input_file output_file
```

NOTE: In order for the server to detect the availability of an encoded payload, the payload file's
name must end in the `.xored` extension.
"""
import array
import argparse

DEFAULT_KEY = [0x32, 0x45, 0x32, 0xca]


def xor_bytes(in_bytes, key=None):
    if not key:
        key = DEFAULT_KEY
    arr = array.array('B', in_bytes)
    for i, val in enumerate(arr):
        cur_key = key[i % len(key)]
        arr[i] = val ^ cur_key
    return bytes(arr)


def xor_file(input_file, output_file=None, key=None):
    with open(input_file, 'rb') as encoded_stream:
        buf = encoded_stream.read()
    buf = xor_bytes(buf, key=key)
    if output_file:
        with open(output_file, 'wb') as decoded_stream:
            decoded_stream.write(bytes(buf))
    return buf


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # A str indexes to str, which cannot be XOR'ed with an int. The server path in
    # file_svc encodes its key the same way before calling xor_bytes.
    parser.add_argument('-key', default=DEFAULT_KEY, type=lambda key: key.encode())
    parser.add_argument('input')
    parser.add_argument('output')

    args = parser.parse_args()
    xor_file(args.input, output_file=args.output, key=args.key)
