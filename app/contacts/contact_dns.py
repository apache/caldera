import asyncio
import json
import os
import random
import uuid

from base64 import b64encode
from enum import Enum

from app.utility.base_world import BaseWorld


class Contact(BaseWorld):
    def __init__(self, services):
        self.name = 'dns'
        self.description = 'Accept DNS tunneling messages'
        self.log = self.create_logger('contact_dns')
        self.contact_svc = services.get('contact_svc')
        self.domain = self.get_config('app.contact.dns.domain')
        self.handler = Handler(self.domain, services, self.name)
        self.transport = None

    async def start(self):
        loop = asyncio.get_event_loop()
        dns = self.get_config('app.contact.dns.socket')
        addr, port = dns.split(':')
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self.handler, local_addr=(addr, port))

    async def stop(self):
        if self.transport:
            self.transport.close()


class DnsPacket:
    query_response_flag = 0x8000        # 1000 0000  0000 0000
    authoritative_resp_flag = 0x0400    # 0000 0100  0000 0000
    truncated_flag = 0x0200             # 0000 0010  0000 0000
    recursion_desired_flag = 0x0100     # 0000 0001  0000 0000
    recursion_available_flag = 0x0080   # 0000 0000  1000 0000
    opcode_mask = 0x7800                # 0111 1000  0000 0000  # bitwise and with flags to get opcode
    response_code_mask = 0x000f         # 0000 0000  0000 1111  # bitwise and with flags to get response code

    opcode_offset = 11

    def __init__(self, transaction_id, flags, num_questions, num_answer_rrs, num_auth_rrs, num_additional_rrs,
                 qname_labels, record_type, dns_class):
        self.transaction_id = int(transaction_id)
        self.flags = int(flags)
        self.num_questions = int(num_questions)
        self.num_answer_rrs = int(num_answer_rrs)
        self.num_auth_rrs = int(num_auth_rrs)
        self.num_additional_rrs = int(num_additional_rrs)
        self.qname_labels = qname_labels
        self.qname = '.'.join(self.qname_labels)
        self.record_type = record_type
        self.dns_class = int(dns_class)

    def is_query(self):
        return not self.flags & self.query_response_flag

    def is_response(self):
        return bool(self.flags & self.query_response_flag)

    def recursion_desired(self):
        return bool(self.flags & self.recursion_desired_flag)

    def recursion_available(self):
        return bool(self.flags & self.recursion_available_flag)

    def truncated(self):
        return bool(self.flags & self.truncated_flag)

    def get_opcode(self):
        return (self.flags & self.opcode_mask) >> self.opcode_offset

    def has_standard_query(self):
        return self.get_opcode() == 0x0

    def get_response_code(self):
        return self.flags & self.response_code_mask

    def __str__(self):
        return '\n'.join([
            'Qname: %s' % self.qname,
            'Is query: %s' % self.is_query(),
            'Is response: %s' % self.is_response(),
            'Transaction ID: 0x%04x' % self.transaction_id,
            'Flags: 0x%04x' % self.flags,
            'Num questions: %d' % self.num_questions,
            'Num answer resource records: %d' % self.num_answer_rrs,
            'Num auth resource records: %d' % self.num_auth_rrs,
            'Num additional resource records: %d' % self.num_additional_rrs,
            'Record type: %d' % self.record_type.value,
            'Class: %d' % self.dns_class,
            'Standard query: %s' % self.has_standard_query(),
            'Opcode: 0x%04x' % self.get_opcode(),
            'Response code: 0x%04x' % self.get_response_code(),
            'Recursion desired: %s' % self.recursion_desired(),
            'Recursion available: %s' % self.recursion_available(),
            'Truncated: %s' % self.truncated(),
        ])

    def _get_header_bytes(self, byteorder='big'):
        return self.transaction_id.to_bytes(2, byteorder=byteorder) + self.flags.to_bytes(2, byteorder=byteorder) \
               + self.num_questions.to_bytes(2, byteorder=byteorder) \
               + self.num_answer_rrs.to_bytes(2, byteorder=byteorder) \
               + self.num_auth_rrs.to_bytes(2, byteorder=byteorder) \
               + self.num_additional_rrs.to_bytes(2, byteorder=byteorder)

    def _get_query_bytes(self, byteorder='big'):
        return self._get_qname_bytes(self.qname_labels, byteorder=byteorder) \
               + self.record_type.value.to_bytes(2, byteorder=byteorder) \
               + self.dns_class.to_bytes(2, byteorder=byteorder)

    @staticmethod
    def generate_packet_from_bytes(data, byteorder='big'):
        transaction_id = int.from_bytes(data[0:2], byteorder=byteorder)
        flags = int.from_bytes(data[2:4], byteorder=byteorder)
        num_questions = int.from_bytes(data[4:6], byteorder=byteorder)
        num_answer_rrs = int.from_bytes(data[6:8], byteorder=byteorder)
        num_auth_rrs = int.from_bytes(data[8:10], byteorder=byteorder)
        num_additional_rrs = int.from_bytes(data[10:12], byteorder=byteorder)
        qname_labels, qname_length = DnsPacket._parse_qname_labels(data[12:])
        qname_offset = 12 + qname_length
        record_type = DnsRecordType(int.from_bytes(data[qname_offset:qname_offset+2], byteorder=byteorder))
        dns_class = int.from_bytes(data[qname_offset+2:qname_offset+4], byteorder=byteorder)
        return DnsPacket(transaction_id, flags, num_questions, num_answer_rrs, num_auth_rrs, num_additional_rrs,
                         qname_labels, record_type, dns_class)

    @staticmethod
    def _get_qname_bytes(qname_labels, byteorder='big'):
        qname_bytes = b''
        for label in qname_labels:
            qname_bytes += len(label).to_bytes(1, byteorder=byteorder)
            qname_bytes += label.encode('ascii')
        qname_bytes += b'\x00'
        return qname_bytes

    @staticmethod
    def _parse_qname_labels(data):
        remaining = data
        parts = []
        representation_length = 1
        while remaining and int(remaining[0]):
            length = int(remaining[0])
            parts.append(remaining[1:1 + length].decode('utf-8'))
            remaining = remaining[1 + length:]
            representation_length += 1 + length
        return parts, representation_length


