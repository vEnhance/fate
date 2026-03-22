from pathlib import Path

import tomli


def prek_revs(prek_toml: Path) -> dict[str, str]:
    """Return {repo_url: rev} for all versioned repos in prek.toml."""
    with open(prek_toml, "rb") as f:
        data = tomli.load(f)
    return {e["repo"]: e["rev"] for e in data.get("repos", []) if "rev" in e}


def prek_up_to_date(prek_toml: Path, cache: dict[str, str]) -> bool:
    """True if every versioned hook in prek_toml is already at the cached latest rev."""
    revs = prek_revs(prek_toml)
    return bool(revs) and all(cache.get(url) == rev for url, rev in revs.items())


def prek_update_cache(prek_toml: Path, cache: dict[str, str]) -> None:
    """Merge latest revs from prek_toml into cache."""
    cache.update(prek_revs(prek_toml))
