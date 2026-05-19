import json
import os
import re
import asyncio
import aioftp

from app.utility.base_world import BaseWorld

MAX_CONNECTIONS = 256
MAX_ANON_CONNECTIONS = 5
SPEED_LIMIT = 1024 * 1024
SPEED_LIMIT_PER_CONN = 100 * 1024
ANON_HOME_PATH = '/anon'
FTP_HOST_PROPERTY = 'app.contact.ftp.host'
FTP_HOST_DEFAULT = '0.0.0.0'  # nosec
FTP_PORT_PROPERTY = 'app.contact.ftp.port'
FTP_PORT_DEFAULT = 2222
FTP_DIRECTORY_PROPERTY = 'app.contact.ftp.server.dir'
FTP_DIRECTORY_DEFAULT = 'ftp_dir'
FTP_USER_PROPERTY = 'app.contact.ftp.user'
FTP_USER_DEFAULT = 'caldera_user'
FTP_PASS_PROPERTY = 'app.contact.ftp.pword'
FTP_PASS_DEFAULT = 'caldera'


class Contact(BaseWorld):
    def __init__(self, services):
        self.check_config()
        self.name = 'ftp'
        self.description = 'Accept agent beacons through ftp'
        self.contact_svc = services.get('contact_svc')
        self.file_svc = services.get('file_svc')
        self.logger = BaseWorld.create_logger('contact_ftp')
        self.host = self.get_config('app.contact.ftp.host')
        self.port = self.get_config('app.contact.ftp.port')
        self.directory = self.get_config('app.contact.ftp.server.dir').lstrip('/')
        self.home = os.path.join('/', self.directory)
        self.user = self.get_config('app.contact.ftp.user')
        self.pword = self.get_config('app.contact.ftp.pword')
        self.server = None

    async def start(self):
        self.set_up_server()
        await self.ftp_server()

    async def stop(self):
        if self.server:
            await self.server.close()

    def set_up_server(self):
        user = self.setup_ftp_users()
        # Instantiate FTP server on local host and listen on port indicated in config
        self.server = FtpHandler(user, self.contact_svc, self.file_svc, self.logger, self.host, self.port, self.user,
                                 self.pword, self.directory)

    def setup_ftp_users(self):
        # Define a new user with full r/w permissions
        return (
            aioftp.User(
                str(self.user),
                str(self.pword),
                home_path=self.home,
                permissions=(
                    aioftp.Permission('/', readable=False, writable=False),
                    aioftp.Permission(self.home, readable=True, writable=True),
                ),
                maximum_connections=MAX_CONNECTIONS,
                read_speed_limit=SPEED_LIMIT,
                write_speed_limit=SPEED_LIMIT,
                read_speed_limit_per_connection=SPEED_LIMIT_PER_CONN,
                write_speed_limit_per_connection=SPEED_LIMIT_PER_CONN
            ),
            aioftp.User(
                home_path=ANON_HOME_PATH,
                permissions=(
                    aioftp.Permission('/', readable=False, writable=False),
                    aioftp.Permission(ANON_HOME_PATH, readable=True),
                ),
                maximum_connections=MAX_ANON_CONNECTIONS,
                read_speed_limit=SPEED_LIMIT,
                write_speed_limit=SPEED_LIMIT,
                read_speed_limit_per_connection=SPEED_LIMIT_PER_CONN,
                write_speed_limit_per_connection=SPEED_LIMIT_PER_CONN
            ),
        )

    async def ftp_server(self):
        await self.server.start(host=self.host, port=self.port)

    def check_config(self):
        if not self.get_config(FTP_HOST_PROPERTY):
            self.set_config('main', FTP_HOST_PROPERTY, FTP_HOST_DEFAULT)
        if not self.get_config(FTP_PORT_PROPERTY):
            self.set_config('main', FTP_PORT_PROPERTY, FTP_PORT_DEFAULT)
        if not self.get_config(FTP_DIRECTORY_PROPERTY):
            self.set_config('main', FTP_DIRECTORY_PROPERTY, FTP_DIRECTORY_DEFAULT)
        if not self.get_config(FTP_USER_PROPERTY):
            self.set_config('main', FTP_USER_PROPERTY, FTP_USER_DEFAULT)
        if not self.get_config(FTP_PASS_PROPERTY):
            self.set_config('main', FTP_PASS_PROPERTY, FTP_PASS_DEFAULT)


