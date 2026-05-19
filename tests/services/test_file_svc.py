import base64
import json
import os
import pytest
import yaml

from aiohttp import web
from base64 import b64encode
from asyncio import Future
from unittest.mock import AsyncMock, MagicMock

from app.data_encoders.base64_basic import Base64Encoder
from app.data_encoders.plain_text import PlainTextEncoder
from app.utility.file_decryptor import decrypt


@pytest.fixture
def store_encoders(event_loop, data_svc):
    event_loop.run_until_complete(data_svc.store(PlainTextEncoder()))
    event_loop.run_until_complete(data_svc.store(Base64Encoder()))


@pytest.mark.usefixtures(
    'init_base_world',
    'store_encoders'
)
class TestFileService:

    @pytest.fixture
    def text_file(self, tmpdir):
        txt_str = 'Hello world!'
        f = tmpdir.mkdir('txt').join('test.txt')
        f.write(txt_str)
        assert f.read() == txt_str
        yield f

    def test_save_file(self, event_loop, file_svc, tmp_path):
        filename = "test_file.txt"
        payload = b'These are the file contents.'
        # Save temporary test file
        event_loop.run_until_complete(file_svc.save_file(filename, payload, tmp_path, encrypt=False))
        file_location = tmp_path / filename
        # Read file contents from saved file
        assert os.path.isfile(file_location)
        with open(file_location, "r") as file_contents:
            assert payload.decode("utf-8") == file_contents.read()

    def test_save_file_rejects_path_traversal(self, event_loop, file_svc, tmp_path):
        # save_file is reachable from the agent contact handlers (DNS, FTP, Gist,
        # Slack) with an attacker-controlled filename. A '../../...' basename
        # must NOT be allowed to escape target_dir, regardless of the encryption
        # setting. Regression test for the unauthenticated file-write primitive
        # that ended in pickle.loads on the next restart.
        payload = b'attacker bytes'
        # Avoid filesystem-resident paths like /etc/passwd as targets — the
        # post-condition guard would false-positive on any pre-existing system
        # file. Use names that cannot pre-exist by accident.
        traversal_attempts = [
            '../caldera-traversal-canary-1.bin',
            '../../caldera-traversal-canary-2.bin',
            '../../../../tmp/caldera-traversal-canary-3.bin',
        ]
        for evil in traversal_attempts:
            with pytest.raises(ValueError, match='escapes parent'):
                event_loop.run_until_complete(
                    file_svc.save_file(evil, payload, str(tmp_path), encrypt=False)
                )
            # Defense-in-depth: confirm the canary file the test name reserves
            # wasn't actually written (proves save_file rejected BEFORE the I/O,
            # not just that it raised after writing).
            resolved = os.path.realpath(os.path.join(str(tmp_path), evil))
            assert not os.path.exists(resolved), 'save_file wrote to %s before raising' % resolved

    def test_create_exfil_sub_directory(self, event_loop, file_svc):
        exfil_dir_name = 'unit-testing-Rocks'
        new_dir = event_loop.run_until_complete(file_svc.create_exfil_sub_directory(exfil_dir_name))
        assert os.path.isdir(new_dir)
        os.rmdir(new_dir)

    def test_read_write_result_file(self, tmpdir, file_svc):
        link_id = '12345'
        output = 'output testing unit'
        error = 'error testing unit'
        test_exit_code = '0'
        output_encoded = str(b64encode(json.dumps(dict(stdout=output, stderr=error, exit_code=test_exit_code)).encode()), 'utf-8')
        file_svc.write_result_file(link_id=link_id, output=output_encoded, location=tmpdir)

        expected_output = dict(stdout=output, stderr=error, exit_code=test_exit_code)
        output_data = file_svc.read_result_file(link_id=link_id, location=tmpdir)
        decoded_output_data = json.loads(base64.b64decode(output_data))
        assert decoded_output_data == expected_output

    def test_read_write_result_file_no_dict(self, tmpdir, file_svc):
        link_id = '12345'
        output = 'output testing unit'
        output_encoded = str(b64encode(output.encode()), 'utf-8')
        file_svc.write_result_file(link_id=link_id, output=output_encoded, location=tmpdir)

        expected_output = {'stdout': output, 'stderr': '', 'exit_code': ''}
        output_data = file_svc.read_result_file(link_id=link_id, location=tmpdir)
        decoded_output_data = json.loads(base64.b64decode(output_data))
        assert decoded_output_data == expected_output

    def test_read_write_result_file_no_base64(self, tmpdir, file_svc):
        link_id = '12345'
        output = 'output testing unit'
        file_svc.write_result_file(link_id=link_id, output=output, location=tmpdir)

        expected_output = {'stdout': output, 'stderr': '', 'exit_code': ''}
        output_data = file_svc.read_result_file(link_id=link_id, location=tmpdir)
        decoded_output_data = json.loads(base64.b64decode(output_data))
        assert decoded_output_data == expected_output

    def test_upload_decode_plaintext(self, event_loop, file_svc, data_svc):
        content = b'this will be encoded and decoded as plaintext'
        self._test_upload_file_with_encoding(event_loop, file_svc, data_svc, encoding='plain-text', upload_content=content,
                                             decoded_content=content)

    def test_upload_decode_b64(self, event_loop, file_svc, data_svc):
        original_content = b'this will be encoded and decoded as base64'
        upload_content = b64encode(original_content)
        self._test_upload_file_with_encoding(event_loop, file_svc, data_svc, encoding='base64', upload_content=upload_content,
                                             decoded_content=original_content)

    def test_download_plaintext_file(self, event_loop, file_svc, data_svc):
        payload_content = b'plaintext content'
        self._test_download_file_with_encoding(event_loop, file_svc, data_svc, encoding='plain-text',
                                               original_content=payload_content, encoded_content=payload_content)

    def test_download_base64_file(self, event_loop, file_svc, data_svc):
        payload_content = b'b64 content'
        self._test_download_file_with_encoding(event_loop, file_svc, data_svc, encoding='base64',
                                               original_content=payload_content,
                                               encoded_content=b64encode(payload_content))

    def test_pack_file(self, event_loop, mocker, tmpdir, file_svc, data_svc):
        payload = 'unittestpayload'
        payload_content = b'content'
        new_payload_content = b'new_content'
        packer_name = 'test'

        # create temp files
        file = tmpdir.join(payload)
        file.write(payload_content)

        # start mocking up methods
        packer = mocker.Mock(return_value=Future())
        packer.return_value = packer
        packer.pack = AsyncMock(return_value=(payload, new_payload_content))
        data_svc.locate = AsyncMock(return_value=[])
        module = mocker.Mock()
        module.Packer = packer
        file_svc.packers[packer_name] = module
        file_svc.data_svc = data_svc
        file_svc.read_file = AsyncMock(return_value=(payload, payload_content))

        file_path, content, display_name = event_loop.run_until_complete(file_svc.get_file(headers=dict(file='%s:%s' % (packer_name, payload))))

        packer.pack.assert_called_once()
        assert payload == file_path
        assert content == new_payload_content

    def test_xored_filename_removal(self, event_loop, mocker, tmpdir, file_svc, data_svc):
        payload = 'unittestpayload.exe.xored'
        payload_content = b'content'
        new_payload_content = b'new_content'
        packer_name = 'test_xored_filename_removal'
        expected_display_name = 'unittestpayload.exe'

        # create temp files
        file = tmpdir.join(payload)
        file.write(payload_content)

        # start mocking up methods
        packer = mocker.Mock(return_value=Future())
        packer.return_value = packer
        packer.pack = AsyncMock(return_value=(payload, new_payload_content))
        data_svc.locate = AsyncMock(return_value=[])
        module = mocker.Mock()
        module.Packer = packer
        file_svc.packers[packer_name] = module
        file_svc.data_svc = data_svc
        file_svc.read_file = AsyncMock(return_value=(payload, payload_content))

        file_path, content, display_name = event_loop.run_until_complete(file_svc.get_file(headers=dict(file='%s:%s' % (packer_name, payload))))

        packer.pack.assert_called_once()
        assert payload == file_path
        assert content == new_payload_content
        assert display_name == expected_display_name

    def test_upload_file(self, event_loop, file_svc):
        upload_dir = event_loop.run_until_complete(file_svc.create_exfil_sub_directory('test-upload'))
        upload_filename = 'uploadedfile.txt'
        upload_content = b'this is a test upload file'
        event_loop.run_until_complete(file_svc.save_file(upload_filename, upload_content, upload_dir, encrypt=False))
        uploaded_file_path = os.path.join(upload_dir, upload_filename)
        assert os.path.isfile(uploaded_file_path)
        with open(uploaded_file_path, 'rb') as file:
            written_data = file.read()
        assert written_data == upload_content
        os.remove(uploaded_file_path)
        os.rmdir(upload_dir)

    def test_multipart_upload_rejects_invalid_filename(self, event_loop, file_svc, tmp_path):
        """Regression test for #3267: save_multipart_file_upload must raise HTTPBadRequest
        when a field filename fails _validate_filename (e.g. path traversal attempt '../evil.txt').
        """
        # Build a fake multipart field with a path-traversal filename
        fake_field = MagicMock()
        fake_field.filename = '../evil.txt'
        fake_field.read = AsyncMock(return_value=b'evil content')

        # Build a fake multipart reader whose next() returns the bad field then None
        fake_reader = MagicMock()
        fake_reader.next = AsyncMock(side_effect=[fake_field, None])

        # Build a fake request whose multipart() returns the reader
        fake_request = MagicMock()
        fake_request.multipart = AsyncMock(return_value=fake_reader)
        fake_request.headers = {}

        with pytest.raises(web.HTTPBadRequest):
            event_loop.run_until_complete(
                file_svc.save_multipart_file_upload(fake_request, str(tmp_path))
            )

    def test_encrypt_upload(self, event_loop, file_svc):
        upload_dir = event_loop.run_until_complete(file_svc.create_exfil_sub_directory('test-encrypted-upload'))
        upload_filename = 'encryptedupload.txt'
        upload_content = b'this is a test upload file'
        event_loop.run_until_complete(file_svc.save_file(upload_filename, upload_content, upload_dir))
        uploaded_file_path = os.path.join(upload_dir, upload_filename)
        decrypted_file_path = upload_filename + '_decrypted'
        config_to_use = 'conf/default.yml'
        with open(config_to_use, encoding='utf-8') as conf:
            config = list(yaml.load_all(conf, Loader=yaml.FullLoader))[0]
        decrypt(uploaded_file_path, config, output_file=decrypted_file_path)
        assert os.path.isfile(decrypted_file_path)
        with open(decrypted_file_path, 'rb') as decrypted_file:
            decrypted_data = decrypted_file.read()
        assert decrypted_data == upload_content
        os.remove(uploaded_file_path)
        os.remove(decrypted_file_path)
        os.rmdir(upload_dir)

    def test_walk_file_path_exists_nonxor(self, event_loop, text_file, file_svc):
        ret = event_loop.run_until_complete(file_svc.walk_file_path(text_file.dirname, text_file.basename))
        assert ret == text_file

    def test_walk_file_path_notexists(self, event_loop, text_file, file_svc):
        ret = event_loop.run_until_complete(file_svc.walk_file_path(text_file.dirname, 'not-a-real.file'))
        assert ret is None

    def test_walk_file_path_xor_fn(self, event_loop, tmpdir, file_svc):
        f = tmpdir.mkdir('txt').join('xorfile.txt.xored')
        f.write("test")
        ret = event_loop.run_until_complete(file_svc.walk_file_path(f.dirname, 'xorfile.txt'))
        assert ret == f

    def test_remove_xored_extension(self, file_svc):
        test_value = 'example_file.exe.xored'
        expected_value = 'example_file.exe'
        ret = file_svc.remove_xored_extension(test_value)
        assert ret == expected_value

    def test_remove_xored_extension_to_non_xored_file(self, file_svc):
        test_value = 'example_file.exe'
        expected_value = 'example_file.exe'
        ret = file_svc.remove_xored_extension(test_value)
        assert ret == expected_value

    def test_add_xored_extension(self, file_svc):
        test_value = 'example_file.exe'
        expected_value = 'example_file.exe.xored'
        ret = file_svc.add_xored_extension(test_value)
        assert ret == expected_value

    def test_add_xored_extension_to_xored_file(self, file_svc):
        test_value = 'example_file.exe.xored'
        expected_value = 'example_file.exe.xored'
        ret = file_svc.add_xored_extension(test_value)
        assert ret == expected_value

    def test_is_extension_xored_true(self, file_svc):
        test_value = 'example_file.exe.xored'
        ret = file_svc.is_extension_xored(test_value)
        assert ret is True

    def test_is_extension_xored_false(self, file_svc):
        test_value = 'example_file.exe'
        ret = file_svc.is_extension_xored(test_value)
        assert ret is False

    def test_sanitize_ldflag_value(self, file_svc):
        safe_values = [
            'safevalue',
            'SAFE29VALUE',
            '_safe_',
            's-a-f-e.s_a_f_e.2',
            '1234567890'
        ]
        for value in safe_values:
            assert value == file_svc.sanitize_ldflag_value('contact', value)
            assert value == file_svc.sanitize_ldflag_value('group', value)
            assert value == file_svc.sanitize_ldflag_value('genericparam', value)

        safe_server_values = [
            'http://localhost',
            'https://localhost:8443',
            'https://127.0.0.1:8443/home.html',
            'https://some.domain.net:8443/home%20test.html',
            'https://_underscore.domain-with-dash.net:8443/home+test.html',
        ]
        for value in safe_server_values:
            assert value == file_svc.sanitize_ldflag_value('server', value)
            assert value == file_svc.sanitize_ldflag_value('http', value)

        safe_socket_values = [
            'localhost:1234',
            '10.10.10.10.:8888',
            'f.q.d.n:443',
            'domain-with-dash.net:443',
        ]
        for value in safe_socket_values:
            assert value == file_svc.sanitize_ldflag_value('socket', value)

        unsafe_values = [
            'unsafe with spaces',
            'unsafe,comma',
            'unsafe;semicolon',
            'unsafe!',
            'unsafe&&test',
            'unsafe||test',
            'unsafe>test',
            'unsafe<test',
            'unsafe$(test)',
            'unsafe~/test',
            'unsafe%test+',
        ]
        for value in unsafe_values:
            with pytest.raises(Exception) as e_info:
                file_svc.sanitize_ldflag_value('group', value)
            assert str(e_info.value) == 'Invalid characters in group LDFLAG value: {}'.format(value)

        unsafe_server_values = [
            'http://localhost||test',
            'https://localhost:8443 space',
            'https://localhost:8443@',
            'https://localhost:8443"test',
            'https://localhost:8443\'test',
            'https://127.0.0.1:8443/home.html$(test)',
            'https://some.domain.net:8443/home%20test.html && test',
            'https://_underscore.domain-with-dash.net:8443/home+test.html; test',
        ]
        for value in unsafe_server_values:
            with pytest.raises(Exception) as e_info:
                file_svc.sanitize_ldflag_value('server', value)
            assert str(e_info.value) == 'Invalid characters in server LDFLAG value: {}'.format(value)

            with pytest.raises(Exception) as e_info:
                file_svc.sanitize_ldflag_value('http', value)
            assert str(e_info.value) == 'Invalid characters in http LDFLAG value: {}'.format(value)

        unsafe_socket_values = [
            'localhost:8888||test',
            '127.0.0.1:8443 space',
            'domain.com:8443@',
            'localhost:8443"test',
            'localhost:8443\'test',
            '127.0.0.1:8443$(test)',
            'some.domain.net:8443 && test',
            'domain-with-dash.net:8443; test',
        ]
        for value in unsafe_socket_values:
            with pytest.raises(Exception) as e_info:
                file_svc.sanitize_ldflag_value('socket', value)
            assert str(e_info.value) == 'Invalid characters in socket LDFLAG value: {}'.format(value)

    @staticmethod
    def _test_download_file_with_encoding(event_loop, file_svc, data_svc, encoding, original_content, encoded_content):
        filename = 'testencodedpayload.txt'
        file_svc.read_file = AsyncMock(return_value=(filename, original_content))
        file_svc.data_svc = data_svc
        file_path, content, display_name = event_loop.run_until_complete(
            file_svc.get_file(headers={'file': filename, 'x-file-encoding': encoding})
        )
        assert file_path == filename
        assert content == encoded_content
        assert display_name == filename

    @staticmethod
    def _test_upload_file_with_encoding(event_loop, file_svc, data_svc, encoding, upload_content, decoded_content):
        file_svc.data_svc = data_svc
        upload_dir = event_loop.run_until_complete(file_svc.create_exfil_sub_directory('testencodeduploaddir'))
        upload_filename = 'testencodedupload.txt'
        event_loop.run_until_complete(file_svc.save_file(upload_filename, upload_content, upload_dir, encrypt=False,
                                      encoding=encoding))
        uploaded_file_path = os.path.join(upload_dir, upload_filename)
        assert os.path.isfile(uploaded_file_path)
        with open(uploaded_file_path, 'rb') as file:
            written_data = file.read()
        assert written_data == decoded_content
        os.remove(uploaded_file_path)
        os.rmdir(upload_dir)
