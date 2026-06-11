from __future__ import annotations

import logging
import os.path
import shlex
import shutil
import sqlite3
import stat
import threading
from unittest import mock

import pytest

import pre_commit.constants as C
from pre_commit import git
from pre_commit.store import _get_default_directory
from pre_commit.store import _LOCAL_RESOURCES
from pre_commit.store import Store
from pre_commit.util import CalledProcessError
from pre_commit.util import cmd_output
from testing.fixtures import git_dir
from testing.util import cwd
from testing.util import git_commit
from testing.util import xfailif_windows


def _select_all_configs(store: Store) -> list[str]:
    with store.connect() as db:
        rows = db.execute('SELECT * FROM configs').fetchall()
        return [path for path, in rows]


def _select_all_repos(store: Store) -> list[tuple[str, str, str]]:
    with store.connect() as db:
        return db.execute('SELECT repo, ref, path FROM repos').fetchall()


def test_our_session_fixture_works():
    """There's a session fixture which makes `Store` invariantly raise to
    prevent writing to the home directory.
    """
    with pytest.raises(AssertionError):
        Store()


def test_get_default_directory_defaults_to_home():
    # Not we use the module level one which is not mocked
    ret = _get_default_directory()
    expected = os.path.realpath(os.path.expanduser('~/.cache/pre-commit'))
    assert ret == expected


def test_adheres_to_xdg_specification():
    with mock.patch.dict(
        os.environ, {'XDG_CACHE_HOME': '/tmp/fakehome'},
    ):
        ret = _get_default_directory()
    expected = os.path.realpath('/tmp/fakehome/pre-commit')
    assert ret == expected


def test_uses_environment_variable_when_present():
    with mock.patch.dict(
        os.environ, {'PRE_COMMIT_HOME': '/tmp/pre_commit_home'},
    ):
        ret = _get_default_directory()
    expected = os.path.realpath('/tmp/pre_commit_home')
    assert ret == expected


def test_store_init(store):
    # Should create the store directory
    assert os.path.exists(store.directory)
    # Should create a README file indicating what the directory is about
    with open(os.path.join(store.directory, 'README')) as readme_file:
        readme_contents = readme_file.read()
        for text_line in (
            'This directory is maintained by the pre-commit project.',
            'Learn more: https://github.com/pre-commit/pre-commit',
        ):
            assert text_line in readme_contents


def test_clone(store, tempdir_factory, caplog):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        rev = git.head_rev(path)
        git_commit()

    ret = store.clone(path, rev)
    # Should have printed some stuff
    assert caplog.record_tuples[0][-1].startswith(
        'Initializing environment for ',
    )

    # Should return a directory inside of the store
    assert os.path.exists(ret)
    assert ret.startswith(store.directory)
    # Directory should start with `repo`
    _, dirname = os.path.split(ret)
    assert dirname.startswith('repo')
    # Should be checked out to the rev we specified
    assert git.head_rev(ret) == rev

    # Assert there's an entry in the sqlite db for this
    assert _select_all_repos(store) == [(path, rev, ret)]


def test_warning_for_deprecated_stages_on_init(store, tempdir_factory, caplog):
    manifest = '''\
-   id: hook1
    name: hook1
    language: system
    entry: echo hook1
    stages: [commit, push]
-   id: hook2
    name: hook2
    language: system
    entry: echo hook2
    stages: [push, merge-commit]
'''

    path = git_dir(tempdir_factory)
    with open(os.path.join(path, C.MANIFEST_FILE), 'w') as f:
        f.write(manifest)
    cmd_output('git', 'add', '.', cwd=path)
    git_commit(cwd=path)
    rev = git.head_rev(path)

    store.clone(path, rev)
    assert caplog.record_tuples[1] == (
        'pre_commit',
        logging.WARNING,
        f'repo `{path}` uses deprecated stage names '
        f'(commit, push, merge-commit) which will be removed in a future '
        f'version.  '
        f'Hint: often `pre-commit autoupdate --repo {shlex.quote(path)}` '
        f'will fix this.  '
        f'if it does not -- consider reporting an issue to that repo.',
    )

    # should not re-warn
    caplog.clear()
    store.clone(path, rev)
    assert caplog.record_tuples == []


def test_no_warning_for_non_deprecated_stages_on_init(
        store, tempdir_factory, caplog,
):
    manifest = '''\
-   id: hook1
    name: hook1
    language: system
    entry: echo hook1
    stages: [pre-commit, pre-push]
-   id: hook2
    name: hook2
    language: system
    entry: echo hook2
    stages: [pre-push, pre-merge-commit]
'''

    path = git_dir(tempdir_factory)
    with open(os.path.join(path, C.MANIFEST_FILE), 'w') as f:
        f.write(manifest)
    cmd_output('git', 'add', '.', cwd=path)
    git_commit(cwd=path)
    rev = git.head_rev(path)

    store.clone(path, rev)
    assert logging.WARNING not in {tup[1] for tup in caplog.record_tuples}


