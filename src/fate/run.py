import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import git

from fate.git_utils import current_branch, is_dirty
from fate.prek import prek_up_to_date, prek_update_cache


def find_faterc(directory: Path) -> Path | None:
    dotfile = directory / ".faterc"
    visible = directory / "faterc"
    if dotfile.exists() and visible.exists():
        print(
            f"warning: both .faterc and faterc exist in {directory}, using faterc",
            file=sys.stderr,
        )
        return visible
    for p in (dotfile, visible):
        if p.exists():
            return p
    return None


def venv_env(venv: str, repo_root: Path) -> dict[str, str]:
    venv_path = Path(venv).expanduser()
    if not venv_path.is_absolute():
        venv_path = repo_root / venv_path
    env = os.environ.copy()
    env["PATH"] = str(venv_path / "bin") + os.pathsep + env.get("PATH", "")
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_path)
    return env


def run_repo(git_root: Path, prek_rev_cache: dict[str, str] | None = None) -> None:
    repo = git.Repo(git_root)

    if is_dirty(repo):
        print(f"Skipping {git_root}: working directory is dirty")
        return

    faterc_path = find_faterc(git_root)
    assert faterc_path is not None
    with open(faterc_path, "rb") as f:
        faterc = tomllib.load(f)

    config = faterc.get("config", {})
    actions = faterc.get("actions", {})
    branch = config.get("branch", "main")
    venv = config.get("venv")
    env = venv_env(venv, git_root) if venv else os.environ.copy()

    orig = current_branch(repo)
    if orig != branch:
        subprocess.run(["git", "checkout", branch], cwd=git_root, check=True)

    try:
        if actions.get("pull", {}).get("enabled", False):
            subprocess.run(["git", "pull"], cwd=git_root, check=True)

        uv_cfg = actions.get("uv", {})
        if uv_cfg.get("enabled", False):
            if not venv:
                raise ValueError("uv action requires venv to be set in [config]")
            subprocess.run(["uv", "sync", "-U"], cwd=git_root, env=env, check=True)
            if uv_cfg.get("commit", True) and is_dirty(repo):
                subprocess.run(
                    ["git", "commit", "-am", "chore(deps): uv sync -U"],
                    cwd=git_root,
                    env=env,
                    check=True,
                )

        prek_cfg = actions.get("prek", {})
        if prek_cfg.get("enabled", False):
            prek_toml = git_root / "prek.toml"
            if prek_rev_cache is None or not prek_up_to_date(prek_toml, prek_rev_cache):
                subprocess.run(
                    ["prek", "auto-update"], cwd=git_root, env=env, check=True
                )
                if prek_rev_cache is not None:
                    prek_update_cache(prek_toml, prek_rev_cache)
            if prek_cfg.get("commit", True) and is_dirty(repo):
                subprocess.run(
                    ["git", "commit", "-am", "ci: prek auto-update"],
                    cwd=git_root,
                    env=env,
                    check=True,
                )

        push_cfg = actions.get("push", {})
        if push_cfg.get("enabled", False):
            try:
                ahead = int(repo.git.rev_list("--count", "@{u}..HEAD"))
            except git.GitCommandError:
                ahead = 0
            if ahead:
                push_args = ["git", "push"]
                if not push_cfg.get("verify", True):
                    push_args.append("--no-verify")
                subprocess.run(push_args, cwd=git_root, env=env, check=True)
    finally:
        if orig and orig != branch:
            subprocess.run(["git", "checkout", orig], cwd=git_root, check=True)


def _find_faterc_files(target: Path) -> list[Path]:
    if shutil.which("fd") is not None:
        result = subprocess.run(
            ["fd", "--unrestricted", "--type", "f", r"^\.?faterc$", str(target)],
            capture_output=True,
            text=True,
        )
        return sorted(Path(p) for p in result.stdout.splitlines() if p)
    files = []
    for dirpath, _, filenames in os.walk(target):
        for name in (".faterc", "faterc"):
            if name in filenames:
                files.append(Path(dirpath) / name)
    return sorted(files)


def _iter_repos(target: Path) -> list[Path]:
    seen: set[Path] = set()
    repos = []
    for faterc in _find_faterc_files(target):
        if faterc.parent not in seen:
            seen.add(faterc.parent)
            repos.append(faterc.parent)
    return repos
