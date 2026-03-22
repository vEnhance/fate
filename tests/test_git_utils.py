import git

from fate.git_utils import current_branch, find_git_root, has_upstream, is_dirty

# --- find_git_root ---


def test_find_git_root_at_root(tmp_path, repo):
    assert find_git_root(tmp_path) == tmp_path


def test_find_git_root_from_subdir(tmp_path, repo):
    subdir = tmp_path / "sub" / "dir"
    subdir.mkdir(parents=True)
    assert find_git_root(subdir) == tmp_path


def test_find_git_root_not_a_repo(tmp_path):
    non_repo = tmp_path / "notarepo"
    non_repo.mkdir()
    assert find_git_root(non_repo) is None


# --- is_dirty ---


def test_is_dirty_clean(repo):
    assert is_dirty(repo) is False


def test_is_dirty_modified_tracked_file(tmp_path, repo):
    (tmp_path / "README").write_text("modified")
    assert is_dirty(repo) is True


def test_is_dirty_staged_new_file(tmp_path, repo):
    (tmp_path / "new.txt").write_text("new")
    repo.index.add(["new.txt"])
    assert is_dirty(repo) is True


def test_is_dirty_untracked_file_does_not_count(tmp_path, repo):
    (tmp_path / "untracked.txt").write_text("untracked")
    assert is_dirty(repo) is False


# --- current_branch ---


def test_current_branch(repo):
    assert current_branch(repo) == repo.active_branch.name


def test_current_branch_detached_head(tmp_path, repo):
    sha = repo.head.commit.hexsha
    repo.git.checkout(sha)
    assert current_branch(repo) == ""


# --- has_upstream ---


def test_has_upstream_no_remote(repo):
    assert has_upstream(repo) is False


def test_has_upstream_with_remote(tmp_path, repo):
    # Create a second repo to act as remote
    remote_path = tmp_path / "remote"
    git.Repo.init(remote_path, bare=True)
    repo.create_remote("origin", str(remote_path))
    repo.remotes.origin.push(
        refspec=f"{repo.active_branch.name}:{repo.active_branch.name}"
    )
    repo.git.branch("--set-upstream-to", f"origin/{repo.active_branch.name}")
    assert has_upstream(repo) is True
