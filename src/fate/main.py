import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import git
import tomli
import tomlkit
import tomlkit.items


def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def find_git_root(path: Path) -> Path | None:
    try:
        repo = git.Repo(path, search_parent_directories=True)
        assert repo.working_tree_dir is not None
        return Path(repo.working_tree_dir)
    except git.InvalidGitRepositoryError:
        return None


def is_dirty(repo: git.Repo) -> bool:
    return repo.is_dirty(untracked_files=False)


def current_branch(repo: git.Repo) -> str:
    try:
        return repo.active_branch.name
    except TypeError:
        return ""  # detached HEAD


def has_upstream(repo: git.Repo) -> bool:
    try:
        repo.git.rev_parse("--abbrev-ref", "@{u}")
        return True
    except git.GitCommandError:
        return False


def print_repo_status(repo_root: Path) -> bool:
    """Print a status line for repo_root. Returns False if no remote (caller should skip)."""
    repo = git.Repo(repo_root)
    branch = current_branch(repo)
    name = _c("1;34", repo_root.name)

    if not repo.remotes:
        print(f"\n{name} (no remote, skipping)")
        return False

    try:
        ahead = int(repo.git.rev_list("--count", "@{u}..HEAD"))
        behind = int(repo.git.rev_list("--count", "HEAD..@{u}"))
    except git.GitCommandError:
        ahead = behind = 0

    staged = len(repo.index.diff("HEAD"))
    dirty = len(repo.index.diff(None))
    untracked = len(repo.untracked_files)

    parts = []
    if ahead:
        parts.append(_c("36", f"↑{ahead}"))
    if behind:
        parts.append(_c("36", f"↓{behind}"))
    if staged:
        parts.append(_c("32", f"●{staged}"))
    if dirty:
        parts.append(_c("31", f"✚{dirty}"))
    if untracked:
        parts.append(_c("34", f"…{untracked}"))

    status = (" " + " ".join(parts)) if parts else ""
    print(f"\n{name} {_c('33', f'({branch})')}{status}")
    return True


def _prek_revs(prek_toml: Path) -> dict[str, str]:
    """Return {repo_url: rev} for all versioned repos in prek.toml."""
    with open(prek_toml, "rb") as f:
        data = tomli.load(f)
    return {e["repo"]: e["rev"] for e in data.get("repos", []) if "rev" in e}


def _prek_up_to_date(prek_toml: Path, cache: dict[str, str]) -> bool:
    """True if every versioned hook in prek_toml is already at the cached latest rev."""
    revs = _prek_revs(prek_toml)
    return bool(revs) and all(cache.get(url) == rev for url, rev in revs.items())


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
            if prek_rev_cache is None or not _prek_up_to_date(
                prek_toml, prek_rev_cache
            ):
                subprocess.run(
                    ["prek", "auto-update"], cwd=git_root, env=env, check=True
                )
                if prek_rev_cache is not None:
                    prek_rev_cache.update(_prek_revs(prek_toml))
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


def cmd_run(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()

    git_root = find_git_root(target)
    if git_root is None:
        print(f"error: {target} is not in a git repository", file=sys.stderr)
        sys.exit(1)

    if find_faterc(git_root) is None:
        print(f"error: no .faterc or faterc found in {git_root}", file=sys.stderr)
        sys.exit(1)

    run_repo(git_root)


def _iter_repos(target: Path) -> list[Path]:
    seen: set[Path] = set()
    repos = []
    for faterc in sorted(
        [*target.rglob(".faterc"), *target.rglob("faterc")],
        key=lambda p: p.parent,
    ):
        if faterc.parent not in seen:
            seen.add(faterc.parent)
            repos.append(faterc.parent)
    return repos


def cmd_gamble(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    repos = _iter_repos(target)
    if not repos:
        print(f"no .faterc or faterc files found in {target}")
        return

    print(_c("1;32", "✨💖 Don't think, just pull! 🎰🪙"))
    print("=================================")

    prek_rev_cache: dict[str, str] = {}
    for repo_root in repos:
        if not print_repo_status(repo_root):
            continue
        try:
            run_repo(repo_root, prek_rev_cache=prek_rev_cache)
        except (subprocess.CalledProcessError, git.GitCommandError) as e:
            print(f"error: {e}", file=sys.stderr)


def cmd_list(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    repos = _iter_repos(target)
    if not repos:
        print(f"no .faterc or faterc files found in {target}")
        return

    print(_c("1;32", "✨💖 Don't think, just pull! 🎰🪙"))
    print("=================================")

    for repo_root in repos:
        print_repo_status(repo_root)


def cmd_init(args: argparse.Namespace) -> None:
    cwd = Path.cwd()

    if not (cwd / ".git").exists():
        print("error: not at the root of a git repository", file=sys.stderr)
        sys.exit(1)

    if find_faterc(cwd) is not None:
        print("error: .faterc or faterc already exists", file=sys.stderr)
        sys.exit(1)
    faterc = cwd / ("faterc" if args.visible else ".faterc")

    has_uv = (cwd / "uv.lock").exists()
    has_prek = (cwd / "prek.toml").exists()

    repo = git.Repo(cwd)
    remote_configured = has_upstream(repo)

    venv_val = None
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        vp = Path(active).resolve()
        try:
            venv_val = str(vp.relative_to(cwd))
        except ValueError:
            try:
                venv_val = f"~/{vp.relative_to(Path.home())}"
            except ValueError:
                venv_val = str(vp)

    def inline(**kwargs) -> tomlkit.items.InlineTable:
        t = tomlkit.inline_table()
        for k, v in kwargs.items():
            t.append(k, v)
        return t

    doc = tomlkit.document()
    config = tomlkit.table()
    config.add("branch", "main")
    if venv_val is not None:
        config.add("venv", venv_val)
    doc.add("config", config)
    doc.add(tomlkit.nl())

    actions = tomlkit.table()
    actions.add("pull", inline(enabled=remote_configured))
    if has_uv:
        actions.add("uv", inline(enabled=venv_val is not None, commit=True))
    if has_prek:
        actions.add("prek", inline(enabled=True, commit=True))
    actions.add("push", inline(enabled=remote_configured, verify=True))
    doc.add("actions", actions)

    faterc.write_text(tomlkit.dumps(doc))
    print(f"created {faterc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fate", description="Automate git repo maintenance"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", aliases=["r"], help="run fate on a repository")
    p_run.add_argument("directory", nargs="?", default=None)
    p_run.set_defaults(func=cmd_run)

    p_gamble = sub.add_parser(
        "gamble", aliases=["g"], help="run fate on all repos under a directory"
    )
    p_gamble.add_argument("directory", nargs="?", default=None)
    p_gamble.set_defaults(func=cmd_gamble)

    p_list = sub.add_parser(
        "list", aliases=["l"], help="show repo statuses without running"
    )
    p_list.add_argument("directory", nargs="?", default=None)
    p_list.set_defaults(func=cmd_list)

    p_init = sub.add_parser("init", aliases=["i"], help="initialize .faterc")
    p_init.add_argument(
        "--visible",
        action="store_true",
        default=False,
        help="create faterc instead of .faterc",
    )
    p_init.set_defaults(func=cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
