import asyncio
import json
import logging
import os
import uuid
import base64

import marshmallow as ma
from aiohttp import web
from aiohttp_jinja2 import render_template

from app.api.packs.advanced import AdvancedPack
from app.api.packs.campaign import CampaignPack
from app.objects.secondclass.c_link import Link
from app.service.auth_svc import check_authorization
from app.utility.base_world import BaseWorld


class RestApi(BaseWorld):

    def __init__(self, services):
        self.log = logging.getLogger('rest_api')
        self.data_svc = services.get('data_svc')
        self.app_svc = services.get('app_svc')
        self.auth_svc = services.get('auth_svc')
        self.file_svc = services.get('file_svc')
        self.rest_svc = services.get('rest_svc')
        asyncio.get_event_loop().create_task(CampaignPack(services).enable())
        asyncio.get_event_loop().create_task(AdvancedPack(services).enable())

    async def enable(self):
        # check if plugin path is present
        if os.path.exists("plugins/magma/dist/assets") and (len(os.listdir("plugins/magma/dist/assets")) > 0):
            self.app_svc.application.router.add_static('/assets', 'plugins/magma/dist/assets/', append_version=True)
        # TODO: only serve static files in legacy plugin mode
        self.app_svc.application.router.add_static('/gui', 'static/', append_version=True)
        # unauthorized GUI endpoints
        self.app_svc.application.router.add_route('GET', '/', self.landing)
        self.app_svc.application.router.add_route('POST', '/enter', self.validate_login)
        self.app_svc.application.router.add_route('POST', '/logout', self.logout)
        # unauthorized API endpoints
        self.app_svc.application.router.add_route('*', '/file/download', self.download_file)
        self.app_svc.application.router.add_route('POST', '/file/upload', self.upload_file)
        # authorized API endpoints
        self.app_svc.application.router.add_route('*', '/api/rest', self.rest_core)
        self.app_svc.application.router.add_route('GET', '/api/{index}', self.rest_core_info)
        self.app_svc.application.router.add_route('GET', '/file/download_exfil', self.download_exfil_file)
        self.app_svc.application.router.add_route('GET', '/{tail:(?!plugin/|api/v2/).*}', self.handle_catch)

    async def validate_login(self, request):
        return await self.auth_svc.login_user(request)

    async def logout(self, request):
        await self.auth_svc.logout_user(request)

    async def landing(self, request):
        return render_template("index.html", request, {})

    async def handle_catch(self, request):
        return render_template("index.html", request, {})

    @check_authorization
    async def rest_core(self, request):
        try:
            access = dict(access=tuple(await self.auth_svc.get_permissions(request)))
            data = dict(await request.json())
            index = data.pop('index')
            options = dict(
                DELETE=dict(
                    agents=lambda d: self.rest_svc.delete_agent(d),
                    operations=lambda d: self.rest_svc.delete_operation(d),
                    abilities=lambda d: self.rest_svc.delete_ability(d),
                    adversaries=lambda d: self.rest_svc.delete_adversary(d)
                ),
                PUT=dict(
                    adversaries=lambda d: self.rest_svc.persist_adversary(access, d),
                    abilities=lambda d: self.rest_svc.persist_ability(access, d),
                    sources=lambda d: self.rest_svc.persist_source(access, d),
                    objectives=lambda d: self.rest_svc.persist_objective(access, d),
                    planners=lambda d: self.rest_svc.update_planner(d),
                    agents=lambda d: self.rest_svc.update_agent_data(d),
                    chain=lambda d: self.rest_svc.update_chain_data(d),
                    operations=lambda d: self.rest_svc.create_operation(access, d),
                    schedule=lambda d: self.rest_svc.create_schedule(access, d),
                    link=lambda d: self.rest_svc.apply_potential_link(Link.load(d)),
                    manual_command=lambda d: self.rest_svc.add_manual_command(access, d)
                ),
                POST=dict(
                    operation_report=lambda d: self.rest_svc.display_operation_report(d),
                    result=lambda d: self.rest_svc.display_result(d),
                    contact=lambda d: self.rest_svc.download_contact_report(d),
                    link=lambda d: self.rest_svc.get_potential_links(**d),
                    operation=lambda d: self.rest_svc.update_operation(**d),
                    task=lambda d: self.rest_svc.task_agent_with_ability(**d),
                    agent_configuration=lambda d: self.rest_svc.get_agent_configuration(d),
                    exfil_files=lambda d: self.rest_svc.list_exfil_files(d)
                )
            )
            if index not in options[request.method]:
                search = {**data, **access}
                return web.json_response(await self.rest_svc.display_objects(index, search))
            return web.json_response(await options[request.method][index](data))
        except ma.ValidationError as e:
            raise web.HTTPBadRequest(content_type='application/json', text=json.dumps(e.messages))
        except TypeError as e:
            self.log.error(repr(e), exc_info=True)
            error_msg = dict(error='400: Bad Request')
            raise web.HTTPBadRequest(text=json.dumps(error_msg), content_type='application/json')
        except Exception as e:
            self.log.error(repr(e), exc_info=True)
            error_msg = dict(error='500: Internal Server Error')
            raise web.HTTPInternalServerError(text=json.dumps(error_msg), content_type='application/json')

    @check_authorization
    async def rest_core_info(self, request):
        try:
            return web.json_response(await self.rest_svc.display_objects(request.match_info['index'], dict(request.query)))
        except ma.ValidationError as e:
            raise web.HTTPBadRequest(content_type='application/json', text=json.dumps(e.messages))
        except Exception as e:
            self.log.error(repr(e), exc_info=True)

    async def upload_file(self, request):
        dir_name = request.headers.get('Directory', None)
        if dir_name:
            return await self.file_svc.save_multipart_file_upload(request, 'data/payloads/')
        agent = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        created_dir = os.path.normpath('/' + agent).lstrip('/')
        saveto_dir = await self.file_svc.create_exfil_sub_directory(dir_name=created_dir)
        operation_dir = await self.file_svc.create_exfil_operation_directory(dir_name=saveto_dir, agent_name=agent[-6:])
        return await self.file_svc.save_multipart_file_upload(request, operation_dir)

    async def download_file(self, request):
        try:
            payload, content, display_name = await self.file_svc.get_file(request.headers)
            headers = dict([('CONTENT-DISPOSITION', 'attachment; filename="%s"' % display_name),
                            ('FILENAME', display_name)])
            return web.Response(body=content, headers=headers)
        except FileNotFoundError:
            raise web.HTTPNotFound(text='File not found')
        except Exception as e:
            raise web.HTTPNotFound(text=str(e))

    @check_authorization
    async def download_exfil_file(self, request):
        exfil_dir = self.get_config('exfil_dir')

        def is_in_exfil_dir(f):
            return f.startswith(exfil_dir + os.sep) or f == exfil_dir

        if request.query.get('file'):
            try:
                file = base64.b64decode(request.query.get('file')).decode('ascii')
                file = os.path.normpath(file)  # normalize path to remove all directory traversal attempts then check for presence in exfil dir
                if not is_in_exfil_dir(file):
                    raise web.HTTPNotFound(text="File not found in exfil dir")
                filename = file.split(os.sep)[-1]
                path = os.sep.join(file.split(os.sep)[:-1])
                _, content = await self.file_svc.read_file(filename, location=path)
                headers = dict([('CONTENT-DISPOSITION', 'attachment; filename="%s"' % filename),
                                ('FILENAME', filename)])
                return web.Response(body=content, headers=headers)
            except web.HTTPNotFound as e:
                raise e
            except FileNotFoundError:
                raise web.HTTPNotFound(text='File not found')
            except Exception as e:
                raise web.HTTPNotFound(text=str(e))
        raise web.HTTPBadRequest(text='A file needs to be specified for download')
