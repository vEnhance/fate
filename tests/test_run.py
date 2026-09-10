from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fate.run import (
    ALL_TASKS,
    GAMBLE_TASKS,
    RepoEntry,
    _find_faterc_files,
    _find_git_repos,
    find_faterc,
    iter_all_repos,
    iter_repos,
    iter_uninitialized_repos,
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


def test_gamble_skips_push(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, GAMBLE_TASKS)
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("pull" in s for s in cmd_strs)
    assert not any("push" in s for s in cmd_strs)


def test_tasks_restrict_what_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, {"pull"})
    assert not any("push" in " ".join(c) for c in mock_subprocess)


def test_faterc_disabled_not_run_even_if_requested(repo, mock_subprocess, monkeypatch):
    """A task disabled in faterc must never run, even if explicitly requested."""
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root)  # nothing enabled
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, {"push"})
    assert not any("push" in " ".join(c) for c in mock_subprocess)


# -- smart pull (no branch switching when only pull is active) --


def test_smart_pull_same_branch(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(entry, {"pull"})
    cmds = _cmds(mock_subprocess)
    assert ("git", "pull") in cmds
    assert not any("checkout" in " ".join(c) for c in mock_subprocess)


def test_smart_pull_different_branch(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(entry, {"pull"})
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
    run_repo(entry, {"pull"})
    cmds = _cmds(calls)
    assert ("git", "fetch", "origin", "main:main") in cmds
    assert ("git", "fetch") in cmds
    assert not any("checkout" in " ".join(c) for c in calls)


def test_pull_with_other_tasks_switches_branch(repo, mock_subprocess, monkeypatch):
    """When tasks that need the target branch are active, checkout should happen."""
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True, push=True)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(entry, ALL_TASKS)
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("checkout" in s for s in cmd_strs)
    assert any("pull" in s for s in cmd_strs)


# -- dirty state --


def test_dirty_with_pull_fetches(repo, mock_subprocess):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, pull=True)
    (root / "README").write_text("dirty")
    run_repo(entry, {"pull"})
    cmds = _cmds(mock_subprocess)
    assert ("git", "fetch") in cmds
    assert ("git", "pull") not in cmds


def test_dirty_with_branch_task_skips(repo, mock_subprocess, capsys):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root, push=True)
    (root / "README").write_text("dirty")
    run_repo(entry, {"push"})
    assert not mock_subprocess
    assert "dirty" in capsys.readouterr().out.lower()


def test_dirty_no_active_tasks_silent(repo, mock_subprocess, capsys):
    root = Path(repo.working_tree_dir)
    entry = _write_faterc(root)  # nothing enabled
    (root / "README").write_text("dirty")
    run_repo(entry, frozenset())
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


def test_find_git_repos_skips_nested_repos(tmp_path):
    """A git repo inside another git repo's working tree is not returned."""
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "vendor" / "lib"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    assert _find_git_repos(tmp_path) == [tmp_path]


def test_find_git_repos_skips_submodules(tmp_path):
    """Submodules have .git as a file; they should not appear as repo roots."""
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "sub"
    sub.mkdir()
    # Submodules use a .git file, not a directory
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n")
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
    run_repo(RepoEntry.unconfigured(root), {"pull"})
    assert ("git", "pull") in _cmds(mock_subprocess)


def test_unconfigured_uv_never_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), {"uv"})
    assert not any("uv" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_prek_never_runs(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), {"prek"})
    assert not any("prek" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_no_faterc_required(repo, mock_subprocess, monkeypatch):
    """unconfigured entry works even when no .faterc file exists."""
    root = Path(repo.working_tree_dir)
    assert find_faterc(root) is None
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), {"pull"})  # must not raise


def test_unconfigured_uses_current_branch_no_checkout(
    repo, mock_subprocess, monkeypatch
):
    """branch = current branch for unconfigured repos, so no checkout is needed."""
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "feature")
    run_repo(RepoEntry.unconfigured(root), {"pull"})
    assert not any("checkout" in " ".join(c) for c in mock_subprocess)


def test_unconfigured_no_tasks_does_nothing(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), frozenset())
    assert not mock_subprocess


def test_unconfigured_gamble_only_pulls(repo, mock_subprocess, monkeypatch):
    root = Path(repo.working_tree_dir)
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")
    run_repo(RepoEntry.unconfigured(root), GAMBLE_TASKS)
    cmd_strs = [" ".join(c) for c in mock_subprocess]
    assert any("pull" in s for s in cmd_strs)
    assert not any("push" in s for s in cmd_strs)


# --- uv export / prek update integration ---

PREK_BEFORE = """\
[[repos]]
repo = "https://github.com/astral-sh/ruff-pre-commit"
rev = "v0.15.7"
hooks = [{ id = "ruff-check" }]
"""

