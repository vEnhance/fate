from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fate.run import (
    RepoEntry,
    _find_faterc_files,
    _find_git_repos,
    find_faterc,
    iter_all_repos,
    iter_repos,
    run_repo,
    venv_env,
)

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
    assert "Warning" in capsys.readouterr().err


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


# --- _find_faterc_files / iter_repos (forcing os.walk path) ---


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


def testiter_repos_deduplicates(tmp_path):
    (tmp_path / ".faterc").write_text("")
    (tmp_path / "faterc").write_text("")
    repos = iter_repos(tmp_path)
    paths = [e.path for e in repos]
    assert paths.count(tmp_path) == 1


def testiter_repos_multiple(tmp_path):
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        (d / ".faterc").write_text("")
    repos = iter_repos(tmp_path)
    paths = [e.path for e in repos]
    assert len(paths) == 3
    assert tmp_path / "a" in paths


# --- run_repo: task filtering, smart pull, dirty state ---


def _write_faterc(path: Path, *, pull=False, push=False, branch="main") -> RepoEntry:
    """Write a minimal .faterc and return the corresponding RepoEntry."""
    lines = [f'[config]\nbranch = "{branch}"\n\n[actions]\n']
    if pull:
        lines.append("pull = { enabled = true }\n")
    if push:
        lines.append("push = { enabled = true, verify = true }\n")
    faterc = path / ".faterc"
    faterc.write_text("".join(lines))
    return RepoEntry.from_faterc(path, faterc)


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Patches subprocess.run in fate.run and returns the recorded call arg lists."""
    calls = []

    def _run(args, **kwargs):
        calls.append(list(args))
        m = MagicMock()
        m.returncode = 0
        return m

    monkeypatch.setattr("fate.run.subprocess.run", _run)
    return calls


def _cmds(calls: list) -> list[tuple]:
    return [tuple(c) for c in calls]


# -- task filtering --


def test_exclude_skips_task(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, exclude={"push"})
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("pull" in s for s in cmd_strs)
    assert not any("push" in s for s in cmd_strs)


def test_only_restricts_tasks(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, only={"pull"})
    assert not any("push" in " ".join(c) for c in mock_subprocess)


def test_faterc_disabled_not_run_even_if_in_only(repo, mock_subprocess, monkeypatch):
    """A task disabled in faterc must never run, even if explicitly listed in only."""
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root)  # nothing enabled
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, only={"push"})
    assert not any("push" in " ".join(c) for c in mock_subprocess)


# -- smart pull (no branch switching when only pull is active) --


def test_smart_pull_same_branch(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, only={"pull"})
    cmds = _cmds(mock_subprocess)
    assert ("git", "pull") in cmds
    assert not any("checkout" in " ".join(c) for c in mock_subprocess)


def test_smart_pull_different_branch(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(entry, only={"pull"})
    cmds = _cmds(mock_subprocess)
    assert ("git", "fetch", "origin", "main:main") in cmds
    assert not any("checkout" in " ".join(c) for c in mock_subprocess)


def test_smart_pull_fallback_on_failed_fetch(repo, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    calls = []

    def _run(args, **kwargs):
        calls.append(list(args))
        m = MagicMock()
        m.returncode = 1 if "main:main" in args else 0
        return m

    monkeypatch.setattr("fate.run.subprocess.run", _run)
    run_repo(entry, only={"pull"})
    cmds = _cmds(calls)
    assert ("git", "fetch", "origin", "main:main") in cmds
    assert ("git", "fetch") in cmds
    assert not any("checkout" in " ".join(c) for c in calls)


def test_pull_with_other_tasks_switches_branch(repo, mock_subprocess, monkeypatch):
    """When tasks that need the target branch are active, checkout should happen."""
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(entry)
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("checkout" in s for s in cmd_strs)
    assert any("pull" in s for s in cmd_strs)


# -- dirty state --


def test_dirty_with_pull_fetches(repo, mock_subprocess):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    (root / "README").write_text("dirty")
    run_repo(entry, only={"pull"})
    cmds = _cmds(mock_subprocess)
    assert ("git", "fetch") in cmds
    assert ("git", "pull") not in cmds


def test_dirty_with_branch_task_skips(repo, mock_subprocess, capsys):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, push=True)
    (root / "README").write_text("dirty")
    run_repo(entry, only={"push"})
    assert not mock_subprocess
    assert "dirty" in capsys.readouterr().out.lower()


def test_dirty_no_active_tasks_silent(repo, mock_subprocess, capsys):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root)  # nothing enabled
    (root / "README").write_text("dirty")
    run_repo(entry, only=set())
    assert not mock_subprocess
    out, err = capsys.readouterr()
    assert not out and not err


# --- RepoEntry ---


def test_repo_entry_from_faterc_loads_config(tmp_path):
    faterc = tmp_path / ".faterc"
    faterc.write_text(
        '[config]\nbranch = "dev"\nvenv = ".venv"\n\n[actions]\npull = { enabled = true }\n'
    )
    entry = RepoEntry.from_faterc(tmp_path, faterc)
    assert entry.path == tmp_path
    assert entry.faterc == faterc
    assert entry.branch == "dev"
    assert entry.venv == ".venv"
    assert entry.actions["pull"]["enabled"] is True


def test_repo_entry_from_faterc_defaults(tmp_path):
    faterc = tmp_path / ".faterc"
    faterc.write_text('[config]\nbranch = "main"\n\n[actions]\n')
    entry = RepoEntry.from_faterc(tmp_path, faterc)
    assert entry.branch == "main"
    assert entry.venv is None
    assert entry.actions == {}


def test_repo_entry_unconfigured(tmp_path):
    entry = RepoEntry.unconfigured(tmp_path)
    assert entry.path == tmp_path
    assert entry.faterc is None
    assert entry.branch is None
    assert entry.venv is None
    assert entry.actions == {}


# --- _find_git_repos ---


def test_find_git_repos_empty(tmp_path):
    assert _find_git_repos(tmp_path) == []


def test_find_git_repos_finds_root(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _find_git_repos(tmp_path) == [tmp_path]


def test_find_git_repos_multiple(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".git").mkdir()
    result = _find_git_repos(tmp_path)
    assert tmp_path / "a" in result
    assert tmp_path / "b" in result
    assert len(result) == 2


def test_find_git_repos_does_not_walk_into_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    # A .git inside .git should not produce a second entry
    (tmp_path / ".git" / "modules").mkdir()
    (tmp_path / ".git" / "modules" / ".git").mkdir()
    assert _find_git_repos(tmp_path) == [tmp_path]


# --- iter_repos returns RepoEntry ---


def test_iter_repos_returns_repo_entries(tmp_path):
    (tmp_path / "a").mkdir()
    faterc = tmp_path / "a" / ".faterc"
    faterc.write_text('[config]\nbranch = "main"\n\n[actions]\n')
    entries = iter_repos(tmp_path)
    assert len(entries) == 1
    assert isinstance(entries[0], RepoEntry)
    assert entries[0].path == tmp_path / "a"
    assert entries[0].faterc == faterc
    assert entries[0].branch == "main"


# --- iter_all_repos ---


def test_iter_all_repos_unconfigured(tmp_path):
    (tmp_path / ".git").mkdir()
    entries = iter_all_repos(tmp_path)
    assert len(entries) == 1
    assert entries[0].faterc is None
    assert entries[0].path == tmp_path


def test_iter_all_repos_configured(tmp_path):
    (tmp_path / ".git").mkdir()
    faterc = tmp_path / ".faterc"
    faterc.write_text('[config]\nbranch = "main"\n\n[actions]\n')
    entries = iter_all_repos(tmp_path)
    assert len(entries) == 1
    assert entries[0].faterc == faterc
    assert entries[0].branch == "main"


def test_iter_all_repos_mixed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    (a / ".git").mkdir()
    (a / ".faterc").write_text('[config]\nbranch = "main"\n\n[actions]\n')
    b.mkdir()
    (b / ".git").mkdir()
    entries = iter_all_repos(tmp_path)
    by_path = {e.path: e for e in entries}
    assert by_path[a].faterc is not None
    assert by_path[b].faterc is None


# --- run_repo: unconfigured entry ---


def test_unconfigured_pull_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), only={"pull"})
    assert ("git", "pull") in _cmds(mock_subprocess)


def test_unconfigured_uv_never_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), only={"uv"})
    assert not any("uv" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_prek_never_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), only={"prek"})
    assert not any("prek" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_no_faterc_required(repo, mock_subprocess, monkeypatch):
    """unconfigured entry works even when no .faterc file exists."""
    root = Path(repo.working_tree_dir)
    assert find_faterc(root) is None
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), only={"pull"})  # must not raise


def test_unconfigured_uses_current_branch_no_checkout(
    repo, mock_subprocess, monkeypatch
):
    """branch = current branch for unconfigured repos, so no checkout is needed."""
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(RepoEntry.unconfigured(root), only={"pull"})
    assert not any("checkout" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_only_empty_does_nothing(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), only=set())
    assert not mock_subprocess


def test_unconfigured_exclude_push_only_pulls(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), exclude={"push"})
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("pull" in s for s in cmd_strs)
    assert not any("push" in s for s in cmd_strs)
