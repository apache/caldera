import unittest
from unittest.mock import MagicMock


class TestIndexedLookups(unittest.TestCase):
    def test_index_and_lookup(self):
        from app.service.data_svc import DataService
        svc = DataService.__new__(DataService)
        svc._index = {}
        svc.log = MagicMock()
        svc.ram = {'agents': []}
        svc.schema = {'agents': []}

        obj = MagicMock()
        obj.paw = 'testpaw'
        obj.match = lambda m: m is None or all(getattr(obj, k, None) == v for k, v in m.items())
        obj.store = lambda ram: (ram['agents'].append(obj) or obj) if obj not in ram['agents'] else obj

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(svc.store(obj))
            result = loop.run_until_complete(svc.locate('agents', dict(paw='testpaw')))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].paw, 'testpaw')

            # Non-existent lookup
            result2 = loop.run_until_complete(svc.locate('agents', dict(paw='nonexistent')))
            self.assertEqual(len(result2), 0)
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
