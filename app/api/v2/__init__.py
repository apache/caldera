from aiohttp import web

DEFAULT_UPLOAD_MAX_SIZE_MB = 25

# Request body limit, in bytes, for the routes of a sub-application.
CLIENT_MAX_SIZE = web.AppKey('client_max_size', int)


@web.middleware
async def request_size_middleware(request, handler):
    """Apply the request body limit of the sub-application that serves the route.

    aiohttp creates every request with the root application's client_max_size, so a
    limit given to a sub-application has no effect on its own. Install this on the
    root application ahead of any middleware that reads the request body.
    """
    for app in reversed(request.match_info.apps):
        limit = app.get(CLIENT_MAX_SIZE)
        if limit:
            if limit != request.client_max_size:
                request = request.clone(client_max_size=limit)
            break
    return await handler(request)


def make_app(services, upload_max_size_mb=DEFAULT_UPLOAD_MAX_SIZE_MB):
    from .responses import json_request_validation_middleware
    from .security import authentication_required_middleware_factory, pass_option_middleware

    try:
        max_size = int(upload_max_size_mb)
        max_size = max_size if max_size > 0 else DEFAULT_UPLOAD_MAX_SIZE_MB
    except (TypeError, ValueError):
        max_size = DEFAULT_UPLOAD_MAX_SIZE_MB

    app = web.Application(
        client_max_size=max_size * 1024 * 1024,
        middlewares=[
            pass_option_middleware,
            authentication_required_middleware_factory(services['auth_svc']),
            json_request_validation_middleware
        ]
    )
    app[CLIENT_MAX_SIZE] = max_size * 1024 * 1024

    from .handlers.health_api import HealthApi
    HealthApi(services).add_routes(app)

    from .handlers.config_api import ConfigApi
    ConfigApi(services).add_routes(app)

    from .handlers.planner_api import PlannerApi
    PlannerApi(services).add_routes(app)

    from .handlers.ability_api import AbilityApi
    AbilityApi(services).add_routes(app)

    from .handlers.plugins_api import PluginApi
    PluginApi(services).add_routes(app)

    from .handlers.fact_source_api import FactSourceApi
    FactSourceApi(services).add_routes(app)

    from .handlers.objective_api import ObjectiveApi
    ObjectiveApi(services).add_routes(app)

    from .handlers.adversary_api import AdversaryApi
    AdversaryApi(services).add_routes(app)

    from .handlers.agent_api import AgentApi
    AgentApi(services).add_routes(app)

    from .handlers.schedule_api import ScheduleApi
    ScheduleApi(services).add_routes(app)

    from .handlers.operation_api import OperationApi
    OperationApi(services).add_routes(app)

    from .handlers.obfuscator_api import ObfuscatorApi
    ObfuscatorApi(services).add_routes(app)

    from .handlers.fact_api import FactApi
    FactApi(services).add_routes(app)

    from .handlers.contact_api import ContactApi
    ContactApi(services).add_routes(app)

    from .handlers.payload_api import PayloadApi
    PayloadApi(services).add_routes(app)

    return app
