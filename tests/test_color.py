from unittest.mock import patch

from fate.color import _c


def test_c_no_tty():
    with patch("sys.stdout.isatty", return_value=False):
        assert _c("32", "hello") == "hello"


def test_c_tty():
    with patch("sys.stdout.isatty", return_value=True):
        assert _c("32", "hello") == "\033[32mhello\033[0m"


def test_c_tty_bold():
    with patch("sys.stdout.isatty", return_value=True):
        assert _c("1;32", "hi") == "\033[1;32mhi\033[0m"
