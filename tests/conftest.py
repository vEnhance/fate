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