def test_clone_cleans_up_on_checkout_failure(store):
    with pytest.raises(Exception) as excinfo:
        # This raises an exception because you can't clone something that
        # doesn't exist!
        store.clone('/i_dont_exist_lol', 'fake_rev')
    assert '/i_dont_exist_lol' in str(excinfo.value)

    repo_dirs = [
        d for d in os.listdir(store.directory) if d.startswith('repo')
    ]
    assert repo_dirs == []


def test_clone_when_repo_already_exists(store, tmp_path):
    # Create a real directory and an entry in the sqlite db that makes it
    # look like the repo has been cloned.
    cached_dir = str(tmp_path.joinpath('cached_repo'))
    os.makedirs(cached_dir)
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            'INSERT INTO repos (repo, ref, path) VALUES (?, ?, ?)',
            ('fake_repo', 'fake_ref', cached_dir),
        )

    assert store.clone('fake_repo', 'fake_ref') == cached_dir


def test_clone_shallow_failure_fallback_to_complete(
    store, tempdir_factory,
    caplog,
):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        rev = git.head_rev(path)
        git_commit()

    # Force shallow clone failure
    def fake_shallow_clone(self, *args, **kwargs):
        raise CalledProcessError(1, (), b'', None)
    store._shallow_clone = fake_shallow_clone

    ret = store.clone(path, rev)

    # Should have printed some stuff
    assert caplog.record_tuples[0][-1].startswith(
        'Initializing environment for ',
    )

    # Should return a directory inside of the store
    assert os.path.exists(ret)
    assert ret.startswith(store.directory)
    # Directory should start with `repo`
    _, dirname = os.path.split(ret)
    assert dirname.startswith('repo')
    # Should be checked out to the rev we specified
    assert git.head_rev(ret) == rev

    # Assert there's an entry in the sqlite db for this
    assert _select_all_repos(store) == [(path, rev, ret)]


def test_clone_tag_not_on_mainline(store, tempdir_factory):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        cmd_output('git', 'checkout', 'master', '-b', 'branch')
        git_commit()
        cmd_output('git', 'tag', 'v1')
        cmd_output('git', 'checkout', 'master')
        cmd_output('git', 'branch', '-D', 'branch')

    # previously crashed on unreachable refs
    store.clone(path, 'v1')


def test_create_when_directory_exists_but_not_db(store):
    # In versions <= 0.3.5, there was no sqlite db causing a need for
    # backward compatibility
    os.remove(store.db_path)
    store = Store(store.directory)
    assert os.path.exists(store.db_path)


def test_create_when_store_already_exists(store):
    # an assertion that this is idempotent and does not crash
    Store(store.directory)


def test_db_repo_name(store):
    assert store.db_repo_name('repo', ()) == 'repo'
    assert store.db_repo_name('repo', ('b', 'a', 'c')) == 'repo:b,a,c'


def test_local_resources_reflects_reality():
    on_disk = {
        res.removeprefix('empty_template_')
        for res in os.listdir('pre_commit/resources')
        if res.startswith('empty_template_')
    }
    assert on_disk == {os.path.basename(x) for x in _LOCAL_RESOURCES}


def test_mark_config_as_used(store, tmpdir):
    with tmpdir.as_cwd():
        f = tmpdir.join('f').ensure()
        store.mark_config_used('f')
        assert _select_all_configs(store) == [f.strpath]


def test_mark_config_as_used_idempotent(store, tmpdir):
    test_mark_config_as_used(store, tmpdir)
    test_mark_config_as_used(store, tmpdir)


def test_mark_config_as_used_does_not_exist(store):
    store.mark_config_used('f')
    assert _select_all_configs(store) == []


def test_mark_config_as_used_roll_forward(store, tmpdir):
    with store.connect() as db:  # simulate pre-1.14.0
        db.executescript('DROP TABLE configs')
    test_mark_config_as_used(store, tmpdir)


@xfailif_windows  # pragma: win32 no cover
def test_mark_config_as_used_readonly(tmpdir):
    cfg = tmpdir.join('f').ensure()
    store_dir = tmpdir.join('store')
    # make a store, then we'll convert its directory to be readonly
    assert not Store(str(store_dir)).readonly  # directory didn't exist
    assert not Store(str(store_dir)).readonly  # directory did exist

    def _chmod_minus_w(p):
        st = os.stat(p)
        os.chmod(p, st.st_mode & ~(stat.S_IWUSR | stat.S_IWOTH | stat.S_IWGRP))

    _chmod_minus_w(store_dir)
    for fname in os.listdir(store_dir):
        assert not os.path.isdir(fname)
        _chmod_minus_w(os.path.join(store_dir, fname))

    store = Store(str(store_dir))
    assert store.readonly
    # should be skipped due to readonly
    store.mark_config_used(str(cfg))
    assert _select_all_configs(store) == []


