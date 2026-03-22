from pathlib import Path

import git

from fate.color import colorize


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
    name = colorize("1;34", repo_root.name)

    if not repo.remotes:
        print(f"{name} (no remote)")
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
        parts.append(colorize("36", f"↑{ahead}"))
    if behind:
        parts.append(colorize("36", f"↓{behind}"))
    if staged:
        parts.append(colorize("32", f"●{staged}"))
    if dirty:
        parts.append(colorize("31", f"✚{dirty}"))
    if untracked:
        parts.append(colorize("34", f"…{untracked}"))

    status = (" " + " ".join(parts)) if parts else ""
    print(f"{name} {colorize('33', f'({branch})')}{status}")
    return True