PREK_AFTER = PREK_BEFORE.replace("v0.15.7", "v0.16.5")

PREK_WITH_UV_EXPORT = (
    PREK_BEFORE
    + """
[[repos]]
repo = "https://github.com/astral-sh/uv-pre-commit"
rev = "0.12.7"
hooks = [{ id = "uv-export" }]
"""
)

UV_EXPORT_CMD = (
    "uv",
    "export",
    "--frozen",
    "--output-file=requirements.txt",
    "--quiet",
)


def _write_task_faterc(path: Path, *, uv=False, prek=False, push=False) -> RepoEntry:
    lines = ['[config]\nbranch = "main"\nvenv = ".venv"\n\n[actions]\n']
    if uv:
        lines.append("uv = { enabled = true, commit = true }\n")
    if prek:
        lines.append("prek = { enabled = true, commit = true }\n")
    if push:
        lines.append("push = { enabled = true, verify = true }\n")
    faterc = path / ".faterc"
    faterc.write_text("".join(lines))
    return RepoEntry.from_faterc(path, faterc)


def _commit_files(repo, root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).write_text(text)
    repo.index.add(list(files))
    repo.index.commit("add fixtures")


def _fake_run(calls: list, on_call=None):
    def _run(args, **kwargs):
        args = list(args)
        calls.append(args)
        m = MagicMock()
        m.returncode = on_call(args) if on_call is not None else 0
        return m

    return _run


@pytest.fixture
def main_branch(monkeypatch):
    monkeypatch.setattr("fate.run.current_branch", lambda _: "main")


def test_uv_exports_requirements(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(
        repo,
        root,
        {"prek.toml": PREK_WITH_UV_EXPORT, "requirements.txt": "gitpython\n"},
    )
    entry = _write_task_faterc(root, uv=True)
    run_repo(entry, {"uv"})
    cmds = _cmds(mock_subprocess)
    assert UV_EXPORT_CMD in cmds
    assert cmds.index(("uv", "sync", "--upgrade")) < cmds.index(UV_EXPORT_CMD)


def test_uv_skips_export_without_requirements_file(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_WITH_UV_EXPORT})
    entry = _write_task_faterc(root, uv=True)
    run_repo(entry, {"uv"})
    assert UV_EXPORT_CMD not in _cmds(mock_subprocess)


def test_uv_skips_export_without_hook(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(
        repo, root, {"prek.toml": PREK_BEFORE, "requirements.txt": "gitpython\n"}
    )
    entry = _write_task_faterc(root, uv=True)
    run_repo(entry, {"uv"})
    assert UV_EXPORT_CMD not in _cmds(mock_subprocess)


def test_uv_skips_export_without_prek_toml(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"requirements.txt": "gitpython\n"})
    entry = _write_task_faterc(root, uv=True)
    run_repo(entry, {"uv"})
    assert UV_EXPORT_CMD not in _cmds(mock_subprocess)


def test_uv_runs_before_prek(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, uv=True, prek=True)
    run_repo(entry, ALL_TASKS)
    cmds = _cmds(mock_subprocess)
    assert cmds.index(("uv", "sync", "--upgrade")) < cmds.index(("prek", "update"))


def test_prek_update_runs_all_files_and_commits(repo, monkeypatch, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True)
    calls: list = []

    def on_call(args):
        if args[:2] == ["prek", "update"]:
            (root / "prek.toml").write_text(PREK_AFTER)
        return 0

    monkeypatch.setattr("fate.run.subprocess.run", _fake_run(calls, on_call))
    run_repo(entry, {"prek"})
    cmds = _cmds(calls)
    assert ("prek", "run", "--all-files") in cmds
    assert ("git", "commit", "-am", "ci: prek update") in cmds


def test_prek_no_run_all_files_when_nothing_updated(repo, mock_subprocess, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True)
    run_repo(entry, {"prek"})
    assert ("prek", "run", "--all-files") not in _cmds(mock_subprocess)


def test_prek_retries_after_auto_fixes(repo, monkeypatch, main_branch):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True)
    calls: list = []
    runs = []

    def on_call(args):
        if args[:2] == ["prek", "update"]:
            (root / "prek.toml").write_text(PREK_AFTER)
            return 0
        if args[:2] == ["prek", "run"]:
            runs.append(args)
            if len(runs) == 1:
                (root / "README").write_text("auto-fixed")
                return 1
        return 0

    monkeypatch.setattr("fate.run.subprocess.run", _fake_run(calls, on_call))
    run_repo(entry, {"prek"})
    assert len(runs) == 2
    assert ("git", "commit", "-am", "ci: prek update") in _cmds(calls)


