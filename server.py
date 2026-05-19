import argparse
import asyncio
import logging
import os
import signal
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme
import sys
import warnings
import subprocess

import aiohttp_apispec
from aiohttp_apispec import validation_middleware
from aiohttp import web

import app.api.v2
from app import version
from app.ascii_banner import get_ascii_banner, no_color, print_rich_banner
from app.api.rest_api import RestApi
from app.api.v2.responses import apispec_request_validation_middleware
from app.api.v2.security import pass_option_middleware
from app.objects.c_agent import Agent
from app.objects.c_obfuscator import Obfuscator
from app.objects.secondclass.c_executor import Executor
from app.objects.secondclass.c_link import Link
from app.service.app_svc import AppService
from app.service.auth_svc import AuthService
from app.service.contact_svc import ContactService
from app.service.data_svc import DataService, DATA_BACKUP_DIR
from app.service.knowledge_svc import KnowledgeService
from app.service.event_svc import EventService
from app.service.file_svc import FileSvc
from app.service.learning_svc import LearningService
from app.service.planning_svc import PlanningService
from app.service.rest_svc import RestService
from app.utility.base_object import AppConfigGlobalVariableIdentifier
from app.utility.base_world import BaseWorld
from app.utility.config_util import ensure_local_config


MAGMA_PATH = "./plugins/magma"


def setup_logger(level=logging.DEBUG):
    format = "%(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    if no_color():
        logging.basicConfig(level=level, format=format, datefmt=datefmt)
    else:
        console = Console(theme=Theme({"logging.level.warning": "yellow"}))
        logging.basicConfig(
            level=level,
            format=format,
            datefmt=datefmt,
            handlers=[RichHandler(rich_tracebacks=True, markup=True, console=console)]
        )

    for logger_name in logging.root.manager.loggerDict.keys():
        if logger_name in ("aiohttp.server", "asyncio"):
            continue
        else:
            logging.getLogger(logger_name).setLevel(100)
    logging.getLogger("markdown_it").setLevel(logging.WARNING)
    logging.captureWarnings(True)


async def start_server():
    await auth_svc.apply(app_svc.application, BaseWorld.get_config("users"))
    runner = web.AppRunner(app_svc.application)
    await runner.setup()
    await web.TCPSite(
        runner, BaseWorld.get_config("host"), BaseWorld.get_config("port")
    ).start()


def run_tasks(services, run_vue_server=False):
    def _handle_sigterm(*args):
        """Convert SIGTERM into KeyboardInterrupt to reuse the existing teardown path.

        When Caldera runs as a systemd service (or with ``& disown`` / ``nohup``),
        the process receives SIGTERM on shutdown rather than SIGINT/KeyboardInterrupt.
        Without this handler the teardown/save logic is never called, so operations
        and other in-memory state are lost (issue #3018).
        """
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    loop = asyncio.new_event_loop()
    # The event loop is set here, before any async work begins.  Services
    # (AppService, DataService, etc.) are instantiated in __main__ prior to
    # this call but they do not cache the loop at construction time — they
    # resolve it lazily via asyncio.get_event_loop().  Setting it explicitly
    # here ensures all subsequent loop.create_task() / loop.run_until_complete()
    # calls operate on the same, controlled loop instance.
    asyncio.set_event_loop(loop)
    try:
        loop.create_task(app_svc.validate_requirements())
        loop.run_until_complete(data_svc.restore_state())
        loop.run_until_complete(knowledge_svc.restore_state())
        loop.run_until_complete(app_svc.register_contacts())
        loop.run_until_complete(app_svc.load_plugins(args.plugins))
        loop.run_until_complete(
            data_svc.load_data(
                loop.run_until_complete(data_svc.locate("plugins", dict(enabled=True)))
            )
        )
        loop.run_until_complete(
            app_svc.load_plugin_expansions(
                loop.run_until_complete(data_svc.locate("plugins", dict(enabled=True)))
            )
        )
        loop.run_until_complete(RestApi(services).enable())
        loop.run_until_complete(auth_svc.set_login_handlers(services))
        loop.create_task(app_svc.start_sniffer_untrusted_agents())
        loop.create_task(app_svc.resume_operations())
        loop.create_task(app_svc.run_scheduler())
        loop.create_task(learning_svc.build_model())
        loop.create_task(app_svc.watch_ability_files())
        loop.run_until_complete(start_server())
        loop.run_until_complete(event_svc.fire_event(exchange="system", queue="ready"))
        loop.run_until_complete(
            data_svc.store(
                Obfuscator(name='plain-text',
                           description='Does no obfuscation to any command, instead running it in plain text',
                           module='app.obfuscators.plain_text')
            )
        )
        loop.run_until_complete(
            data_svc.store(
                Obfuscator(name='base64',
                           description='Obfuscates commands in base64',
                           module='app.obfuscators.base64_basic')
            )
        )
        if run_vue_server:
            loop.run_until_complete(start_vue_dev_server())
        logging.info("All systems ready.")
        print_rich_banner()
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(
            services.get("app_svc").teardown(main_config_file=args.environment)
        )
    finally:
        # Cancel all pending tasks before shutdown to avoid resource leaks
        # and "Task was destroyed but it is pending!" warnings.
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        asyncio.set_event_loop(None)


