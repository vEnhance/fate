import argparse

import pytest

from fate.main import _parse_duration


@pytest.mark.parametrize(
    "s, expected",
    [
        ("1s", 1.0),
        ("500ms", 0.5),
        ("2m", 120.0),
        ("1h", 3600.0),
        ("1.5s", 1.5),
        ("0ms", 0.0),
        ("90m", 5400.0),
    ],
)
def test_parse_duration_valid(s, expected):
    assert _parse_duration(s) == pytest.approx(expected)


@pytest.mark.parametrize("s", ["", "abc", "1x", "1", "ms", "1 s", "1S"])
def test_parse_duration_invalid(s):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_duration(s)