def test_prek_failure_leaves_repo_alone(repo, monkeypatch, main_branch, capsys):
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True)
    calls: list = []
    runs = []

    def on_call(args):
        if args[:2] == ["prek", "update"]:
            (root / "prek.toml").write_text(PREK_AFTER)
            return 0
        if args[:2] == ["prek", "run"]:
            runs.append(args)
            return 1
        return 0

    monkeypatch.setattr("fate.run.subprocess.run", _fake_run(calls, on_call))
    run_repo(entry, {"prek"})
    assert len(runs) == 1
    assert not any("commit" in " ".join(c) for c in calls)
    assert "manual intervention" in capsys.readouterr().out


def test_prek_failure_blocks_push(repo_with_upstream, monkeypatch, main_branch):
    repo = repo_with_upstream
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True, push=True)
    calls: list = []

    def on_call(args):
        if args[:2] == ["prek", "update"]:
            (root / "prek.toml").write_text(PREK_AFTER)
            return 0
        return 1 if args[:2] == ["prek", "run"] else 0

    monkeypatch.setattr("fate.run.subprocess.run", _fake_run(calls, on_call))
    run_repo(entry, {"prek", "push"})
    assert ("git", "push") not in _cmds(calls)


def test_prek_success_allows_push(repo_with_upstream, monkeypatch, main_branch):
    repo = repo_with_upstream
    root = Path(repo.working_tree_dir)
    _commit_files(repo, root, {"prek.toml": PREK_BEFORE})
    entry = _write_task_faterc(root, prek=True, push=True)
    calls: list = []

    def on_call(args):
        if args[:2] == ["prek", "update"]:
            (root / "prek.toml").write_text(PREK_AFTER)
        return 0

    monkeypatch.setattr("fate.run.subprocess.run", _fake_run(calls, on_call))
    run_repo(entry, {"prek", "push"})
    assert ("git", "push") in _cmds(calls)


# --- iter_uninitialized_repos ---


def _make_repo(path: Path, *markers: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    for name in markers:
        (path / name).write_text("")
    return path


def test_seek_empty(tmp_path):
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_finds_uv_lock(tmp_path):
    _make_repo(tmp_path / "a", "uv.lock")
    assert iter_uninitialized_repos(tmp_path) == [(tmp_path / "a", ["uv.lock"])]


def test_seek_finds_prek_toml(tmp_path):
    _make_repo(tmp_path / "a", "prek.toml")
    assert iter_uninitialized_repos(tmp_path) == [(tmp_path / "a", ["prek.toml"])]


def test_seek_lists_both_markers(tmp_path):
    _make_repo(tmp_path / "a", "prek.toml", "uv.lock")
    assert iter_uninitialized_repos(tmp_path) == [
        (tmp_path / "a", ["uv.lock", "prek.toml"])
    ]


def test_seek_skips_configured_repo(tmp_path):
    a = _make_repo(tmp_path / "a", "uv.lock")
    (a / ".faterc").write_text('[config]\nbranch = "main"\n\n[actions]\n')
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_skips_visible_faterc_repo(tmp_path):
    a = _make_repo(tmp_path / "a", "uv.lock")
    (a / "faterc").write_text('[config]\nbranch = "main"\n\n[actions]\n')
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_skips_repo_without_markers(tmp_path):
    _make_repo(tmp_path / "a")
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_ignores_marker_outside_git_repo(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "uv.lock").write_text("")
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_ignores_marker_below_repo_root(tmp_path):
    """A uv.lock in a vendored subdirectory is not something fate would act on."""
    a = _make_repo(tmp_path / "a")
    vendored = a / ".venv" / "lib" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "uv.lock").write_text("")
    assert iter_uninitialized_repos(tmp_path) == []


def test_seek_ignores_nested_repo(tmp_path):
    a = _make_repo(tmp_path / "a", "uv.lock")
    _make_repo(a / "vendor" / "lib", "prek.toml")
    assert iter_uninitialized_repos(tmp_path) == [(a, ["uv.lock"])]


def test_seek_includes_target_itself(tmp_path):
    _make_repo(tmp_path, "uv.lock")
    assert iter_uninitialized_repos(tmp_path) == [(tmp_path, ["uv.lock"])]


def test_seek_respects_depth(tmp_path):
    _make_repo(tmp_path / "one" / "two", "uv.lock")
    assert iter_uninitialized_repos(tmp_path, depth=1) == []
    assert iter_uninitialized_repos(tmp_path) == [
        (tmp_path / "one" / "two", ["uv.lock"])
    ]


def test_seek_skips_hidden_directories_by_default(tmp_path):
    _make_repo(tmp_path / ".cache" / "a", "uv.lock")
    assert iter_uninitialized_repos(tmp_path) == []
    assert iter_uninitialized_repos(tmp_path, unrestricted=True) == [
        (tmp_path / ".cache" / "a", ["uv.lock"])
    ]
