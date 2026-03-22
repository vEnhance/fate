import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import git
import tomlkit
import tomlkit.items

from fate.color import colorize
from fate.git_utils import find_git_root, has_upstream, print_repo_status
from fate.run import _iter_repos, find_faterc, run_repo


def _parse_duration(s: str) -> float:
    """Parse a duration string like '500ms', '1s', '2m', '1h' into seconds."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid duration {s!r}: expected a number followed by ms, s, m, or h"
        )
    value, unit = float(m.group(1)), m.group(2)
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]


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


def cmd_gamble(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    repos = _iter_repos(target)
    if not repos:
        print(f"no .faterc or faterc files found in {target}")
        return

    print(colorize("1;32", "✨💖 Don't think, just pull! 🎰🪙"))
    print("=================================")

    prek_rev_cache: dict[str, str] = {}
    for i, repo_root in enumerate(repos):
        if i > 0 and args.throttle:
            time.sleep(args.throttle)
        print()
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

    print(colorize("1;32", "✨💖 Don't think, just pull! 🎰🪙"))
    print("=================================")

    for i, repo_root in enumerate(repos):
        if args.fetch:
            if i > 0 and args.throttle:
                time.sleep(args.throttle)
            subprocess.run(["git", "fetch", "--quiet"], cwd=repo_root, check=False)
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
    p_gamble.add_argument(
        "-t",
        "--throttle",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_gamble.set_defaults(func=cmd_gamble)

    p_list = sub.add_parser(
        "list", aliases=["l", "ls"], help="show repo statuses without running"
    )
    p_list.add_argument("directory", nargs="?", default=None)
    p_list.add_argument(
        "-t",
        "--throttle",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_list.add_argument(
        "-f",
        "--fetch",
        action="store_true",
        default=False,
        help="run git fetch before showing status",
    )
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
