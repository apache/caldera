import pytest

from app.objects.secondclass.c_fact import Fact
from app.utility.base_parser import BaseParser


@pytest.fixture
def mock_parser():
    return BaseParser(dict(
        mappers=None,
        used_facts=[
            Fact('trait1', value='value1'),
            Fact('trait2', value='value2')
        ],
        source_facts=None
    ))


class TestBaseParser:
    def test_set_value(self, mock_parser):
        assert len(mock_parser.used_facts) == 2
        assert BaseParser.set_value('', 'testmatch', mock_parser.used_facts) is None
        assert BaseParser.set_value('testsearch', 'testmatch', mock_parser.used_facts) == 'testmatch'
        assert BaseParser.set_value('trait2', 'testmatch', mock_parser.used_facts) == 'value2'

    def test_parsing(self):
        # Email
        assert BaseParser.email('notanemail') == []
        assert BaseParser.email('test@example.com') == ['test@example.com']
        assert BaseParser.email('blah test@example.com blah blah test2@example2.org blah') == ['test@example.com', 'test2@example2.org']

        # Filename
        assert BaseParser.filename('noextension') == []
        assert BaseParser.filename('test.xml') == ['test.xml']
        assert BaseParser.filename('blah blah test2.png blah test.xml blahblah') == ['test2.png', 'test.xml']

        # Line splitting
        assert BaseParser.line('singleline') == ['singleline']
        assert BaseParser.line('two\nlines') == ['two', 'lines']
        assert BaseParser.line('three\r\nline\r\nexample') == ['three', 'line', 'example']

        # IP address
        assert BaseParser.ip('notanip') == []
        assert BaseParser.ip('not.an.ip.address') == []
        assert BaseParser.ip('1.2.3') == []
        assert BaseParser.ip('1.2.3.4') == ['1.2.3.4']
        assert BaseParser.ip('blah 1.2.3.4 1.2. 1.2.3. 5.6.7.8 blah') == ['1.2.3.4', '5.6.7.8']

        # Broadcast
        assert BaseParser.broadcastip('notbroadcast') == []
        assert BaseParser.broadcastip('broadcast 1.2.3.4') == ['1.2.3.4']

        # JSON
        assert BaseParser.load_json('{"a":"b"}') == dict(a='b')
        assert BaseParser.load_json('{"a":"b"') is None  # malformed
