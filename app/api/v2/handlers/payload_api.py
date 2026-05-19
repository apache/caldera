import asyncio
import itertools
import os
import pathlib
import re
from io import IOBase
from typing import Optional

import aiohttp_apispec
from aiohttp import web

from app.api.v2.handlers.base_api import BaseApi
from app.api.v2.schemas.payload_schemas import PayloadQuerySchema, PayloadSchema, PayloadCreateRequestSchema, \
    PayloadDeleteRequestSchema


class PayloadApi(BaseApi):
    def __init__(self, services):
        super().__init__(auth_svc=services['auth_svc'])
        self.data_svc = services['data_svc']
        self.file_svc = services['file_svc']

    def add_routes(self, app: web.Application):
        router = app.router
        router.add_get('/payloads', self.get_payloads)
        router.add_post("/payloads", self.post_payloads)
        router.add_delete("/payloads/{name}", self.delete_payloads)

    @aiohttp_apispec.docs(tags=['payloads'],
                          summary='Retrieve payloads',
                          description='Retrieves all stored payloads. Supports optional filtering by name '
                                      '(case-insensitive substring match via the `name` query parameter).')
    @aiohttp_apispec.querystring_schema(PayloadQuerySchema)
    @aiohttp_apispec.response_schema(PayloadSchema(),
                                     description='Returns a list of all payloads in PayloadSchema format.')
    async def get_payloads(self, request: web.Request):
        sort: bool = request['querystring'].get('sort')
        exclude_plugins: bool = request['querystring'].get('exclude_plugins')
        add_path: bool = request['querystring'].get('add_path')
        name_filter: Optional[str] = request['querystring'].get('name')

        cwd = pathlib.Path.cwd()
        payload_dirs = [cwd / 'data' / 'payloads']

        if not exclude_plugins:
            payload_dirs.extend(cwd / 'plugins' / plugin.name / 'payloads'
                                for plugin in await self.data_svc.locate('plugins') if plugin.enabled)

        payloads = {
            str(p.parent.relative_to(cwd) / self.file_svc.remove_xored_extension(p.name))
            if add_path
            else self.file_svc.remove_xored_extension(p.name)
            for p in itertools.chain.from_iterable(p_dir.glob('[!.]*') for p_dir in payload_dirs)
            if p.is_file() and not p.is_symlink() and not p.name.startswith('.')
        }

        payloads = list(payloads)

        if name_filter:
            name_filter_lower = name_filter.lower()
            payloads = [p for p in payloads if name_filter_lower in pathlib.PurePath(p).name.lower()]

        if sort:
            payloads.sort()

        return web.json_response(payloads)

    @aiohttp_apispec.docs(
        tags=['payloads'],
        summary='Create a payload',
        description='Uploads a payload.')
    @aiohttp_apispec.form_schema(PayloadCreateRequestSchema)
    @aiohttp_apispec.response_schema(
        PayloadSchema(),
        description="The created payload in a list in PayloadSchema format (with name changed in case of a duplicate).")
    async def post_payloads(self, request: web.Request):
        # As aiohttp_apispec.form_schema already calls request.multipart(),
        # accessing the file using the prefilled request["form"] dictionary.
        file_field: web.FileField = request["form"]["file"]

        # Sanitize the file name to prevent directory traversal
        sanitized_filename = self.sanitize_filename(file_field.filename)

        # Generate the file name and path
        file_name, file_path = await self.__generate_file_name_and_path(sanitized_filename)

        # Save the file to a temporary location first
        temp_file_path = pathlib.Path(file_path).parent / f"temp_{file_name}"
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.__save_file, str(temp_file_path), file_field.file)

        # Validate the saved file to ensure it is not a symbolic link
        if temp_file_path.is_symlink():
            temp_file_path.unlink()
            raise web.HTTPBadRequest(reason="Uploaded file is a symbolic link and is not allowed.")

        # Move the validated file to the final destination
        temp_file_path.rename(file_path)

        body: dict[list[str]] = {"payloads": [file_name]}
        return web.json_response(body)

    @aiohttp_apispec.docs(
        tags=['payloads'],
        summary='Delete a payload',
        description='Deletes a given payload.',
        responses={
            204: {"description": "Payload has been properly deleted."},
            404: {"description": "Payload not found."},
        })
    @aiohttp_apispec.match_info_schema(PayloadDeleteRequestSchema)
    async def delete_payloads(self, request: web.Request):
        file_name: str = request.match_info.get("name")

        # Filename Input Validation
        if not file_name:
            return web.HTTPBadRequest(reason="File name is required.")

        # Sanitize the filename
        sanitized_filename = self.sanitize_filename(file_name)

        # Additional safety checks
        if not sanitized_filename or sanitized_filename in ['.', '..']:
            return web.HTTPBadRequest(reason="Invalid file name.")

        try:
            safe_path = self.validate_and_canonicalize_path(sanitized_filename)
            safe_path_obj = pathlib.Path(safe_path)
            if safe_path_obj.is_symlink():
                raise ValueError(f"Invalid path: {sanitized_filename} is a symbolic link.")
            os.remove(safe_path_obj)
            response = web.HTTPNoContent()
        except ValueError as e:
            response = web.HTTPNotFound(reason=str(e))
        except FileNotFoundError:
            response = web.HTTPNotFound()
        except PermissionError:
            response = web.HTTPForbidden(reason="Permission denied.")
        raise response

    @classmethod
    async def __generate_file_name_and_path(cls, sanitized_filename: str) -> [str, str]:
        """
        Finds whether an uploaded file already exists in the payload directory.
        In the case, generates a new file name with an incremental suffix to avoid overriding the existing one.
        Otherwise, the original file name is used.

        :param sanitized_filename: The sanitized file name.
        :return: A tuple containing the generated file name and path for future storage.
        """
        file_name_candidate: str = sanitized_filename
        file_path: str = os.path.join('data/payloads/', file_name_candidate)
        suffix: int = 1

        # Generating a file suffix in the case it already exists.
        while os.path.exists(file_path):
            file_name_candidate = f"{pathlib.Path(sanitized_filename).stem}_" \
                                  f"{suffix}{pathlib.Path(sanitized_filename).suffix}"
            file_path = os.path.join('data/payloads/', file_name_candidate)
            suffix += 1
        file_name: str = file_name_candidate
        return file_name, file_path

    @staticmethod
    def __save_file(target_file_path: str, io_base_src: IOBase):
        """
        Save an uploaded file content into a targeted file path.
        Note this method calls blocking methods and must be run into a dedicated thread.

        :param target_file_path: The destination path to write to.
        :param io_base_src: The stream with file content to read from.
        """
        size: int = 0
        read_chunk: bool = True
        with open(target_file_path, 'wb') as buffered_io_base_dest:
            while read_chunk:
                chunk: bytes = io_base_src.read(8192)
                if chunk:
                    size += len(chunk)
                    buffered_io_base_dest.write(chunk)
                else:
                    read_chunk = False

    @staticmethod
    def validate_and_canonicalize_path(input_path: str, base_directory: str = "data/payloads/") -> str:
        """
        Validates and canonicalizes a file path to ensure it is within the designated directory.

        :param input_path: The input file path to validate.
        :param base_directory: The base directory to constrain paths to.
        :return: The canonicalized absolute path if valid.
        :raises ValueError: If the path resolves outside the base directory.
        """
        base_dir = pathlib.Path(base_directory).resolve()

        try:
            resolved_path = (base_dir / pathlib.Path(input_path).name).resolve()
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"Invalid path: {input_path} resolves outside the designated directory {base_directory}")
        except Exception as e:
            raise ValueError(f"Invalid path: {input_path}. Error: {e}")

        return str(resolved_path)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitizes a file name to remove potentially dangerous characters.

        :param filename: The original file name.
        :return: A sanitized file name.
        """
        return re.sub(r'[^\w\.-]', '_', filename)