def test_clone_with_recursive_submodules(store, tmp_path):
    sub = tmp_path.joinpath('sub')
    sub.mkdir()
    sub.joinpath('submodule').write_text('i am a submodule')
    cmd_output('git', '-C', str(sub), 'init', '.')
    cmd_output('git', '-C', str(sub), 'add', '.')
    git.commit(str(sub))

    repo = tmp_path.joinpath('repo')
    repo.mkdir()
    repo.joinpath('repository').write_text('i am a repo')
    cmd_output('git', '-C', str(repo), 'init', '.')
    cmd_output('git', '-C', str(repo), 'add', '.')
    cmd_output('git', '-C', str(repo), 'submodule', 'add', str(sub), 'sub')
    git.commit(str(repo))

    rev = git.head_rev(str(repo))
    ret = store.clone(str(repo), rev)

    assert os.path.exists(ret)
    assert os.path.exists(os.path.join(ret, str(repo), 'repository'))
    assert os.path.exists(os.path.join(ret, str(sub), 'submodule'))


def test_clone_recovers_when_repo_dir_missing(store, tempdir_factory, caplog):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        rev = git.head_rev(path)
        git_commit()

    # First clone succeeds normally
    ret1 = store.clone(path, rev)
    assert os.path.isdir(ret1)
    assert git.head_rev(ret1) == rev

    # Simulate partial PRE_COMMIT_HOME cleanup: remove the repo directory
    # but leave the sqlite record in place
    shutil.rmtree(ret1)
    assert not os.path.exists(ret1)

    caplog.clear()

    # Second clone should detect the stale path, re-clone, and update the db
    ret2 = store.clone(path, rev)
    assert os.path.isdir(ret2)
    assert ret2 != ret1
    assert git.head_rev(ret2) == rev

    # DB should now point to the new path only
    repos = _select_all_repos(store)
    assert repos == [(path, rev, ret2)]

    # Should have logged the re-creation
    messages = [r.message for r in caplog.records]
    assert any('Re-creating missing repo directory' in m for m in messages)
    assert any('Initializing environment' in m for m in messages)


def test_clone_recovery_concurrent_no_duplicate(store, tempdir_factory):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        rev = git.head_rev(path)
        git_commit()

    # Clone once, then delete to create a stale entry
    ret1 = store.clone(path, rev)
    shutil.rmtree(ret1)

    results = []
    errors = []

    def do_clone():
        try:
            results.append(store.clone(path, rev))
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=do_clone)
    t2 = threading.Thread(target=do_clone)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    # Both threads should get valid, identical paths
    assert len(results) == 2
    assert results[0] == results[1]
    assert os.path.isdir(results[0])

    # DB should contain exactly one entry
    repos = _select_all_repos(store)
    assert len(repos) == 1
    assert repos[0] == (path, rev, results[0])


@xfailif_windows  # pragma: win32 no cover
def test_clone_stale_path_readonly_returns_stale(tmpdir):
    store_dir = tmpdir.join('store')
    store = Store(str(store_dir))

    # Insert a stale record pointing to a non-existent directory
    stale_path = str(tmpdir.join('gone'))
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            'INSERT INTO repos (repo, ref, path) VALUES (?, ?, ?)',
            ('repo', 'ref', stale_path),
        )

    # Make store readonly
    def _chmod_minus_w(p):
        st = os.stat(p)
        os.chmod(p, st.st_mode & ~(stat.S_IWUSR | stat.S_IWOTH | stat.S_IWGRP))

    _chmod_minus_w(store_dir)
    for fname in os.listdir(str(store_dir)):
        _chmod_minus_w(os.path.join(str(store_dir), fname))

    ro_store = Store(str(store_dir))
    assert ro_store.readonly

    # Should return the stale path without attempting recovery
    assert ro_store.clone('repo', 'ref') == stale_path


def test_clone_cache_hit_no_reinit(store, tempdir_factory, caplog):
    path = git_dir(tempdir_factory)
    with cwd(path):
        git_commit()
        rev = git.head_rev(path)
        git_commit()

    ret1 = store.clone(path, rev)
    caplog.clear()

    # Second clone should be a fast cache hit with no log output
    ret2 = store.clone(path, rev)
    assert ret2 == ret1
    assert caplog.record_tuples == []