def init_swagger_documentation(app):
    """Makes swagger documentation available at /api/docs for any endpoints
    marked for aiohttp_apispec documentation.
    """
    warnings.filterwarnings("ignore", message="Multiple schemas resolved to the name")
    aiohttp_apispec.setup_aiohttp_apispec(
        app=app,
        title="Caldera",
        version=version.get_version(),
        swagger_path="/api/docs",
        url="/api/docs/swagger.json",
        static_path="/static/swagger",
    )
    app.middlewares.append(apispec_request_validation_middleware)
    app.middlewares.append(validation_middleware)


async def enable_cors(request, response):
    response.headers["Access-Control-Allow-Origin"] = (
        "http://" + args.uiDevHost + ":3000"
    )
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
    )
    response.headers["Access-Control-Allow-Headers"] = (
        "Access-Control-Allow-Headers, Origin, Accept, X-Requested-With, Content-Type, Access-Control-Request-Method, Access-Control-Request-Headers"
    )


async def start_vue_dev_server():
    proc = await asyncio.create_subprocess_exec(
        "npm", "run", "dev", stdout=sys.stdout, stderr=sys.stderr, cwd=MAGMA_PATH
    )
    logging.info("VueJS development server started (PID %s).", proc.pid)


def _get_parser():

    def list_str(values):
        return values.split(",")

    parser = argparse.ArgumentParser(
        description=get_ascii_banner(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-E",
        "--environment",
        required=False,
        default="local",
        help="Select an env. file to use",
    )
    parser.add_argument(
        "-l",
        "--log",
        dest="logLevel",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
        default="INFO",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        required=False,
        default=False,
        help="remove object_store on start",
    )
    parser.add_argument(
        "-P",
        "--plugins",
        required=False,
        default=os.listdir("plugins"),
        help="Start up with a single plugin",
        type=list_str,
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        required=False,
        default=False,
        help='Start caldera with insecure default config values. Equivalent to "-E default".',
    )
    parser.add_argument(
        "--uidev",
        dest="uiDevHost",
        help="Start VueJS dev server for front-end alongside the caldera server. Provide hostname, i.e. localhost.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        required=False,
        default=False,
        help="Build the VueJS front-end to serve it from the caldera server.",
    )
    return parser


if __name__ == "__main__":
    sys.path.append("")

    parser = _get_parser()
    args = parser.parse_args()
    setup_logger(getattr(logging, args.logLevel))

    if args.insecure:
        logging.warning(
            "[orange_red1]--insecure flag set. Caldera will use the default user accounts in default.yml config file.[/orange_red1]"
        )
        args.environment = "default"
    elif args.environment == "local":
        ensure_local_config()

    main_config_path = "conf/%s.yml" % args.environment
    BaseWorld.apply_config("main", BaseWorld.strip_yml(main_config_path)[0], apply_hash=True,
                           overwrite_path=main_config_path)
    logging.info("Using main config from %s" % main_config_path)
    BaseWorld.apply_config("agents", BaseWorld.strip_yml("conf/agents.yml")[0])
    BaseWorld.apply_config("payloads", BaseWorld.strip_yml("conf/payloads.yml")[0])

    data_svc = DataService()
    knowledge_svc = KnowledgeService()
    contact_svc = ContactService()
    planning_svc = PlanningService(
        global_variable_owners=[
            Executor,
            Agent,
            Link,
            AppConfigGlobalVariableIdentifier,
        ]
    )
    rest_svc = RestService()
    auth_svc = AuthService()
    file_svc = FileSvc()
    learning_svc = LearningService()
    event_svc = EventService()

    def _get_size_mb(config_key, default):
        try:
            val = int(BaseWorld.get_config(config_key))
            return val if val > 0 else default
        except (TypeError, ValueError):
            return default

    app_svc = AppService(
        application=web.Application(
            client_max_size=_get_size_mb('client_max_size_mb', 1) * 1024 * 1024,
            middlewares=[pass_option_middleware]
        )
    )
    app_svc.register_subapp("/api/v2", app.api.v2.make_app(
        app_svc.get_services(),
        upload_max_size_mb=_get_size_mb('api_upload_max_size_mb', 100)
    ))
    init_swagger_documentation(app_svc.application)
    if args.uiDevHost:
        if not os.path.exists(f"{MAGMA_PATH}/dist") and (os.path.exists(f"{MAGMA_PATH}") and len(os.listdir(MAGMA_PATH)) > 0):
            logging.info("Building VueJS front-end.")
            subprocess.run(["npm", "run", "build"], cwd=MAGMA_PATH, check=True)
            logging.info("VueJS front-end build complete.")
        else:
            logging.warning(
                f"[bright_yellow]The `--uidev` flag was supplied, but the Caldera v5 Vue UI is not present."
                f" The Vue UI should be located in {MAGMA_PATH}. Use `--recursive` when cloning Caldera."
                f" Flag ignored; continuing with default server startup.[/bright_yellow]"
            )
            args.uiDevHost = False
        app_svc.application.on_response_prepare.append(enable_cors)

    if args.build:
        if os.path.exists(f"{MAGMA_PATH}") and len(os.listdir(MAGMA_PATH)) > 0:
            logging.info("Building VueJS front-end.")
            subprocess.run(["npm", "install"], cwd=MAGMA_PATH, check=True)
            subprocess.run(["npm", "run", "build"], cwd=MAGMA_PATH, check=True)
            logging.info("VueJS front-end build complete.")
        else:
            logging.warning(
                f"[bright_yellow]The `--build` flag was supplied, but the Caldera v5 Vue UI is not present."
                f" The Vue UI should be located in {MAGMA_PATH}. Use `--recursive` when cloning Caldera.[/bright_yellow]"
            )
    else:
        if os.path.exists(f"{MAGMA_PATH}") and len(os.listdir(MAGMA_PATH)) > 0 and not os.path.exists(f"{MAGMA_PATH}/dist"):
            logging.warning(
                "[bright_yellow]Caldera v5 Vue UI source detected, but not built components, and `--build` flag not supplied."
                " If attempting to start Caldera v5 with the UI for the first time, the `--build` flag must be"
                " supplied to trigger the building of the Vue source components.[/bright_yellow]"
            )
        elif not os.path.exists(f"{MAGMA_PATH}/dist"):
            logging.warning(
                f"[bright_yellow]Built Caldera v5 Vue components not detected, and `--build` flag not supplied."
                f" If attempting to start Caldera v5 with the UI for the first time, the `--build` flag and"
                f" Magma plugin (located in {MAGMA_PATH}) must be supplied to trigger the building of the Vue source components.[/bright_yellow]"
            )

    if args.fresh:
        logging.info(
            "[green]Fresh startup: resetting server data. See %s directory for data backups.[/green]",
            DATA_BACKUP_DIR,
        )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(data_svc.destroy())
            loop.run_until_complete(knowledge_svc.destroy())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            asyncio.set_event_loop(None)

    run_tasks(services=app_svc.get_services(), run_vue_server=args.uiDevHost)