class DnsAnswerObj:
    def __init__(self, record_type, dns_class, ttl, data):
        self.record_type = record_type
        self.dns_class = dns_class
        self.ttl = ttl
        self.data = data

    def get_bytes(self, byteorder='big'):
        return DnsResponse.standard_pointer.to_bytes(2, byteorder=byteorder) \
            + self.record_type.value.to_bytes(2, byteorder=byteorder) \
            + self.dns_class.to_bytes(2, byteorder=byteorder) \
            + self.ttl.to_bytes(4, byteorder=byteorder) \
            + len(self.data).to_bytes(2, byteorder=byteorder) \
            + self.data

    def __str__(self):
        return '\n'.join([
            'Record type: %d' % self.record_type.value,
            'Dns class: %d' % self.dns_class,
            'TTL: %d' % self.ttl,
            'Data: %s' % self.data.hex(),
            'Data length: %d' % len(self.data),
        ])


class DnsResponse(DnsPacket):
    standard_pointer = 0xc00c
    max_txt_size = 255
    default_ttl = 300
    max_ttl = 86400
    min_ttl = 300

    def __init__(self, transaction_id, flags, num_questions, num_answer_rrs, num_auth_rrs, num_additional_rrs,
                 qname_labels, record_type, dns_class, answers):
        super().__init__(transaction_id, flags, num_questions, num_answer_rrs, num_auth_rrs, num_additional_rrs,
                         qname_labels, record_type, dns_class)
        self.answers = answers if answers else []

    def get_bytes(self, byteorder='big'):
        return self._get_header_bytes(byteorder=byteorder) + self._get_query_bytes(byteorder=byteorder) \
            + self._get_answer_bytes(byteorder=byteorder)

    def __str__(self):
        output = [super().__str__(), 'Answers:']
        for answer in self.answers:
            answer_str_tabbed = '\n    '.join(str(answer).split('\n'))
            output.append('    ' + answer_str_tabbed + '\n')
        return '\n'.join(output)

    def _get_answer_bytes(self, byteorder='big'):
        answer_bytes = b''
        for answer in self.answers:
            answer_bytes += answer.get_bytes(byteorder=byteorder)
        return answer_bytes

    @staticmethod
    def generate_response_for_query(dns_query, r_code, answers, authoritative=True, recursion_available=False,
                                    truncated=False):
        """Given DnsPacket query, return response with provided fields.
        Answers is list of DnsAnswerObj for the given query.
        """

        transaction_id = dns_query.transaction_id  # DNS response carries same transaction ID as query

        authoritative_flag = DnsResponse.authoritative_resp_flag if authoritative else 0x0
        truncated_flag = DnsResponse.truncated_flag if truncated else 0x0
        opcode_mask = dns_query.get_opcode() << DnsResponse.opcode_offset
        recursion_desired_flag = DnsResponse.recursion_desired_flag if dns_query.recursion_desired() else 0x0
        recursion_available_flag = DnsResponse.recursion_available_flag if recursion_available else 0x0
        flags = 0x0 | DnsResponse.query_response_flag | opcode_mask | authoritative_flag | truncated_flag \
            | recursion_desired_flag | recursion_available_flag | r_code.value
        num_questions = dns_query.num_questions
        num_answers = len(answers)
        num_auth_rrs = 0
        num_additional_rrs = 0
        qname_labels = dns_query.qname_labels
        record_type = dns_query.record_type
        dns_class = dns_query.dns_class
        return DnsResponse(transaction_id, flags, num_questions, num_answers, num_auth_rrs, num_additional_rrs,
                           qname_labels, record_type, dns_class, answers)


