import functools
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
from fate.prek import (
    prek_revs,
    prek_up_to_date,
    prek_update_cache,
    uv_export_command,
    uv_export_output,
)

ALL_TASKS = frozenset({"pull", "uv", "prek", "push"})
NO_PUSH_TASKS = ALL_TASKS - {"push"}


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


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PREK_QUIET", "1")
    return env


def venv_env(venv: str, repo_root: Path) -> dict[str, str]:
    venv_path = Path(venv).expanduser()
    if not venv_path.is_absolute():
        venv_path = repo_root / venv_path
    env = base_env()
    env["PATH"] = str(venv_path / "bin") + os.pathsep + env.get("PATH", "")
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_path)
    return env


def uv_export(git_root: Path, env: dict[str, str]) -> None:
    """Refresh the exported requirements file, if a uv-export hook would demand one."""
    prek_toml = git_root / "prek.toml"
    if not prek_toml.exists():
        return
    command = uv_export_command(prek_toml)
    if command is None or not (git_root / uv_export_output(command)).exists():
        return
    print(colorize("32", f"uv: {' '.join(command)}"))
    subprocess.run(command, cwd=git_root, env=env, check=True)


def prek_run_all_files(git_root: Path, env: dict[str, str], repo: git.Repo) -> bool:
    """Run every hook, retrying once if the first pass auto-fixed files.

    Returns True if the hooks end up passing.
    """

    def hooks_pass() -> bool:
        result = subprocess.run(
            ["prek", "run", "--all-files"], cwd=git_root, env=env, check=False
        )
        return result.returncode == 0

    print(colorize("32", "prek: running updated hooks on all files"))
    before = repo.git.status("--porcelain")
    if hooks_pass():
        return True
    if repo.git.status("--porcelain") == before:
        return False
    print(colorize("33", "prek: retrying after auto-fixes"))
    return hooks_pass()


def prek_task(
    git_root: Path,
    env: dict[str, str],
    repo: git.Repo,
    cfg: dict,
    rev_cache: dict[str, str] | None,
) -> bool:
    """Update prek hooks and commit the result. False if manual fixes are needed."""
    prek_toml = git_root / "prek.toml"
    if rev_cache is not None and prek_up_to_date(prek_toml, rev_cache):
        print(colorize("32", "prek: all hooks up-to-date (cached)"))
    else:
        before = prek_revs(prek_toml)
        subprocess.run(
            ["prek", "update"],
            cwd=git_root,
            env=env,
            check=True,
            capture_output=True,
        )
        after = prek_revs(prek_toml)
        if rev_cache is not None:
            prek_update_cache(prek_toml, rev_cache)
        updated = {url for url, rev in after.items() if before.get(url) != rev}
        if updated:
            for url in sorted(updated):
                print(
                    colorize("1;32", f"prek: {url}: {before.get(url)} -> {after[url]}")
                )
            if not prek_run_all_files(git_root, env, repo):
                return False
        else:
            print(colorize("32", "prek: all hooks up-to-date"))
    if cfg.get("commit", True) and is_dirty(repo):
        subprocess.run(
            ["git", "commit", "-am", "ci: prek update"],
            cwd=git_root,
            env=env,
            check=True,
        )
    return True


def run_repo(
    entry: RepoEntry,
    tasks: frozenset[str] | set[str],
    prek_rev_cache: dict[str, str] | None = None,
) -> None:
    """Run the requested actions on a single repo, if enabled in its faterc.

    Unconfigured repos (entry.faterc is None) only allow pull/push, targeting the current branch.
    """
    git_root = entry.path
    repo = git.Repo(git_root)

    branch = entry.branch or current_branch(repo)
    env = venv_env(entry.venv, git_root) if entry.venv else base_env()

    def active(name: str) -> bool:
        if name not in tasks:
            return False
        if entry.faterc is None:
            return name in {"pull", "push"}
        cfg = entry.actions.get(name, {})
        if cfg.get("enabled") is False:
            print(colorize("0;35", f"- skipping {name} (enabled = false)"))
        return cfg.get("enabled", False)

    pull_active = active("pull")
    uv_active = active("uv")
    prek_active = active("prek")
    push_active = active("push")
    needs_branch = uv_active or prek_active or push_active

    if is_dirty(repo):
        if pull_active:
            print(colorize("1;33", "Working dir is dirty, running git fetch only"))
            subprocess.run(["git", "fetch"], cwd=git_root, env=env, check=True)
        elif needs_branch:
            print(colorize("1;33", "Skipping dirty working directory"))
        return

    orig = current_branch(repo)

    if pull_active and not needs_branch:
        # No branch-switching tasks active: update target branch without checkout.
        if orig == branch:
            subprocess.run(["git", "pull"], cwd=git_root, env=env, check=True)
        else:
            # Fast-forward the local branch ref from origin without switching to it.
            result = subprocess.run(
                ["git", "fetch", "origin", f"{branch}:{branch}"],
                cwd=git_root,
                env=env,
                check=True,
            )
            if result.returncode != 0:
                # Diverged or no upstream; fall back to plain fetch.
                subprocess.run(["git", "fetch"], cwd=git_root, env=env, check=True)
        return

    if not needs_branch:
        return

    if orig != branch:
        subprocess.run(["git", "checkout", branch], cwd=git_root, env=env, check=True)

    needs_attention = False
    try:
        if pull_active:
            subprocess.run(["git", "pull"], cwd=git_root, env=env, check=True)

        uv_cfg = entry.actions.get("uv", {})
        if uv_active:
            if not entry.venv:
                raise ValueError("uv action requires venv to be set in [config]")
            subprocess.run(
                ["uv", "sync", "--upgrade"], cwd=git_root, env=env, check=True
            )
            uv_export(git_root, env)
            if uv_cfg.get("commit", True) and is_dirty(repo):
                subprocess.run(
                    ["git", "commit", "-am", "chore(deps): uv sync --upgrade"],
                    cwd=git_root,
                    env=env,
                    check=True,
                )

        if prek_active and not prek_task(
            git_root, env, repo, entry.actions.get("prek", {}), prek_rev_cache
        ):
            print(
                colorize(
                    "1;31",
                    f"{git_root}: prek hooks still failing after auto-fixes, "
                    "leaving the repo for manual intervention",
                )
            )
            needs_attention = True

        push_cfg = entry.actions.get("push", {})
        if push_active and not needs_attention:
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
            if needs_attention:
                print(colorize("1;33", f"{git_root}: staying on branch {branch}"))
            else:
                subprocess.run(
                    ["git", "checkout", orig], cwd=git_root, env=env, check=True
                )


