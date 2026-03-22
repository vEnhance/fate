import pytest

from fate.run import _find_faterc_files, _iter_repos, find_faterc, venv_env

# --- find_faterc ---


def test_find_faterc_dotfile(tmp_path):
    (tmp_path / ".faterc").write_text("")
    assert find_faterc(tmp_path) == tmp_path / ".faterc"


def test_find_faterc_visible(tmp_path):
    (tmp_path / "faterc").write_text("")
    assert find_faterc(tmp_path) == tmp_path / "faterc"


def test_find_faterc_both_prefers_visible(tmp_path, capsys):
    (tmp_path / ".faterc").write_text("")
    (tmp_path / "faterc").write_text("")
    assert find_faterc(tmp_path) == tmp_path / "faterc"
    assert "warning" in capsys.readouterr().err


def test_find_faterc_none(tmp_path):
    assert find_faterc(tmp_path) is None


# --- venv_env ---


def test_venv_env_absolute(tmp_path):
    env = venv_env(str(tmp_path), tmp_path)
    assert str(tmp_path / "bin") in env["PATH"]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path)


def test_venv_env_relative(tmp_path):
    env = venv_env(".venv", tmp_path)
    assert str(tmp_path / ".venv" / "bin") in env["PATH"]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")


def test_venv_env_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env = venv_env("~/.venvs/myenv", tmp_path)
    assert str(tmp_path / ".venvs" / "myenv" / "bin") in env["PATH"]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venvs" / "myenv")


def test_venv_env_prepends_path(tmp_path):
    env = venv_env(str(tmp_path), tmp_path)
    assert env["PATH"].startswith(str(tmp_path / "bin"))


# --- _find_faterc_files / _iter_repos (forcing os.walk path) ---


@pytest.fixture(autouse=True)
def no_fd(monkeypatch):
    """Force os.walk path by hiding fd."""
    monkeypatch.setattr("shutil.which", lambda _: None)


def test_find_faterc_files_empty(tmp_path):
    assert _find_faterc_files(tmp_path) == []


def test_find_faterc_files_finds_dotfile(tmp_path):
    (tmp_path / ".faterc").write_text("")
    assert _find_faterc_files(tmp_path) == [tmp_path / ".faterc"]


def test_find_faterc_files_finds_visible(tmp_path):
    (tmp_path / "faterc").write_text("")
    assert _find_faterc_files(tmp_path) == [tmp_path / "faterc"]


def test_find_faterc_files_both_in_same_dir(tmp_path):
    (tmp_path / ".faterc").write_text("")
    (tmp_path / "faterc").write_text("")
    files = _find_faterc_files(tmp_path)
    assert tmp_path / ".faterc" in files
    assert tmp_path / "faterc" in files


def test_find_faterc_files_nested(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / ".faterc").write_text("")
    (tmp_path / "b" / "faterc").write_text("")
    files = _find_faterc_files(tmp_path)
    assert tmp_path / "a" / ".faterc" in files
    assert tmp_path / "b" / "faterc" in files


def test_iter_repos_deduplicates(tmp_path):
    (tmp_path / ".faterc").write_text("")
    (tmp_path / "faterc").write_text("")
    repos = _iter_repos(tmp_path)
    assert repos.count(tmp_path) == 1


def test_iter_repos_multiple(tmp_path):
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        (d / ".faterc").write_text("")
    repos = _iter_repos(tmp_path)
    assert len(repos) == 3
    assert tmp_path / "a" in repos
