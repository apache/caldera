import unittest
import marshmallow as ma


class TestSchema(ma.Schema):
    name = ma.fields.String()
    class Meta:
        unknown = ma.EXCLUDE


class TestMarshmallowWarning(unittest.TestCase):
    def test_warning_on_unknown_fields(self):
        from app.api.v2.handlers.base_object_api import load_schema_with_warning
        schema = TestSchema()
        with self.assertLogs('caldera.schema', level='WARNING') as log:
            result = load_schema_with_warning(schema, {'name': 'test', 'unknown_field': 'val'})
        self.assertEqual(result['name'], 'test')
        self.assertTrue(any('unknown_field' in msg for msg in log.output))

    def test_no_warning_when_all_known(self):
        from app.api.v2.handlers.base_object_api import load_schema_with_warning
        schema = TestSchema()
        # Should not raise or log
        result = load_schema_with_warning(schema, {'name': 'test'})
        self.assertEqual(result['name'], 'test')


