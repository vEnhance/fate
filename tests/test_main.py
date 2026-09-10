import argparse
import tomllib
from pathlib import Path

import pytest

from fate.main import _parse_duration, _parse_tasks, _venv_setting, init_repo


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
        ("1", 1.0),
        ("5", 5.0),
        ("2.5", 2.5),
    ],
)
def test_parse_duration_valid(s, expected):
    assert _parse_duration(s) == pytest.approx(expected)


@pytest.mark.parametrize("s", ["", "abc", "1x", "ms", "1 s", "1S"])
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


# --- _venv_setting ---


def test_venv_setting_active_inside_repo(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    assert _venv_setting(tmp_path, str(venv)) == ".venv"


def test_venv_setting_active_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    venv = tmp_path / ".venvs" / "myenv"
    venv.mkdir(parents=True)
    assert _venv_setting(tmp_path / "repo", str(venv)) == "~/.venvs/myenv"


def test_venv_setting_active_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    venv = tmp_path / "opt" / "env"
    venv.mkdir(parents=True)
    assert _venv_setting(tmp_path / "repo", str(venv)) == str(venv)


def test_venv_setting_falls_back_to_in_tree_venv(tmp_path):
    (tmp_path / ".venv").mkdir()
    assert _venv_setting(tmp_path, None) == ".venv"


def test_venv_setting_none(tmp_path):
    assert _venv_setting(tmp_path, None) is None


# --- init_repo ---


@pytest.fixture(autouse=True)
def no_active_venv(monkeypatch):
    """The test suite itself runs inside a virtualenv; init_repo must not see it."""
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def _config(faterc: Path) -> dict:
    with open(faterc, "rb") as f:
        return tomllib.load(f)


def test_init_repo_creates_faterc(repo):
    root = Path(repo.working_tree_dir)
    faterc = init_repo(root)
    assert faterc == root / ".faterc"
    data = _config(faterc)
    assert data["config"]["branch"] == "main"
    assert data["actions"]["pull"]["enabled"] is False
    assert "uv" not in data["actions"]


def test_init_repo_visible(repo):
    root = Path(repo.working_tree_dir)
    assert init_repo(root, visible=True) == root / "faterc"


def test_init_repo_enables_pull_and_push_with_upstream(repo_with_upstream):
    root = Path(repo_with_upstream.working_tree_dir)
    data = _config(init_repo(root))
    assert data["actions"]["pull"]["enabled"] is True
    assert data["actions"]["push"]["enabled"] is True


def test_init_repo_adds_uv_and_prek_actions(repo):
    root = Path(repo.working_tree_dir)
    (root / "uv.lock").write_text("")
    (root / "prek.toml").write_text("")
    (root / ".venv").mkdir()
    data = _config(init_repo(root))
    assert data["config"]["venv"] == ".venv"
    assert data["actions"]["uv"]["enabled"] is True
    assert data["actions"]["prek"]["enabled"] is True


def test_init_repo_uv_disabled_without_venv(repo):
    root = Path(repo.working_tree_dir)
    (root / "uv.lock").write_text("")
    data = _config(init_repo(root))
    assert data["actions"]["uv"]["enabled"] is False
    assert "venv" not in data["config"]


def test_init_repo_records_active_virtualenv(repo, monkeypatch, tmp_path):
    home = tmp_path.parent / f"{tmp_path.name}-home"
    monkeypatch.setenv("HOME", str(home))
    venv = home / ".venvs" / "myenv"
    venv.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    root = Path(repo.working_tree_dir)
    (root / "uv.lock").write_text("")
    data = _config(init_repo(root))
    assert data["config"]["venv"] == "~/.venvs/myenv"
    assert data["actions"]["uv"]["enabled"] is True


def test_init_repo_rejects_non_repo(tmp_path):
    with pytest.raises(ValueError, match="not the root of a git repository"):
        init_repo(tmp_path)


def test_init_repo_rejects_existing_faterc(repo):
    root = Path(repo.working_tree_dir)
    (root / ".faterc").write_text("")
    with pytest.raises(ValueError, match="already exists"):
        init_repo(root)


def test_init_repo_rejects_bare_dot_git_directory(tmp_path):
    """A .git directory that isn't actually a repository."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="not a valid git repository"):
        init_repo(tmp_path)
