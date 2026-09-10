import git
import pytest


@pytest.fixture
def repo(tmp_path):
    """A temporary git repo with one initial commit."""
    r = git.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")
    (tmp_path / "README").write_text("hello")
    r.index.add(["README"])
    r.index.commit("initial commit")
    return r


@pytest.fixture
def repo_with_upstream(repo, tmp_path):
    """A temporary git repo whose branch tracks a bare remote."""
    bare = tmp_path.parent / f"{tmp_path.name}-remote.git"
    git.Repo.init(bare, bare=True)
    branch = repo.active_branch.name
    origin = repo.create_remote("origin", str(bare))
    origin.push(refspec=f"{branch}:{branch}")
    repo.git.branch("--set-upstream-to", f"origin/{branch}", branch)
    return repo
