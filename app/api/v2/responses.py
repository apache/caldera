import json
import logging

from aiohttp import web

_log = logging.getLogger('caldera')

_GENERIC_ERROR_BODY = json.dumps({'error': 'An internal server error occurred'})
from json import JSONDecodeError

from aiohttp.web_exceptions import HTTPUnprocessableEntity
from marshmallow.exceptions import ValidationError

from app.api.v2 import errors
from app.api.v2.schemas.error_schemas import JsonHttpErrorSchema


class JsonHttpErrorResponse:
    """Base class for json formatted versions of aiohttp responses."""

    def __init__(self, error, details=None, **kwargs):
        kwargs['content_type'] = 'application/json'
        kwargs['text'] = JsonHttpErrorSchema.serialize(error, details)
        super().__init__(**kwargs)


class JsonHttpBadRequest(JsonHttpErrorResponse, web.HTTPBadRequest):
    """An HTTP 400 response with a json formatted body."""


class JsonHttpForbidden(JsonHttpErrorResponse, web.HTTPForbidden):
    """An HTTP 403 response with a json formatted body."""
    pass


class JsonHttpNotFound(JsonHttpErrorResponse, web.HTTPNotFound):
    """An HTTP 404 response with a json formatted body."""


@web.middleware
async def apispec_request_validation_middleware(request, handler):
    """Middleware to handle errors thrown by schema validation

    Must be added before `validation_middleware`"""
    try:
        return await handler(request)
    except TypeError as ex:
        # ex: Schema `post_load()` instantiates an object, but required
        #   argument is missing
        raise JsonHttpBadRequest(
            error='Error parsing JSON',
            details=str(ex)
        )
    except AttributeError as ex:
        # ex: JSON contains attribute that does not exist in Schema
        # Or any other AttributeError...
        raise JsonHttpBadRequest(
            error='AttributeError',
            details=str(ex)
        )
    except ValidationError as ex:
        # ex: List of objects sent when single object expected
        formatted_message = json.dumps({"json": ex.messages}, indent=2)
        raise HTTPUnprocessableEntity(
            text=formatted_message
        )
    except JSONDecodeError as ex:
        raise JsonHttpBadRequest(
            error='Unexpected error occurred while parsing json',
            details=str(ex)
        )


@web.middleware
async def internal_error_middleware(request, handler):
    """Catch unhandled exceptions and return a sanitised error response.

    4xx HTTPExceptions are re-raised as-is so aiohttp handles them normally.
    5xx HTTPExceptions are sanitised (body replaced with a generic message)
    while **preserving the original status code and any important headers**
    (e.g. ``Retry-After`` on a 503).  Unhandled non-HTTP exceptions become 500s.
    """
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status_code < 500:
            raise  # 4xx: let aiohttp handle normally, details are safe to expose
        # 5xx: log and replace body but keep the original status code
        _log.exception('HTTP %d in request handler', exc.status_code)
        return web.Response(
            status=exc.status_code,
            content_type='application/json',
            text=_GENERIC_ERROR_BODY,
            headers=exc.headers,
        )
    except Exception:
        _log.exception('Unhandled exception in request handler')
        return web.Response(
            status=500,
            content_type='application/json',
            text=_GENERIC_ERROR_BODY,
        )


@web.middleware
async def json_request_validation_middleware(request, handler):
    """Middleware that converts json decoding and marshmallow validation
    errors into 400 responses w/ json bodies.
    """
    try:
        return await handler(request)
    except errors.DataValidationError as ex:
        raise JsonHttpBadRequest(
            error=str(ex),
            details={ex.name: ex.value}
        )
    except errors.RequestValidationError as ex:
        raise JsonHttpBadRequest('Received invalid json', details=ex.errors)
    except errors.RequestUnparsableJsonError:
        raise JsonHttpBadRequest('Unexpected error occurred while parsing json')
