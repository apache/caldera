import asyncio
import base64
import binascii
import copy
import json
import os
import re
import subprocess
import sys

from aiohttp import web
from multidict import CIMultiDict
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.service.interfaces.i_file_svc import FileServiceInterface
from app.utility.base_service import BaseService
from app.utility.payload_encoder import xor_file, xor_bytes

_SAFE_FILENAME_RE = re.compile(r'^[a-zA-Z0-9._\-]+$')
FILE_ENCRYPTION_FLAG = '%encrypted%'
URL_SANITIZATION_REGEX = re.compile(r'^[\w\-\.:%+/]+$')
ALLOWED_DEFAULT_LDFLAG_REGEX = re.compile(r'^[\w\-\.]+$')
ALLOWED_LDFLAG_REGEXES = {
    'server': URL_SANITIZATION_REGEX,
    'http': URL_SANITIZATION_REGEX,
    'socket': re.compile(r'^[\w\-\.:]+$')
}
USER_PAYLOAD_ENCRYPTION_FLAG = bytes('%userencryptedpayload%', encoding='utf-8')


class FileSvc(FileServiceInterface, BaseService):

    def __init__(self):
        self.log = self.add_service('file_svc', self)
        self.data_svc = self.get_service('data_svc')
        self.special_payloads = dict()
        self.encryptor = self._get_encryptor()
        self.encrypt_output = False if self.get_config('encrypt_files') is False else True
        self.packers = dict()

    async def get_file(self, headers):
        headers = CIMultiDict(headers)
        if 'file' not in headers:
            raise KeyError('File key was not provided')

        packer = None
        display_name = payload = headers.get('file')
        if ':' in payload:
            _, display_name = packer, payload = payload.split(':')
            headers['file'] = payload
        if any(payload.endswith(x) for x in [y for y in self.special_payloads if y.startswith('.')]):
            payload, display_name = await self._operate_extension(payload, headers)
        if self.is_uuid4(payload):
            payload, display_name = self.get_payload_name_from_uuid(payload)
        if payload in self.special_payloads:
            payload, display_name = await self.special_payloads[payload](headers)
        file_path, contents = await self.read_file(payload)
        if packer:
            if packer in self.packers:
                file_path, contents = await self.get_payload_packer(packer).pack(file_path, contents)
            else:
                self.log.warning('packer not available for payload, returning unpacked')
        if headers.get('xor_key'):
            xor_key = headers['xor_key']
            contents = xor_bytes(contents, xor_key.encode())
        if headers.get('name'):
            display_name = headers.get('name')
        display_name = self.remove_xored_extension(file_path)
        contents = await self._perform_data_encoding(headers, contents)
        return file_path, contents, display_name

    async def save_file(self, filename, payload, target_dir, encrypt=True, encoding=None):
        if encoding:
            payload = await self._decode_contents(payload, encoding)
        full_path = os.path.join(target_dir, filename)
        # Containment check — the resolved final path must not escape its parent.
        # save_file has two legitimate call modes:
        #   (1) target_dir + basename — sink mode, used by agent contact handlers
        #       (DNS, FTP, Gist, Slack) that take filename from upload metadata.
        #   (2) ''  + relative path  — path mode, used by ability/source managers
        #       that assemble paths from sanitised IDs.
        # Either mode is broken by a filename containing '..'. Resolving the final
        # path and checking it stays under the parent rejects both classes with one
        # check, without needing callers to know which mode they are using.
        parent = os.path.realpath(target_dir) if target_dir else os.path.realpath(os.getcwd())
        final = os.path.realpath(full_path)
        if final != parent and not final.startswith(parent + os.sep):
            raise ValueError('save_file: path %r escapes parent %r (filename=%r, target_dir=%r)'
                             % (final, parent, filename, target_dir))
        self._save(full_path, payload, encrypt)

    async def create_exfil_sub_directory(self, dir_name):
        path = os.path.join(self.get_config('exfil_dir'), dir_name)
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    async def create_exfil_operation_directory(self, dir_name, agent_name):
        op_list = self.data_svc.ram['operations']
        op_list_filtered = [x for x in op_list if x.state not in x.get_finished_states()]
        special_chars = {ord(c): '_' for c in r':<>"/\|?*'}
        agent_opid = [(x.name.translate(special_chars), '_', x.start.strftime("%Y-%m-%d_%H%M%SZ"))
                      for x in op_list_filtered if agent_name in [y.paw for y in x.agents]]
        path = os.path.join((dir_name), ''.join(agent_opid[0]))
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    @staticmethod
    def _validate_filename(name):
        """Validate that a filename contains only safe characters.
        Returns True if valid, False otherwise.
        """
        if not name or len(name) > 255:
            return False
        if '\x00' in name:
            return False
        if name == '.' or '..' in name or '/' in name or '\\' in name:
            return False
        if not _SAFE_FILENAME_RE.fullmatch(name):
            return False
        return True

    async def save_multipart_file_upload(self, request, target_dir, encrypt=True):
        try:
            reader = await request.multipart()
            headers = CIMultiDict(request.headers)
            while True:
                field = await reader.next()
                if not field:
                    break
                if not self._validate_filename(field.filename):
                    self.log.warning('Invalid filename rejected: %r', field.filename)
                    raise web.HTTPBadRequest(reason='Invalid filename: disallowed characters or path traversal')
                _, filename = os.path.split(field.filename)
                await self.save_file(filename, bytes(await field.read()), target_dir,
                                     encrypt=encrypt, encoding=headers.get('x-file-encoding'))
                self.log.debug('Uploaded file %s/%s' % (target_dir, filename))
            return web.Response()
        except web.HTTPException:
            raise
        except Exception as e:
            self.log.debug('Exception uploading file: %s' % e)

    async def find_file_path(self, name, location=''):
        for plugin in await self.data_svc.locate('plugins', match=dict(enabled=True)):
            for subd in ['', 'data']:
                file_path = await self.walk_file_path(os.path.join('plugins', plugin.name, subd, location), name)
                if file_path:
                    return plugin.name, file_path
        file_path = await self.walk_file_path(os.path.join('data', location), name)
        if file_path:
            return None, file_path
        return None, await self.walk_file_path('%s' % location, name)

    async def read_file(self, name, location='payloads'):
        _, file_name = await self.find_file_path(name, location=location)
        if file_name:
            if self.is_extension_xored(file_name):
                return name, xor_file(file_name)
            return name, self._read(file_name)
        raise FileNotFoundError

    def read_result_file(self, link_id, location='data/results'):
        buf = self._read(os.path.join(location, link_id))
        decoded_buf = buf.decode('utf-8')
        try:
            json.loads(self.decode_bytes(decoded_buf))
            return decoded_buf
        except json.JSONDecodeError:
            results = json.dumps(dict(
                stdout=self.decode_bytes(decoded_buf, strip_newlines=False), stderr='', exit_code=''))
            return self.encode_string(str(results))
        except binascii.Error:
            results = json.dumps(dict(stdout=decoded_buf, stderr='', exit_code=''))
            return self.encode_string(str(results))

    def write_result_file(self, link_id, output, location='data/results'):
        output = bytes(output, encoding='utf-8')
        self._save(os.path.join(location, link_id), output)

    async def add_special_payload(self, name, func):
        """
        Call a special function when specific payloads are downloaded

        :param name:
        :param func:
        :return:
        """
        if callable(func):  # Check to see if the passed function is already a callable function
            self.special_payloads[name] = func

    async def compile_go(self, platform, output, src_fle, arch='amd64', ldflags='-s -w', cflags='', buildmode='',
                         build_dir='.', loop=None):
        env = copy.copy(os.environ)
        env['GOARCH'] = arch
        env['GOOS'] = platform
        if cflags:
            for cflag in cflags.split(' '):
                name, value = cflag.split('=')
                env[name] = value

        args = ['go', 'build']
        if buildmode:
            args.append(buildmode)
        if ldflags:
            args.extend(['-ldflags', "{}".format(ldflags)])

        args.extend(['-o', output, src_fle])

        loop = loop if loop else asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: subprocess.check_output(args, cwd=build_dir, env=env))
        except subprocess.CalledProcessError as e:
            self.log.warning('Problem building golang executable {}: {} '.format(src_fle, e))

    @staticmethod
    def sanitize_ldflag_value(param, value):
        """
        Validate that the specified LDFLAG value for the given parameter
        only contains safe characters.
        Raises a ValueError if disallowed characters are found.
        """
        if not ALLOWED_LDFLAG_REGEXES.get(param, ALLOWED_DEFAULT_LDFLAG_REGEX).fullmatch(value):
            raise ValueError('Invalid characters in %s LDFLAG value: %s' % (param, value))
        return value

    def get_payload_name_from_uuid(self, payload):
        for t in ['standard_payloads', 'special_payloads']:
            for k, v in self.get_config(prop=t, name='payloads').items():
                if v['id'] == payload:
                    if v.get('obfuscation_name'):
                        return k, v['obfuscation_name'][0]
                    return k, k
        return payload, payload

    def get_payload_packer(self, packer):
        return self.packers[packer].Packer(self)

    def list_exfilled_files(self, startdir=None):
        if not startdir:
            startdir = self.get_config('exfil_dir')
        if not os.path.exists(startdir):
            return dict()
        exfil_files = dict()
        exfil_list = [x for x in os.walk(startdir) if x[2]]
        for d in exfil_list:
            agent_path = d[0]
            exfil_agent_key = d[0].split(os.sep)[-2]
            exfil_subdir = d[0].split(os.sep)[-1]
            if exfil_agent_key not in exfil_files:
                exfil_files[exfil_agent_key] = dict()
            for file in d[-1]:
                if exfil_subdir not in exfil_files[exfil_agent_key]:
                    exfil_files[exfil_agent_key][exfil_subdir] = dict()
                if file not in exfil_files[exfil_agent_key][exfil_subdir]:
                    exfil_files[exfil_agent_key][exfil_subdir][file] = os.path.join(agent_path, file)
        return exfil_files

    @staticmethod
    async def walk_file_path(path, target):
        for root, _, files in os.walk(path):
            if target in files:
                return os.path.join(root, target)
            xored_target = FileSvc.add_xored_extension(target)
            if xored_target in files:
                return os.path.join(root, xored_target)
        return None

    @staticmethod
    def remove_xored_extension(filename):
        if FileSvc.is_extension_xored(filename):
            return filename.replace('.xored', '')
        return filename

    @staticmethod
    def is_extension_xored(filename):
        return filename.endswith('.xored')

    @staticmethod
    def add_xored_extension(filename):
        if FileSvc.is_extension_xored(filename):
            return filename
        return '%s.xored' % filename

    def _save(self, filename, content, encrypt=True):
        if encrypt and (self.encryptor and self.encrypt_output):
            content = bytes(FILE_ENCRYPTION_FLAG, 'utf-8') + self.encryptor.encrypt(content)
        with open(filename, 'wb') as f:
            f.write(content)

    def _read(self, filename):
        with open(filename, 'rb') as f:
            buf = f.read()
        if buf.startswith(USER_PAYLOAD_ENCRYPTION_FLAG):
            # Handle encrypted user-uploaded payloads
            buf = buf[len(USER_PAYLOAD_ENCRYPTION_FLAG):]
            key = buf[0:32]
            iv = buf[32:48]
            ciphertext = buf[48:]
            cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
            decryptor = cipher.decryptor()
            buf = decryptor.update(ciphertext) + decryptor.finalize()
        elif self.encryptor and buf.startswith(bytes(FILE_ENCRYPTION_FLAG, encoding='utf-8')):
            try:
                buf = self.encryptor.decrypt(buf[len(FILE_ENCRYPTION_FLAG):])
            except InvalidToken:
                self.log.error('Failed to decrypt saved Caldera state due to incorrect encryption key.\n'
                               ' - If attempting to restore secure backup, verify that conf/local.yml exists with '
                               'correct encryption_key value, and that the server is being run without --insecure.\n'
                               ' - If attempting to restore insecure backup, verify that conf/default.yml exists '
                               'with correct encryption_key value, and that the server is being run with --insecure.\n'
                               ' - If correct encryption_key value cannot be recovered, rerun the server with --fresh '
                               'to disregard stored server state.')
                sys.exit(1)
        return buf

    def _get_encryptor(self):
        generated_key = PBKDF2HMAC(algorithm=hashes.SHA256(),
                                   length=32,
                                   salt=bytes(self.get_config('crypt_salt'), 'utf-8'),
                                   iterations=2 ** 20,
                                   backend=default_backend())
        return Fernet(base64.urlsafe_b64encode(generated_key.derive(bytes(self.get_config('encryption_key'), 'utf-8'))))

    async def _operate_extension(self, payload, headers):
        try:
            target = '.' + payload.split('.')[-1]
            return await self.special_payloads[target](self.get_services(), headers)
        except Exception as e:
            self.log.error('Error loading extension handler=%s, %s' % (payload, e))

    async def _perform_data_encoding(self, headers, contents):
        requested_encoding = headers.get('x-file-encoding')
        if requested_encoding:
            return await self._encode_contents(contents, requested_encoding)
        return contents

    async def _get_encoder_by_name(self, encoding):
        if encoding:
            encoders = await self.data_svc.locate('data_encoders', match=dict(name=encoding))
            if encoders:
                return encoders[0]
        self.log.error('Could not find the requested data encoder %s' % encoding)

    async def _encode_contents(self, contents, encoder_name):
        self.log.debug('Encoding file contents using %s encoding' % encoder_name)
        encoder = await self._get_encoder_by_name(encoder_name)
        if encoder:
            return encoder.encode(contents)
        else:
            self.log.error('Failed to encode contents. Returning original contents')
            return contents

    async def _decode_contents(self, contents, encoder_name):
        self.log.debug('Decoding file contents using %s encoding' % encoder_name)
        encoder = await self._get_encoder_by_name(encoder_name)
        if encoder:
            return encoder.decode(contents)
        else:
            self.log.error('Failed to decode contents. Returning original contents')
            return contents


def _go_vars(arch, platform):
    return '%s GOARCH=%s %s GOOS=%s' % (_get_header(), arch, _get_header(), platform)


def _get_header():
    return 'SET' if os.name == 'nt' else ''