class DnsRecordType(Enum):
    A = 1
    NS = 2
    TXT = 16
    AAAA = 28
    CNAME = 5


class DnsResponseCodes(Enum):
    SUCCESS = 0
    NXDOMAIN = 3


class Handler(asyncio.DatagramProtocol):
    _remaining_data_suffix = 0x2e  # .
    _completed_data_suffix = 0x2c  # ,

    class MessageType(Enum):
        Beacon = 'be'  # Beacons will also contain execution results
        InstructionDownload = 'id'
        PayloadRequest = 'pr'
        PayloadFilenameDownload = 'pf'
        PayloadDataDownload = 'pd'
        FileUploadRequest = 'ur'
        FileUploadData = 'ud'

    class TunneledMessage:
        def __init__(self, message_id, message_type, num_chunks):
            self.message_id = message_id
            self.message_type = message_type
            self.chunks = [None] * num_chunks
            self.required_chunks = num_chunks
            self.completed_chunks = 0

        def add_chunk(self, chunk_index, contents):
            if contents and self.chunks[chunk_index] is None:
                self.chunks[chunk_index] = contents
                self.completed_chunks += 1

        def is_complete(self):
            return self.completed_chunks == self.required_chunks

        def export_contents(self):
            return b''.join(self.chunks)

    class StoredResponse:
        def __init__(self, data):
            self.data = data
            self.offset = 0
            self.size = len(data)

        def read_data(self, num_bytes):
            ret_data = None
            if not self.finished_reading():
                end_seek = min(self.size, self.offset + num_bytes)
                ret_data = self.data[self.offset:end_seek]
                self.offset = end_seek
            return ret_data

        def finished_reading(self):
            return self.offset >= self.size

    class ClientRequestContext:
        def __init__(self, request_id, dns_request, request_contents):
            self.request_id = request_id
            self.dns_request = dns_request  # DnsPacket object
            self.request_contents = request_contents

    class FileUploadRequest:
        def __init__(self, request_id, requesting_paw, directory, filename):
            self.request_id = request_id
            self.requesting_paw = requesting_paw
            self.directory = directory
            self.filename = filename

    def __init__(self, domain, services, name):
        super().__init__()
        self.services = services
        self.contact_svc = services.get('contact_svc')
        self.file_svc = services.get('file_svc')
        self.name = name
        self.log = BaseWorld.create_logger('contact_dns_handler')
        self.domain = domain
        self.transport = None

        # Stores received message chunks.
        self.pending_messages = {}

        # Stores completed messages from agents.
        self.completed_messages = {}

        # Stores instructions for agents to fetch
        # Key: message ID.
        # Value: StoredResponse obj
        self.pending_instructions = {}

        # Stores payloads for agents to fetch
        # Key: message ID.
        # Value: StoredResponse obj
        self.pending_payloads = {}

        # Stores payload names for agents to fetch
        # Key: message ID.
        # Value: StoredResponse obj
        self.pending_payload_names = {}

        # Maps upload request IDs to upload requests
        # Key: message ID
        # Value: FileUploadRequest obj
        self.pending_uploads = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        asyncio.get_event_loop().create_task(self._handle_msg(data, addr))

    async def _handle_msg(self, data, addr):
        try:
            response_data = await self.generate_dns_tunneling_response_bytes(data)
            self.transport.sendto(response_data, addr)
        except Exception as e:
            self.log.error(e)

    async def generate_dns_tunneling_response_bytes(self, data):
        packet = DnsPacket.generate_packet_from_bytes(data)
        response_obj = await self._get_response_for_dns_request(packet)
        return response_obj.get_bytes()

    async def _get_response_for_dns_request(self, dns_request_packet):
        """Given DNS request packet, parse out the agent message. If the message is incomplete, add to pending
        message. If the message is complete, or if it completes any pending messages, then process the complete
        message. Returns the corresponding DNS response object for the request."""

        # Qname labels are of format message_id.message_type.chunk_index.total_chunks.data.contact_base_domain_fqdn.
        # For example, 281723.be.0.1.68656c6c6f20776f726c64.mycalderac2domain.com indicates the following:
        #   message ID is 281723
        #   message type is "be", meaning "beacon"
        #   this is the first chunk out of a total of 1 chunks
        #   the data is "hello world" encoded in hex
        #   base c2 domain is mycalderac2domain.com
        labels = dns_request_packet.qname_labels
        if dns_request_packet.qname.lower() != self.domain.lower() \
                and not dns_request_packet.qname.lower().endswith('.' + self.domain.lower()):
            self.log.warning('Received request for qname %s that is not the C2 DNS tunneling domain %s' %
                             (dns_request_packet.qname, self.domain))
            self.log.warning('Sending NXDOMAIN response.')
            return self._generate_nxdomain_response(dns_request_packet)
        if dns_request_packet.record_type == DnsRecordType.AAAA:
            return self._generate_dummy_ipv6_response(dns_request_packet)
        elif dns_request_packet.record_type not in (DnsRecordType.A, DnsRecordType.TXT):
            self.log.warning('Received unsupported DNS record type request %d' % dns_request_packet.record_type.value)
            return self._generate_empty_response(dns_request_packet)

        message_id = labels[0]
        try:
            # Store message
            self._store_data_chunk(labels)
        except ValueError as e:
            # Invalid or mismatched message type - send NXDomain response
            self.log.warning('Invalid dns tunneling message type received from client. Full error: %s' % e)
            return self._generate_nxdomain_response(dns_request_packet)

        # Handle the message if complete. Any non-A record request is automatically considered "complete"
        if self._message_complete(message_id) or dns_request_packet.record_type != DnsRecordType.A:
            self._store_completed_message(message_id)
            return await self._generate_response_for_completed_message(message_id, dns_request_packet)
        else:
            # Return standard acknowledgement response for incomplete A-record messages
            return self._generate_response_for_incomplete_message(dns_request_packet)

    def _generate_nxdomain_response(self, dns_query):
        return DnsResponse.generate_response_for_query(dns_query, DnsResponseCodes.NXDOMAIN, [])

    def _generate_empty_response(self, dns_query):
        return DnsResponse.generate_response_for_query(dns_query, DnsResponseCodes.SUCCESS, [])

    def _generate_dummy_ipv6_response(self, dns_request_packet):
        ipv6_bytes = self._get_random_ipv6_addr()
        answer_obj = DnsAnswerObj(DnsRecordType.AAAA, dns_request_packet.dns_class, DnsResponse.default_ttl, ipv6_bytes)
        return DnsResponse.generate_response_for_query(dns_request_packet, DnsResponseCodes.SUCCESS, [answer_obj])

    def _store_completed_message(self, message_id):
        msg = self.pending_messages.pop(message_id, None)
        if msg:
            self.completed_messages[message_id] = msg

    async def _generate_response_for_completed_message(self, message_id, dns_request_packet):
        msg = self.completed_messages.pop(message_id, None)
        if msg:
            contents = msg.export_contents()
            request_context = self.ClientRequestContext(message_id, dns_request_packet, contents)

            # Process message based on message type
            if msg.message_type == self.MessageType.Beacon:
                return await self._process_beacon(request_context)
            elif msg.message_type == self.MessageType.InstructionDownload:
                return self._process_download_request_via_txt(request_context, self.pending_instructions, 'instructions')
            elif msg.message_type == self.MessageType.PayloadRequest:
                return await self._process_payload_request(request_context)
            elif msg.message_type == self.MessageType.PayloadFilenameDownload:
                return self._process_download_request_via_txt(request_context, self.pending_payload_names, 'payload filename')
            elif msg.message_type == self.MessageType.PayloadDataDownload:
                return self._process_download_request_via_txt(request_context, self.pending_payloads, 'payload data')
            elif msg.message_type == self.MessageType.FileUploadRequest:
                return self._process_upload_request(request_context)
            elif msg.message_type == self.MessageType.FileUploadData:
                return await self._process_upload_data(request_context)
            else:
                self.log.warning('Unsupported message type %s' % msg.message_type.value)
                return self._generate_nxdomain_response(dns_request_packet)

    def _process_upload_request(self, request_context):
        upload_metadata = self._unpack_json(request_context.request_contents)
        if upload_metadata:
            filename = upload_metadata.get('file')
            requesting_paw = upload_metadata.get('paw')
            directory = upload_metadata.get('directory', str(uuid.uuid4()))
            if filename and requesting_paw:
                self.log.debug('Received upload request for file %s for request ID %s' %
                               (filename, request_context.request_id))
                self.pending_uploads[request_context.request_id] = self.FileUploadRequest(
                    request_context.request_id,
                    requesting_paw,
                    directory,
                    filename
                )
                return self._generate_server_ready_ipv4_response(request_context.dns_request)
            else:
                self.log.warning('Client file upload request (ID %s) is missing filename, hostname, and/or paw' %
                                 request_context.request_id)
        else:
            self.log.warning('Empty upload request received from message ID %s' % request_context.request_id)
        return self._generate_nxdomain_response(request_context.dns_request)

    async def _process_upload_data(self, request_context):
        # Make sure we are expecting this upload
        upload_request = self.pending_uploads.get(request_context.request_id)
        if upload_request:
            # Append the request ID to the filename to help make it unique
            unique_filename = '-'.join([upload_request.filename, request_context.request_id])

            # request_context.request_contents contains the file upload data
            await self._submit_uploaded_file(upload_request.requesting_paw,
                                             upload_request.directory,
                                             unique_filename,
                                             request_context.request_contents)
            return self._generate_server_ready_ipv4_response(request_context.dns_request)
        else:
            self.log.warning('Client sent upload data without first making an upload request (request ID %s)' %
                             request_context.request_id)
        return self._generate_nxdomain_response(request_context.dns_request)

    async def _submit_uploaded_file(self, paw, directory, filename, data):
        if paw and filename and directory and data:
            if not self.file_svc._validate_filename(filename):
                self.log.warning('Invalid filename rejected: %r', filename)
                return
            created_dir = os.path.normpath('/' + directory).lstrip('/')
            saveto_dir = await self.file_svc.create_exfil_sub_directory(dir_name=created_dir)
            await self.file_svc.save_file(filename, data, saveto_dir)
            self.log.debug('Uploaded file %s/%s' % (saveto_dir, filename))

    async def _process_payload_request(self, request_context):
        payload_metadata = self._unpack_json(request_context.request_contents)
        if payload_metadata:
            filename = payload_metadata.get('file')
            if filename:
                payload, content, display_name = await self._fetch_payload(payload_metadata)
                if payload and content and display_name:
                    # Save file contents and payload name for agent to fetch later.
                    encoded_payload_name = b64encode(display_name.encode('utf-8'))
                    encoded_contents = b64encode(content)
                    self.pending_payloads[request_context.request_id] = self.StoredResponse(encoded_contents)
                    self.pending_payload_names[request_context.request_id] = self.StoredResponse(encoded_payload_name)

                    # Notify agent that payload is ready
                    self.log.debug('Stored payload %s for request ID %s' % (display_name, request_context.request_id))
                    return self._generate_server_ready_ipv4_response(request_context.dns_request)
                else:
                    self.log.warning('Failed to fetch file: %s' % filename)
            else:
                self.log.warning('Client did not include filename in payload request ID %s' % request_context.request_id)
        else:
            self.log.warning('Empty payload request received from message ID %s' % request_context.request_id)
        return self._generate_nxdomain_response(request_context.dns_request)

    async def _fetch_payload(self, payload_metadata):
        try:
            return await self.file_svc.get_file(payload_metadata)
        except FileNotFoundError:
            self.log.warning('Could not find requested payload')
            return None, None, None
        except Exception as e:
            self.log.warning('Error fetching payload: %s' % e)
            return None, None, None

    def _process_download_request_via_txt(self, request_context, data_repo, data_type='unknown'):
        if request_context.dns_request.record_type != DnsRecordType.TXT:
            self.log.warning('Client attempted to request %s without sending TXT query.' % data_type)
            return self._generate_nxdomain_response(request_context.dns_request)
        else:
            stored_response = data_repo.get(request_context.request_id)
            if stored_response:
                return self._generate_data_chunk_txt_response(data_repo, request_context, stored_response)
            else:
                self.log.warning('No %s found for message ID %s' % (data_type, request_context.request_id))
                return self._generate_nxdomain_response(request_context.dns_request)

    def _generate_data_chunk_txt_response(self, data_repo, request_context, stored_response):
        data = bytearray(stored_response.read_data(DnsResponse.max_txt_size - 1))
        if stored_response.finished_reading():
            # This is the last data chunk to send.
            data.append(self._completed_data_suffix)
            data_repo.pop(request_context.request_id)
        else:
            data.append(self._remaining_data_suffix)
        return self._generate_txt_response(request_context.dns_request, data, DnsResponse.default_ttl)

    async def _process_beacon(self, request_context):
        profile = self._unpack_json(request_context.request_contents)
        if profile:
            profile['paw'] = profile.get('paw')
            profile['contact'] = profile.get('contact', self.name)
            beacon_response = await self._get_beacon_response(profile)

            # Store beacon response for agent to fetch later, and tell agent that beacon is ready
            self._store_beacon_response(request_context.request_id, beacon_response)
            return self._generate_server_ready_ipv4_response(request_context.dns_request)
        else:
            self.log.warning('Empty profile received from beacon message ID %s' % request_context.request_id)
            return self._generate_nxdomain_response(request_context.dns_request)

    async def _get_beacon_response(self, profile):
        agent, instructions = await self.contact_svc.handle_heartbeat(**profile)
        response = dict(paw=agent.paw,
                        sleep=await agent.calculate_sleep(),
                        watchdog=agent.watchdog,
                        instructions=json.dumps([json.dumps(i.display) for i in instructions]))
        if agent.pending_contact != agent.contact:
            response['new_contact'] = agent.pending_contact
            self.log.debug('Sending agent instructions to switch from C2 channel %s to %s'
                           % (agent.contact, agent.pending_contact))
        if agent.executor_change_to_assign:
            response['executor_change'] = agent.assign_pending_executor_change()
            self.log.debug('Asking agent to update executor: %s', response.get('executor_change'))
        return response

    def _store_beacon_response(self, beacon_id, response_dict):
        # Convert response_dict to json bytes
        response_bytes = b64encode(json.dumps(response_dict).encode('utf-8'))
        self.pending_instructions[beacon_id] = self.StoredResponse(response_bytes)

    def _generate_server_ready_ipv4_response(self, dns_request_packet):
        # IPv4 address with odd last octet means that server is ready for next step
        response_data = self._generate_random_ipv4_response(False)
        return self._generate_ipv4_response(dns_request_packet, response_data, DnsResponse.default_ttl)

    def _unpack_json(self, data):
        json_contents = None
        try:
            json_contents = json.loads(data.decode('utf-8'))
        except Exception as e:
            self.log.error('Error decoding contents into json: %s' % e)
        return json_contents

    def _generate_ipv4_response(self, dns_request_packet, ipv4_bytes, ttl):
        answer_obj = DnsAnswerObj(DnsRecordType.A, dns_request_packet.dns_class, ttl, ipv4_bytes)
        return DnsResponse.generate_response_for_query(dns_request_packet, DnsResponseCodes.SUCCESS, [answer_obj])

    def _generate_txt_response(self, dns_request_packet, txt_bytes, ttl):
        payload = len(txt_bytes).to_bytes(1, byteorder='big') + txt_bytes
        answer_obj = DnsAnswerObj(DnsRecordType.TXT, dns_request_packet.dns_class, ttl, payload)
        return DnsResponse.generate_response_for_query(dns_request_packet, DnsResponseCodes.SUCCESS, [answer_obj])

    def _generate_response_for_incomplete_message(self, request_packet):
        if request_packet.record_type != DnsRecordType.A:
            self.log.warning("Client sent incomplete DNS tunneling message that was not an A record request. Invalid")
            return self._generate_nxdomain_response(request_packet)
        else:
            response_data = self._generate_random_ipv4_response(True)
            return self._generate_ipv4_response(request_packet, response_data, DnsResponse.default_ttl)

    def _message_complete(self, message_id):
        """Returns true if the message is complete, false if still missing chunks."""

        msg = self.pending_messages.get(message_id)
        return msg and msg.is_complete()

    def _store_data_chunk(self, labels):
        """Given the DNS request qname labels, store the data chunks in the appropriate pending tunneled message."""

        message_id = labels[0]
        message_type = self.MessageType(labels[1])
        chunk_index = int(labels[2])
        num_chunks = int(labels[3])
        data = bytes.fromhex(labels[4])

        pending_message = self.pending_messages.get(message_id)
        if not pending_message:
            # First chunk seen for this message ID.
            pending_message = self.TunneledMessage(message_id, message_type, num_chunks)
            self.pending_messages[message_id] = pending_message
        elif pending_message.message_type != message_type:
            raise ValueError('New data chunk type %s does not match current message type %s for message ID %s'
                             % (pending_message.message_type.value, message_type.value, message_id))
        pending_message.add_chunk(chunk_index, data)

    @staticmethod
    def _generate_random_ipv4_response(last_octet_even):
        """Generate random IPv4 address as an A record response.
        If last_octet_even is true, make sure the last octet is even. Otherwise, make sure it is odd."""
        random_ip_int = random.randrange(1, 0xffffffff)
        if (random_ip_int % 2 == 0 and not last_octet_even) or (random_ip_int % 2 == 1 and last_octet_even):
            random_ip_int += 1
        return random_ip_int.to_bytes(4, byteorder='big')

    @staticmethod
    def _get_random_ipv6_addr():
        return random.getrandbits(128).to_bytes(16, byteorder='big')
