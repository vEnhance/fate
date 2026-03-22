import pytest

from fate.prek import prek_revs, prek_up_to_date, prek_update_cache

SAMPLE = """\
[[repos]]
repo = "builtin"
hooks = [{ id = "check-json" }]

[[repos]]
repo = "https://github.com/astral-sh/ruff-pre-commit"
rev = "v0.15.7"
hooks = [{ id = "ruff-check" }]

[[repos]]
repo = "https://github.com/codespell-project/codespell"
rev = "v2.4.2"
hooks = [{ id = "codespell" }]

[[repos]]
repo = "local"
hooks = [{ id = "my-hook", name = "my", entry = "echo", language = "system" }]
"""

# prek.toml with multiline inline tables (TOML 1.1)
MULTILINE = """\
[[repos]]
repo = "https://github.com/astral-sh/ruff-pre-commit"
rev = "v0.15.7"
hooks = [
  {
    id = "ruff-check",
    args = ["--fix"]
  }
]
"""


@pytest.fixture
def prek_toml(tmp_path):
    p = tmp_path / "prek.toml"
    p.write_text(SAMPLE)
    return p


def testprek_revs(prek_toml):
    assert prek_revs(prek_toml) == {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.15.7",
        "https://github.com/codespell-project/codespell": "v2.4.2",
    }


def testprek_revs_ignores_builtin_and_local(prek_toml):
    revs = prek_revs(prek_toml)
    assert "builtin" not in revs
    assert "local" not in revs


def testprek_revs_multiline_inline_tables(tmp_path):
    p = tmp_path / "prek.toml"
    p.write_text(MULTILINE)
    assert prek_revs(p) == {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.15.7",
    }


def test_prek_up_to_date_all_cached(prek_toml):
    cache = {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.15.7",
        "https://github.com/codespell-project/codespell": "v2.4.2",
    }
    assert prek_up_to_date(prek_toml, cache) is True


def test_prek_up_to_date_missing_entry(prek_toml):
    cache = {"https://github.com/astral-sh/ruff-pre-commit": "v0.15.7"}
    assert prek_up_to_date(prek_toml, cache) is False


def test_prek_up_to_date_stale_rev(prek_toml):
    cache = {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.14.0",
        "https://github.com/codespell-project/codespell": "v2.4.2",
    }
    assert prek_up_to_date(prek_toml, cache) is False


def test_prek_up_to_date_empty_cache(prek_toml):
    assert prek_up_to_date(prek_toml, {}) is False


def test_prek_update_cache(prek_toml):
    cache: dict[str, str] = {}
    prek_update_cache(prek_toml, cache)
    assert cache["https://github.com/astral-sh/ruff-pre-commit"] == "v0.15.7"
    assert cache["https://github.com/codespell-project/codespell"] == "v2.4.2"


def test_prek_update_cache_overwrites(prek_toml):
    cache = {"https://github.com/astral-sh/ruff-pre-commit": "v0.14.0"}
    prek_update_cache(prek_toml, cache)
    assert cache["https://github.com/astral-sh/ruff-pre-commit"] == "v0.15.7"
