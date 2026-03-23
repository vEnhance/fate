import argparse

import pytest

from fate.main import _parse_duration, _parse_tasks


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


# --- _parse_tasks ---


def test_parse_tasks_none_returns_none():
    assert _parse_tasks(None) is None


def test_parse_tasks_empty_list_returns_empty_set():
    assert _parse_tasks([]) == set()


def test_parse_tasks_single():
    assert _parse_tasks(["pull"]) == {"pull"}


def test_parse_tasks_comma_separated():
    assert _parse_tasks(["pull,push"]) == {"pull", "push"}


def test_parse_tasks_repeated_args():
    assert _parse_tasks(["pull", "push"]) == {"pull", "push"}


def test_parse_tasks_comma_and_repeated_equivalent():
    assert _parse_tasks(["pull,push"]) == _parse_tasks(["pull", "push"])


def test_parse_tasks_mixed():
    assert _parse_tasks(["pull,uv", "push"]) == {"pull", "uv", "push"}


def test_parse_tasks_strips_whitespace():
    assert _parse_tasks(["pull, push"]) == {"pull", "push"}


def test_parse_tasks_unknown_warns(capsys):
    result = _parse_tasks(["bogus"])
    assert result is not None and "bogus" in result
    assert "Warning" in capsys.readouterr().err
