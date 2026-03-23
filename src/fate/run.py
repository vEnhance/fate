import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import git

from fate.color import colorize
from fate.git_utils import current_branch, is_dirty
from fate.prek import prek_revs, prek_up_to_date, prek_update_cache


@dataclass
class RepoEntry:
    path: Path
    faterc: Path | None
    branch: str | None = (
        None  # None → use current branch at runtime (unconfigured repos)
    )
    venv: str | None = None
    actions: dict = field(default_factory=dict)

    @classmethod
    def from_faterc(cls, path: Path, faterc: Path) -> "RepoEntry":
        with open(faterc, "rb") as f:
            data = tomllib.load(f)
        config = data.get("config", {})
        return cls(
            path=path,
            faterc=faterc,
            branch=config.get("branch", "main"),
            venv=config.get("venv"),
            actions=data.get("actions", {}),
        )

    @classmethod
    def unconfigured(cls, path: Path) -> "RepoEntry":
        return cls(path=path, faterc=None)


def find_faterc(directory: Path) -> Path | None:
    dotfile = directory / ".faterc"
    visible = directory / "faterc"
    if dotfile.exists() and visible.exists():
        print(
            colorize(
                "1;31",
                f"Warning: Both .faterc and faterc exist in {directory}, using faterc",
            ),
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


def run_repo(
    entry: RepoEntry,
    only: set[str] | None = None,
    exclude: set[str] | None = None,
    prek_rev_cache: dict[str, str] | None = None,
) -> None:
    """Run enabled actions on a single repo.

    only: if given, restrict to this set of task names (still gated by faterc for configured repos)
    exclude: skip these task names
    Unconfigured repos (entry.faterc is None) only allow pull/push, targeting the current branch.
    """
    git_root = entry.path
    repo = git.Repo(git_root)
    exclude = exclude or set()

    branch = entry.branch or current_branch(repo)
    env = venv_env(entry.venv, git_root) if entry.venv else os.environ.copy()

    def active(name: str) -> bool:
        if only is not None and name not in only:
            return False
        if name in exclude:
            return False
        if entry.faterc is None:
            return name in {"pull", "push"}
        return entry.actions.get(name, {}).get("enabled", False)

    pull_active = active("pull")
    uv_active = active("uv")
    prek_active = active("prek")
    push_active = active("push")
    needs_branch = uv_active or prek_active or push_active

    if is_dirty(repo):
        if pull_active:
            print(
                colorize(
                    "1;33",
                    f"{git_root}: Working directory is dirty, running git fetch only",
                )
            )
            subprocess.run(["git", "fetch"], cwd=git_root, check=True)
        elif needs_branch:
            print(colorize("1;33", f"Skipping {git_root}: Working directory is dirty"))
        return

    orig = current_branch(repo)

    if pull_active and not needs_branch:
        # No branch-switching tasks active: update target branch without checkout.
        if orig == branch:
            subprocess.run(["git", "pull"], cwd=git_root, check=True)
        else:
            # Fast-forward the local branch ref from origin without switching to it.
            result = subprocess.run(
                ["git", "fetch", "origin", f"{branch}:{branch}"],
                cwd=git_root,
            )
            if result.returncode != 0:
                # Diverged or no upstream; fall back to plain fetch.
                subprocess.run(["git", "fetch"], cwd=git_root, check=True)
        return

    if not needs_branch:
        return

    if orig != branch:
        subprocess.run(["git", "checkout", branch], cwd=git_root, check=True)

    try:
        if pull_active:
            subprocess.run(["git", "pull"], cwd=git_root, check=True)

        uv_cfg = entry.actions.get("uv", {})
        if uv_active:
            if not entry.venv:
                raise ValueError("uv action requires venv to be set in [config]")
            subprocess.run(
                ["uv", "sync", "--upgrade"], cwd=git_root, env=env, check=True
            )
            if uv_cfg.get("commit", True) and is_dirty(repo):
                subprocess.run(
                    ["git", "commit", "-am", "chore(deps): uv sync -U"],
                    cwd=git_root,
                    env=env,
                    check=True,
                )

        prek_cfg = entry.actions.get("prek", {})
        if prek_active:
            prek_toml = git_root / "prek.toml"
            if prek_rev_cache is not None and prek_up_to_date(
                prek_toml, prek_rev_cache
            ):
                print(colorize("32", "prek: all hooks up-to-date (cached)"))
            else:
                before = prek_revs(prek_toml)
                subprocess.run(
                    ["prek", "auto-update"],
                    cwd=git_root,
                    env=env,
                    check=True,
                    capture_output=True,
                )
                after = prek_revs(prek_toml)
                if prek_rev_cache is not None:
                    prek_update_cache(prek_toml, prek_rev_cache)
                updated = {url for url, rev in after.items() if before.get(url) != rev}
                if updated:
                    for url in sorted(updated):
                        print(
                            colorize(
                                "1;32",
                                f"prek: {url}: {before.get(url)} -> {after[url]}",
                            )
                        )
                else:
                    print(colorize("32", "prek: all hooks up-to-date"))
            if prek_cfg.get("commit", True) and is_dirty(repo):
                subprocess.run(
                    ["git", "commit", "-am", "ci: prek auto-update"],
                    cwd=git_root,
                    env=env,
                    check=True,
                )

        push_cfg = entry.actions.get("push", {})
        if push_active:
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
    fd = shutil.which("fdfind") or shutil.which("fd")
    if fd is not None:
        result = subprocess.run(
            [fd, "--unrestricted", "--type", "f", r"^\.?faterc$", str(target)],
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


def iter_repos(target: Path) -> list[RepoEntry]:
    seen: set[Path] = set()
    repos = []
    for faterc in _find_faterc_files(target):
        parent = faterc.parent
        if parent not in seen:
            seen.add(parent)
            repos.append(RepoEntry.from_faterc(parent, faterc))
    return repos


def _find_git_repos(target: Path) -> list[Path]:
    """Find all git repository roots under (and including) target."""
    fd = shutil.which("fdfind") or shutil.which("fd")
    if fd is not None:
        result = subprocess.run(
            [fd, "--unrestricted", "--type", "d", r"^\.git$", str(target)],
            capture_output=True,
            text=True,
        )
        return sorted(Path(p).parent for p in result.stdout.splitlines() if p)
    repos = []
    for dirpath, dirnames, _ in os.walk(target):
        if ".git" in dirnames:
            repos.append(Path(dirpath))
            dirnames.remove(".git")  # don't recurse into .git itself
    return sorted(repos)


def iter_all_repos(target: Path) -> list[RepoEntry]:
    """Return a RepoEntry for every git repo found under target."""
    configured = {entry.path: entry for entry in iter_repos(target)}
    return [
        configured.get(repo, RepoEntry.unconfigured(repo))
        for repo in _find_git_repos(target)
    ]