class FtpHandler(aioftp.Server):
    def __init__(self, user, contact_svc, file_svc, logger, host, port, username, password, user_dir):
        super().__init__(user, maximum_connections=MAX_CONNECTIONS)
        self.contact_svc = contact_svc
        self.file_svc = file_svc
        self.logger = logger
        self.host = host
        self.port = port
        self.login = username
        self.pword = password
        self.ftp_server_dir = os.path.join(os.getcwd(), user_dir)
        self._check_ftp_server_dir()

    @aioftp.ConnectionConditions(
        aioftp.ConnectionConditions.login_required,
        aioftp.ConnectionConditions.passive_server_started)
    @aioftp.PathPermissions(aioftp.PathPermissions.writable)
    async def stor(self, connection, rest, mode='wb'):

        @aioftp.ConnectionConditions(
            aioftp.ConnectionConditions.data_connection_made,
            wait=True,
            fail_code='425',
            fail_info='Can not open data connection')
        @aioftp.worker
        async def stor_worker(self, connection, rest):
            bytes_obj = b''
            stream = connection.data_connection
            del connection.data_connection
            if connection.restart_offset:
                file_mode = 'r+b'
            else:
                file_mode = mode
            file_out = connection.path_io.open(real_path, mode=file_mode)

            async with file_out, stream:
                if connection.restart_offset:
                    await file_out.seek(connection.restart_offset)
                async for data in stream.iter_by_block(connection.block_size):
                    bytes_obj += data

            await self.handle_agent_file(split_path, bytes_obj)
            connection.response('226', 'data transfer done')
            del stream
            return True

        real_path, virtual_path = self.get_paths(connection, rest)
        split_path = str(virtual_path).split('/')
        self.logger.debug('File received: %s' % str(split_path[-1]))

        if await connection.path_io.is_dir(real_path.parent):
            coro = stor_worker(self, connection, rest)
            task = asyncio.create_task(coro)
            connection.extra_workers.add(task)
            code, info = '150', 'data transfer started'
        else:
            code, info = '550', 'path unreachable'
        connection.response(code, info)
        return True

    async def handle_agent_file(self, split_file_path, file_bytes):
        if re.match(r'^Alive\.txt$', split_file_path[-1]):
            profile = json.loads(file_bytes.decode())
            agent, instructions = await self.contact_caldera_server(profile)
            paw, contents = await self.create_beacon_response(agent, instructions)
            self.write_file(paw, 'Response.txt', bytes(json.dumps(contents).encode('ascii')))
        elif re.match(r'^Payload\.txt$', split_file_path[-1]):
            profile = json.loads(file_bytes.decode())
            file_path, contents, display_name = await self.get_payload_file(profile)
            if file_path is not None:
                self.write_file(profile.get('paw'), profile.get('file'), contents)
        else:
            paw = split_file_path[-2]
            filename = split_file_path[-1]
            await self.submit_uploaded_file(paw, filename, file_bytes)

    async def contact_caldera_server(self, profile):
        paw = profile.get('paw')
        profile['paw'] = paw
        profile['contact'] = profile.get('contact', 'ftp')
        return await self.contact_svc.handle_heartbeat(**profile)

    async def create_beacon_response(self, agent, instructions):
        response = dict(paw=agent.paw,
                        sleep=await agent.calculate_sleep(),
                        watchdog=agent.watchdog,
                        instructions=json.dumps([json.dumps(i.display) for i in instructions]))
        if agent.pending_contact != agent.contact:
            response['new_contact'] = agent.pending_contact
            self.logger.debug('Sending agent instructions to switch from C2 channel %s to %s'
                              % (agent.contact, agent.pending_contact))
        return agent.paw, response

    def write_file(self, paw, file_name, contents):
        agent_dir_path = os.path.join(self.ftp_server_dir, paw)
        try:
            if not os.path.exists(agent_dir_path):
                os.makedirs(agent_dir_path)
            file_path = os.path.join(agent_dir_path, file_name)
            with open(file_path, 'w+b') as f:
                f.write(contents)
            self.logger.debug('File written to: %s' % agent_dir_path)
        except IOError:
            self.logger.error('Failed to write file %s for paw %s', file_name, paw)

    async def get_payload_file(self, payload_dict):
        return await self.file_svc.get_file(payload_dict)

    async def submit_uploaded_file(self, paw, filename, data):
        created_dir = os.path.normpath('/' + paw).lstrip('/')
        saveto_dir = await self.file_svc.create_exfil_sub_directory(dir_name=created_dir)
        await self.file_svc.save_file(filename, data, saveto_dir)
        self.logger.debug('Uploaded file: %s/%s' % (saveto_dir, filename))

    def _check_ftp_server_dir(self):
        if not os.path.exists(self.ftp_server_dir):
            os.makedirs(self.ftp_server_dir)