@functools.cache
def _find_fd() -> str | None:
    fd = shutil.which("fdfind") or shutil.which("fd")
    if fd is None:
        print(
            "Warning: fd/fdfind not found, falling back to os.walk (slower). "
            "Install fd for better performance.",
            file=sys.stderr,
        )
    return fd


def _fd_base(depth: int | None) -> list[str] | None:
    fd = _find_fd()
    if fd is None:
        return None
    # --hidden: needed so fd can find .faterc and .git (both start with '.')
    # --no-ignore-vcs: don't let .gitignore hide repos from us
    cmd = [fd, "--hidden", "--no-ignore-vcs"]
    if depth is not None:
        cmd.extend(["--max-depth", str(depth + 1)])
    return cmd


def _in_hidden_dir(path: Path, target: Path) -> bool:
    """Return True if any directory component between target and path starts with '.'."""
    return any(part.startswith(".") for part in path.relative_to(target).parts[:-1])


def _find_faterc_files(
    target: Path, depth: int | None = None, unrestricted: bool = False
) -> list[Path]:
    cmd = _fd_base(depth)
    if cmd is not None:
        cmd.extend(["--type", "f", r"^\.?faterc$", str(target)])
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        paths = sorted(Path(p) for p in result.stdout.splitlines() if p)
        if not unrestricted:
            paths = [p for p in paths if not _in_hidden_dir(p, target)]
        return paths
    files = []
    for dirpath, dirnames, filenames in os.walk(target):
        current_depth = len(Path(dirpath).relative_to(target).parts)
        if not unrestricted:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if depth is not None and current_depth >= depth:
            dirnames.clear()
        for name in (".faterc", "faterc"):
            if name in filenames:
                files.append(Path(dirpath) / name)
    return sorted(files)


def iter_repos(
    target: Path, depth: int | None = None, unrestricted: bool = False
) -> list[RepoEntry]:
    seen: set[Path] = set()
    repos = []
    for faterc in _find_faterc_files(target, depth=depth, unrestricted=unrestricted):
        parent = faterc.parent
        if parent not in seen:
            seen.add(parent)
            repos.append(RepoEntry.from_faterc(parent, faterc))
    return repos


def _find_git_repos(
    target: Path, depth: int | None = None, unrestricted: bool = False
) -> list[Path]:
    """Find top-level git repository roots under (and including) target.

    Repos nested inside another git repo (submodules, vendored repos, etc.) are skipped.
    """
    cmd = _fd_base(depth)
    if cmd is not None:
        cmd.extend(["--type", "d", r"^\.git$", str(target)])
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        git_dirs = sorted(Path(p) for p in result.stdout.splitlines() if p)
        if not unrestricted:
            git_dirs = [p for p in git_dirs if not _in_hidden_dir(p, target)]
        candidates = [p.parent for p in git_dirs]
    else:
        candidates = []
        for dirpath, dirnames, _ in os.walk(target):
            current_depth = len(Path(dirpath).relative_to(target).parts)
            is_git_repo = ".git" in dirnames
            if not unrestricted:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if is_git_repo:
                candidates.append(Path(dirpath))
                dirnames.clear()  # stop recursing into this repo entirely
            elif depth is not None and current_depth >= depth:
                dirnames.clear()
        candidates.sort()

    # Strip repos that are nested inside another found repo (needed for the fd path).
    top_level: list[Path] = []
    for repo in candidates:
        if not top_level or not repo.is_relative_to(top_level[-1]):
            top_level.append(repo)
    return top_level


def iter_all_repos(
    target: Path, depth: int | None = None, unrestricted: bool = False
) -> list[RepoEntry]:
    """Return a RepoEntry for every git repo found under target."""
    configured = {
        entry.path: entry
        for entry in iter_repos(target, depth=depth, unrestricted=unrestricted)
    }
    return [
        configured.get(repo, RepoEntry.unconfigured(repo))
        for repo in _find_git_repos(target, depth=depth, unrestricted=unrestricted)
    ]


INIT_MARKERS = ("uv.lock", "prek.toml")


def iter_uninitialized_repos(
    target: Path, depth: int | None = None, unrestricted: bool = False
) -> list[tuple[Path, list[str]]]:
    """Return (repo root, markers) for git repos with no faterc but with uv/prek set up.

    Without a faterc, `fate` can only pull and push such a repo,
    so these are exactly the repos where `fate init` would unlock something.
    """
    configured = {
        entry.path
        for entry in iter_repos(target, depth=depth, unrestricted=unrestricted)
    }
    found = []
    for repo in _find_git_repos(target, depth=depth, unrestricted=unrestricted):
        if repo in configured:
            continue
        markers = [name for name in INIT_MARKERS if (repo / name).exists()]
        if markers:
            found.append((repo, markers))
    return found
