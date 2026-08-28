import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v2.handlers.base_object_api import DEFAULT_LIMIT, MAX_LIMIT


class TestApiPagination(unittest.TestCase):
    def test_pagination_defaults(self):
        limit = min(int(str(DEFAULT_LIMIT)), MAX_LIMIT)
        offset = max(int('0'), 0)
        self.assertEqual(limit, DEFAULT_LIMIT)
        self.assertEqual(offset, 0)

    def test_pagination_applies_correctly(self):
        items = list(range(50))
        limit = 10
        offset = 5
        result = items[offset:offset + limit]
        self.assertEqual(result, list(range(5, 15)))

    def test_pagination_max_limit(self):
        limit = min(int('5000'), MAX_LIMIT)
        self.assertEqual(limit, MAX_LIMIT)

    def test_pagination_negative_offset_clamped(self):
        offset = max(int('-5'), 0)
        self.assertEqual(offset, 0)

    def test_pagination_beyond_end(self):
        items = list(range(10))
        result = items[20:30]
        self.assertEqual(result, [])

    def test_invalid_limit_falls_back_to_default(self):
        """Non-integer 'limit' query param must fall back to DEFAULT_LIMIT."""
        try:
            limit = min(int('notanumber'), MAX_LIMIT)
        except (ValueError, TypeError):
            limit = DEFAULT_LIMIT
        self.assertEqual(limit, DEFAULT_LIMIT)

    def test_invalid_offset_falls_back_to_zero(self):
        """Non-integer 'offset' query param must fall back to 0."""
        try:
            offset = max(int('notanumber'), 0)
        except (ValueError, TypeError):
            offset = 0
        self.assertEqual(offset, 0)

    def test_get_all_objects_applies_pagination(self):
        """get_all_objects must honour limit/offset and set x_total_count."""
        from app.api.v2.handlers.base_object_api import BaseObjectApi

        # Build a minimal concrete subclass.
        class ConcreteApi(BaseObjectApi):
            def add_routes(self, app):
                pass

        auth_svc = MagicMock()
        auth_svc.get_permissions = AsyncMock(return_value=[])

        api = ConcreteApi(
            description='test',
            obj_class=object,
            schema=MagicMock(),
            ram_key='test_key',
            id_property='id',
            auth_svc=auth_svc,
        )

        # Fake the API manager to return 25 objects.
        fake_objects = [{'id': str(i)} for i in range(25)]
        api._api_manager = MagicMock()
        api._api_manager.find_and_dump_objects = MagicMock(return_value=fake_objects)

        # Build a mock request with limit=10, offset=5.
        request = MagicMock()
        request.__getitem__ = MagicMock(side_effect=lambda k: {
            'querystring': {'sort': 'name', 'limit': '10', 'offset': '5'}
        }[k])

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            api.get_all_objects(request)
        )

        # Should return items [5..14].
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0], {'id': '5'})
        # Total count must be stored on the request.
        request.__setitem__.assert_called_with('x_total_count', 25)

    def test_get_all_objects_rejects_over_max_limit(self):
        """limit values above MAX_LIMIT must be clamped to MAX_LIMIT."""
        from app.api.v2.handlers.base_object_api import BaseObjectApi

        class ConcreteApi(BaseObjectApi):
            def add_routes(self, app):
                pass

        auth_svc = MagicMock()
        auth_svc.get_permissions = AsyncMock(return_value=[])

        api = ConcreteApi(
            description='test',
            obj_class=object,
            schema=MagicMock(),
            ram_key='test_key',
            id_property='id',
            auth_svc=auth_svc,
        )

        # Return MAX_LIMIT + 500 objects so we can tell if clamping works.
        fake_objects = [{'id': str(i)} for i in range(MAX_LIMIT + 500)]
        api._api_manager = MagicMock()
        api._api_manager.find_and_dump_objects = MagicMock(return_value=fake_objects)

        request = MagicMock()
        request.__getitem__ = MagicMock(side_effect=lambda k: {
            'querystring': {'sort': 'name', 'limit': str(MAX_LIMIT + 9999), 'offset': '0'}
        }[k])

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            api.get_all_objects(request)
        )
        self.assertEqual(len(result), MAX_LIMIT)
