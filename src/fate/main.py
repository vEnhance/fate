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

from fate.git_utils import find_git_root, has_upstream, print_repo_status
from fate.run import RepoEntry, find_faterc, iter_all_repos, iter_repos, run_repo


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


VALID_TASKS = {"pull", "uv", "prek", "push"}


def _parse_tasks(raw: list[str] | None) -> set[str] | None:
    """Parse repeated/comma-separated --only / --exclude values."""
    if raw is None:
        return None
    result: set[str] = set()
    for item in raw:
        for task in item.split(","):
            task = task.strip()
            if not task:
                continue
            if task not in VALID_TASKS:
                print(f"Warning: unknown task {task!r}", file=sys.stderr)
            result.add(task)
    return result


def _run_all(
    target: Path,
    only: set[str] | None,
    exclude: set[str],
    delay: float = 0.0,
    blank_lines: bool = True,
    all_repos: bool = False,
    show_path: bool = False,
) -> None:
    if all_repos:
        repos = iter_all_repos(target)
        if not repos:
            print(f"No git repositories found in {target}")
            return
    else:
        repos = iter_repos(target)
        if not repos:
            print(f"No .faterc or faterc files found in {target}")
            return

    prek_rev_cache: dict[str, str] = {}
    for i, entry in enumerate(repos):
        if i > 0 and delay:
            time.sleep(delay)
        if i > 0 and blank_lines:
            print()
        try:
            path_prefix = ""
            if show_path:
                try:
                    parent = entry.path.relative_to(target).parent
                    path_prefix = str(parent) + "/" if str(parent) != "." else ""
                except ValueError:
                    path_prefix = str(entry.path.parent) + "/"
            if not print_repo_status(entry.path, path_prefix=path_prefix):
                continue
            run_repo(entry, only=only, exclude=exclude, prek_rev_cache=prek_rev_cache)
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

    run_repo(RepoEntry.from_faterc(git_root, faterc))


def cmd_gamble(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    exclude: set[str] = set() if args.push else {"push"}
    _run_all(
        target,
        only=None,
        exclude=exclude,
        delay=args.delay,
        all_repos=args.all,
        show_path=args.show_path,
    )


def cmd_list(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    _run_all(
        target,
        only=set(),
        exclude=set(),
        delay=args.delay,
        blank_lines=False,
        all_repos=args.all,
        show_path=args.show_path,
    )


def cmd_pull(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    _run_all(
        target,
        only={"pull"},
        exclude=set(),
        delay=args.delay,
        all_repos=args.all,
        show_path=args.show_path,
    )


def cmd_push(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    _run_all(
        target,
        only={"push"},
        exclude=set(),
        delay=args.delay,
        all_repos=args.all,
        show_path=args.show_path,
    )


def cmd_multirun(args: argparse.Namespace) -> None:
    target = Path(args.directory).resolve() if args.directory else Path.cwd()
    only = _parse_tasks(args.only)
    exclude = _parse_tasks(args.exclude) or set()
    _run_all(
        target,
        only=only,
        exclude=exclude,
        delay=args.delay,
        all_repos=args.all,
        show_path=args.show_path,
    )


def cmd_init(args: argparse.Namespace) -> None:
    cwd = Path.cwd()

    if not (cwd / ".git").exists():
        print("Error: not at the root of a git repository", file=sys.stderr)
        sys.exit(1)

    if find_faterc(cwd) is not None:
        print("Error: .faterc or faterc already exists", file=sys.stderr)
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
        "init", aliases=["i"], help="Initialize .faterc in the current directory."
    )
    p_init.add_argument(
        "--visible",
        action="store_true",
        default=False,
        help="create faterc instead of .faterc",
    )
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser(
        "run", aliases=["r"], help="Run fate on a single repository."
    )
    p_run.add_argument("directory", nargs="?", default=None)
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser(
        "list", aliases=["l", "ls"], help="Show repo statuses without running."
    )
    p_list.add_argument("directory", nargs="?", default=None)
    p_list.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="Delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_list.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    p_list.add_argument(
        "-s",
        "--show-path",
        action="store_true",
        default=False,
        help="Show each repo's path relative to the search directory",
    )
    p_list.set_defaults(func=cmd_list)

    p_pull = sub.add_parser("pull", help="Run only the pull task on all repositories.")
    p_pull.add_argument("directory", nargs="?", default=None)
    p_pull.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_pull.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    p_pull.add_argument(
        "-s",
        "--show-path",
        action="store_true",
        default=False,
        help="Show each repo's path relative to the search directory",
    )
    p_pull.set_defaults(func=cmd_pull)

    p_gamble = sub.add_parser(
        "gamble", aliases=["g"], help="Run all tasks except push on all repositories."
    )
    p_gamble.add_argument("directory", nargs="?", default=None)
    p_gamble.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_gamble.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="Also run push (= multirun with no exclusions), legacy option",
    )
    p_gamble.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    p_gamble.add_argument(
        "-s",
        "--show-path",
        action="store_true",
        default=False,
        help="Show each repo's path relative to the search directory",
    )
    p_gamble.set_defaults(func=cmd_gamble)

    p_push = sub.add_parser("push", help="Run only the push task on all repositories.")
    p_push.add_argument("directory", nargs="?", default=None)
    p_push.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_push.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    p_push.add_argument(
        "-s",
        "--show-path",
        action="store_true",
        default=False,
        help="Show each repo's path relative to the search directory",
    )
    p_push.set_defaults(func=cmd_push)

    p_multirun = sub.add_parser(
        "multirun",
        aliases=["m"],
        help="Run all repositories with optional task filters.",
    )
    p_multirun.add_argument("directory", nargs="?", default=None)
    p_multirun.add_argument(
        "-d",
        "--delay",
        type=_parse_duration,
        default=0.0,
        metavar="DURATION",
        help="delay between repos (e.g. 1s, 500ms, 2m)",
    )
    p_multirun.add_argument(
        "-o",
        "--only",
        action="append",
        metavar="TASKS",
        help="Only run these tasks, comma-separated (e.g. pull,push). Repeatable.",
    )
    p_multirun.add_argument(
        "-e",
        "--exclude",
        action="append",
        metavar="TASKS",
        help="Skip these tasks, comma-separated. Repeatable.",
    )
    p_multirun.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Include all git repos under the directory, even without .faterc",
    )
    p_multirun.add_argument(
        "-s",
        "--show-path",
        action="store_true",
        default=False,
        help="Show each repo's path relative to the search directory",
    )
    p_multirun.set_defaults(func=cmd_multirun)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
