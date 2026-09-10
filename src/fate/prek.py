import shlex
from pathlib import Path

import tomli

UV_EXPORT_DEFAULT_ARGS = ["--frozen", "--output-file=requirements.txt", "--quiet"]


def _load(prek_toml: Path) -> dict:
    with open(prek_toml, "rb") as f:
        return tomli.load(f)


def prek_revs(prek_toml: Path) -> dict[str, str]:
    """Return {repo_url: rev} for all versioned repos in prek.toml."""
    data = _load(prek_toml)
    return {e["repo"]: e["rev"] for e in data.get("repos", []) if "rev" in e}


def prek_up_to_date(prek_toml: Path, cache: dict[str, str]) -> bool:
    """True if every versioned hook in prek_toml is already at the cached latest rev."""
    revs = prek_revs(prek_toml)
    return bool(revs) and all(cache.get(url) == rev for url, rev in revs.items())


def prek_update_cache(prek_toml: Path, cache: dict[str, str]) -> None:
    """Merge latest revs from prek_toml into cache."""
    cache.update(prek_revs(prek_toml))


def uv_export_command(prek_toml: Path) -> list[str] | None:
    """The command the uv-export hook would run, or None if prek.toml has no such hook."""
    for repo in _load(prek_toml).get("repos", []):
        for hook in repo.get("hooks", []):
            if hook.get("id") == "uv-export":
                entry = shlex.split(hook.get("entry", "uv export"))
                return entry + list(hook.get("args", UV_EXPORT_DEFAULT_ARGS))
    return None


def uv_export_output(command: list[str]) -> str:
    """The file that an `uv export` command writes to."""
    for i, arg in enumerate(command):
        if arg.startswith("--output-file="):
            return arg.split("=", 1)[1]
        if arg in ("--output-file", "-o") and i + 1 < len(command):
            return command[i + 1]
    return "requirements.txt"
