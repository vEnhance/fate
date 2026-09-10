import argparse
import os
import re
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

import git
import tomlkit
import tomlkit.items

from fate.color import colorize
from fate.git_utils import find_git_root, has_upstream, print_repo_status
from fate.run import (
    GAMBLE_TASKS,
    RepoEntry,
    find_faterc,
    iter_all_repos,
    iter_repos,
    iter_uninitialized_repos,
    run_repo,
)


def _parse_duration(s: str) -> float:
    """Parse a duration string like '500ms', '1s', '2m', '1h' into seconds.

    If no unit is specified, seconds are assumed.
    """
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", s)
    if not m or not s:
        raise argparse.ArgumentTypeError(
            f"Invalid duration {s!r}: Expected a number optionally followed by ms, s, m, or h"
        )
    value, unit = float(m.group(1)), m.group(2) or "s"
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]


def _run_all(
    target: Path,
    tasks: frozenset[str] | set[str],
    delay: float = 0.0,
    blank_lines: bool = True,
    all_repos: bool = False,
    depth: int | None = None,
    unrestricted: bool = False,
) -> None:
    if all_repos:
        repos = iter_all_repos(target, depth=depth, unrestricted=unrestricted)
        if not repos:
            print(f"No git repositories found in {target}")
            return
    else:
        repos = iter_repos(target, depth=depth, unrestricted=unrestricted)
        if not repos:
            print(f"No .faterc or faterc files found in {target}")
            return

    prek_rev_cache: dict[str, str] = {}
    for i, entry in enumerate(repos):
        if i > 0 and delay:
            print(colorize("37", f"Waiting {delay} seconds before the next repo..."))
            time.sleep(delay)
        if i > 0 and blank_lines:
            print()
        try:
            try:
                parent = entry.path.relative_to(target).parent
                path_prefix = str(parent) + "/" if str(parent) != "." else ""
            except ValueError:
                path_prefix = str(entry.path.parent) + "/"
            if not print_repo_status(entry.path, path_prefix=path_prefix):
                continue
            run_repo(entry, tasks, prek_rev_cache=prek_rev_cache)
        except git.InvalidGitRepositoryError:
            print(
                f"Warning: {entry.path}: not a valid git repository, skipping",
                file=sys.stderr,
            )
        except (subprocess.CalledProcessError, git.GitCommandError) as e:
            print(f"Error: {e}", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()

    git_root = find_git_root(target)
    if git_root is None:
        print(f"Error: {target} is not in a git repository", file=sys.stderr)
        sys.exit(1)

    faterc = find_faterc(git_root)
    if faterc is None:
        print(f"Error: No .faterc or faterc found in {git_root}", file=sys.stderr)
        sys.exit(1)

    run_repo(RepoEntry.from_faterc(git_root, faterc), GAMBLE_TASKS)


def cmd_gamble(args: argparse.Namespace) -> None:
    _run_all_from_args(args, GAMBLE_TASKS)


def cmd_list(args: argparse.Namespace) -> None:
    _run_all_from_args(args, frozenset(), blank_lines=False)


def cmd_pull(args: argparse.Namespace) -> None:
    _run_all_from_args(args, {"pull"})


def cmd_push(args: argparse.Namespace) -> None:
    _run_all_from_args(args, {"push"})


def _venv_setting(directory: Path, active_venv: str | None) -> str | None:
    """Pick the venv to record: the active virtualenv, else an in-tree .venv."""
    if active_venv:
        venv = Path(active_venv).resolve()
        if venv.is_relative_to(directory):
            return str(venv.relative_to(directory))
        if venv.is_relative_to(Path.home()):
            return f"~/{venv.relative_to(Path.home())}"
        return str(venv)
    if (directory / ".venv").is_dir():
        return ".venv"
    return None


def init_repo(directory: Path, visible: bool = False) -> Path:
    """Write a faterc for the repository at directory and return its path.

    Raises ValueError if directory isn't a git repository root, or already has one.
    """
    if not (directory / ".git").exists():
        raise ValueError(f"{directory} is not the root of a git repository")
    if find_faterc(directory) is not None:
        raise ValueError(f".faterc or faterc already exists in {directory}")
    try:
        repo = git.Repo(directory)
    except git.InvalidGitRepositoryError:
        raise ValueError(f"{directory} is not a valid git repository") from None

    faterc = directory / ("faterc" if visible else ".faterc")
    has_uv = (directory / "uv.lock").exists()
    has_prek = (directory / "prek.toml").exists()
    remote_configured = has_upstream(repo)
    venv = _venv_setting(directory, os.environ.get("VIRTUAL_ENV"))

    def inline(**kwargs) -> tomlkit.items.InlineTable:
        t = tomlkit.inline_table()
        for k, v in kwargs.items():
            t.append(k, v)
        return t

    doc = tomlkit.document()
    config = tomlkit.table()
    config.add("branch", "main")
    if venv is not None:
        config.add("venv", venv)
    doc.add("config", config)
    doc.add(tomlkit.nl())

    actions = tomlkit.table()
    actions.add("pull", inline(enabled=remote_configured))
    if has_uv:
        actions.add("uv", inline(enabled=venv is not None, commit=True))
    if has_prek:
        actions.add("prek", inline(enabled=True, commit=True))
    actions.add("push", inline(enabled=remote_configured, verify=True))
    doc.add("actions", actions)

    faterc.write_text(tomlkit.dumps(doc))
    return faterc


def cmd_init(args: argparse.Namespace) -> None:
    directory = Path(args.directory).resolve() if args.directory else Path.cwd()
    try:
        faterc = init_repo(directory, visible=args.visible)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"created {faterc}")


