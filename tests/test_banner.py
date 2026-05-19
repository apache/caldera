import os

from unittest import mock
from unittest.mock import patch

from app.ascii_banner import get_ascii_banner, print_rich_banner

WANT_BANNER = """
 ██████╗ █████╗ ██╗     ██████╗ ███████╗██████╗  █████╗
██╔════╝██╔══██╗██║     ██╔══██╗██╔════╝██╔══██╗██╔══██╗
██║     ███████║██║     ██║  ██║█████╗  ██████╔╝███████║
██║     ██╔══██║██║     ██║  ██║██╔══╝  ██╔══██╗██╔══██║
╚██████╗██║  ██║███████╗██████╔╝███████╗██║  ██║██║  ██║
 ╚═════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
"""


WANT_BANNER_SECTION_1 = "\n\
 ██████╗ █████╗ ██╗     ██████╗ ███████╗██████╗  █████╗\n\
██╔════╝██╔══██╗██║     ██╔══██╗██╔════╝██╔══██╗██╔══██╗\n\
"


WANT_BANNER_SECTION_2 = "\
██║     ███████║██║     ██║  ██║█████╗  ██████╔╝███████║\n\
██║     ██╔══██║██║     ██║  ██║██╔══╝  ██╔══██╗██╔══██║\n\
"


WANT_BANNER_SECTION_3 = "\
╚██████╗██║  ██║███████╗██████╔╝███████╗██║  ██║██║  ██║\n\
 ╚═════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝\n\
"


class TestBanner:
    @mock.patch.dict(os.environ, {'NO_COLOR': '0'}, clear=True)
    def test_banner_with_color(self):
        with patch('app.ascii_banner.rich_print', return_value=None) as mock_rich_print:
            print_rich_banner()
        mock_rich_print.assert_called_once_with(f'[blue]{WANT_BANNER_SECTION_1}[/blue][purple]{WANT_BANNER_SECTION_2}[/purple][red]{WANT_BANNER_SECTION_3}[/red]')

        assert get_ascii_banner() == f'\x1b[38;5;20m{WANT_BANNER_SECTION_1}\x1b[38;5;92m{WANT_BANNER_SECTION_2}\x1b[38;5;1m{WANT_BANNER_SECTION_3}\033[0m'

    @mock.patch.dict(os.environ, {'NO_COLOR': '1'}, clear=True)
    def test_banner_without_color(self):
        with patch('app.ascii_banner.rich_print', return_value=None) as mock_rich_print:
            print_rich_banner()
        mock_rich_print.assert_called_once_with(f'{WANT_BANNER_SECTION_1}{WANT_BANNER_SECTION_2}{WANT_BANNER_SECTION_3}')

        assert get_ascii_banner() == WANT_BANNER
