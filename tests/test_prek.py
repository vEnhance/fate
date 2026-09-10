import pytest

from fate.prek import (
    prek_revs,
    prek_up_to_date,
    prek_update_cache,
    uv_export_command,
    uv_export_output,
)

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


# --- uv_export_command / uv_export_output ---

UV_EXPORT = """\
[[repos]]
repo = "https://github.com/astral-sh/uv-pre-commit"
rev = "0.12.7"
hooks = [{ id = "uv-lock" }, { id = "uv-export" }]
"""

UV_EXPORT_ARGS = """\
[[repos]]
repo = "https://github.com/astral-sh/uv-pre-commit"
rev = "0.12.7"
hooks = [
  { id = "uv-export", args = ["--no-dev", "--output-file=reqs/base.txt"] },
]
"""


def test_uv_export_command_default_args(tmp_path):
    p = tmp_path / "prek.toml"
    p.write_text(UV_EXPORT)
    assert uv_export_command(p) == [
        "uv",
        "export",
        "--frozen",
        "--output-file=requirements.txt",
        "--quiet",
    ]


def test_uv_export_command_custom_args(tmp_path):
    p = tmp_path / "prek.toml"
    p.write_text(UV_EXPORT_ARGS)
    assert uv_export_command(p) == [
        "uv",
        "export",
        "--no-dev",
        "--output-file=reqs/base.txt",
    ]


def test_uv_export_command_absent(prek_toml):
    assert uv_export_command(prek_toml) is None


def test_uv_export_output_default():
    assert uv_export_output(["uv", "export", "--frozen"]) == "requirements.txt"


def test_uv_export_output_equals_form():
    assert uv_export_output(["uv", "export", "--output-file=reqs.txt"]) == "reqs.txt"


def test_uv_export_output_separate_arg():
    assert uv_export_output(["uv", "export", "-o", "reqs.txt"]) == "reqs.txt"