def cmd_seek(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    found = iter_uninitialized_repos(
        target, depth=args.depth, unrestricted=args.unrestricted
    )
    if not found:
        print(f"Nothing to initialize in {target}")
        return

    for repo, markers in found:
        try:
            label = str(repo.relative_to(target))
        except ValueError:
            label = str(repo)
        print(f"{colorize('1;34', label)} {colorize('37', ', '.join(markers))}")
    print()
    print(f"Run {colorize('1;32', 'fate init')} in each one you want.")


def _add_multi_args(p: argparse.ArgumentParser) -> None:
    """Add the common arguments shared by all multi-repo subcommands."""
    p.add_argument("directory", nargs="?", default=None)
    p.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="Delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    depth_group = p.add_mutually_exclusive_group()
    depth_group.add_argument(
        "--depth",
        type=int,
        default=None,
        metavar="N",
        help="Search at most N directories deep (default: 1)",
    )
    depth_group.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        default=False,
        help="Search recursively to any depth",
    )
    p.add_argument(
        "-u",
        "--unrestricted",
        action="store_true",
        default=False,
        help="Also search inside hidden directories",
    )


def _run_all_from_args(
    args: argparse.Namespace,
    tasks: frozenset[str] | set[str],
    blank_lines: bool = True,
) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    _run_all(
        target,
        tasks,
        delay=args.delay,
        blank_lines=blank_lines,
        all_repos=args.all,
        depth=None if args.recursive else (args.depth if args.depth is not None else 1),
        unrestricted=args.unrestricted,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fate",
        description="Runs git pull and other commands recursively on your git repositories.",
        epilog="✨💖 Don't ask, just pull! 🎰🪙",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {version('fate-casino')}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init", aliases=["i"], help="Initialize .faterc in a git repository."
    )
    p_init.add_argument("directory", nargs="?", default=None)
    p_init.add_argument(
        "--visible",
        action="store_true",
        default=False,
        help="create faterc instead of .faterc",
    )
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser(
        "run", aliases=["r"], help="Run all tasks except push on a single repository."
    )
    p_run.add_argument("directory", nargs="?", default=None)
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser(
        "list", aliases=["l", "ls"], help="Show repo statuses without running."
    )
    _add_multi_args(p_list)
    p_list.set_defaults(func=cmd_list)

    p_pull = sub.add_parser("pull", help="Run only the pull task on all repositories.")
    _add_multi_args(p_pull)
    p_pull.set_defaults(func=cmd_pull)

    p_gamble = sub.add_parser(
        "gamble", aliases=["g"], help="Run all tasks except push on all repositories."
    )
    _add_multi_args(p_gamble)
    p_gamble.set_defaults(func=cmd_gamble)

    p_push = sub.add_parser("push", help="Run only the push task on all repositories.")
    _add_multi_args(p_push)
    p_push.set_defaults(func=cmd_push)

    p_seek = sub.add_parser(
        "seek",
        aliases=["s"],
        help="Find git repositories that look like they want a .faterc.",
    )
    p_seek.add_argument("directory", nargs="?", default=None)
    p_seek.add_argument(
        "--depth",
        type=int,
        default=None,
        metavar="N",
        help="Search at most N directories deep (default: unlimited)",
    )
    p_seek.add_argument(
        "-u",
        "--unrestricted",
        action="store_true",
        default=False,
        help="Also search inside hidden directories",
    )
    p_seek.set_defaults(func=cmd_seek)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
