from __future__ import annotations

import json

import pre_commit.constants as C
from pre_commit.clientlib import load_config
from pre_commit.commands.diagnose import _diagnose_hook
from pre_commit.commands.diagnose import _format_healthy
from pre_commit.commands.diagnose import diagnose
from pre_commit.repository import all_hooks
from testing.fixtures import make_config_from_repo
from testing.fixtures import make_repo
from testing.fixtures import sample_local_config
from testing.fixtures import sample_meta_config
from testing.fixtures import write_config


def _pre_clone(store, cap_out):
    """Trigger clone so subsequent calls don't emit log noise."""
    all_hooks(load_config(C.CONFIG_FILE), store)
    cap_out.get()  # discard log output


def test_format_healthy():
    assert _format_healthy(None) == 'n/a'
    assert _format_healthy(True) == 'healthy'
    assert _format_healthy('version mismatch') == (
        'UNHEALTHY - version mismatch'
    )


def test_diagnose_cloned_repo_table(
        tempdir_factory, store, in_git_dir, cap_out,
):
    path = make_repo(tempdir_factory, 'script_hooks_repo')
    config = make_config_from_repo(path)
    write_config('.', config)

    ret = diagnose(C.CONFIG_FILE, store)
    assert ret == 0
    out = cap_out.get()

    assert f'repo: file://{path}' in out
    assert 'rev:' in out
    assert 'hook: bash_hook' in out
    assert 'language:        unsupported_script' in out
    assert 'install_dir:     n/a' in out
    assert 'healthy:         n/a' in out


def test_diagnose_cloned_repo_json(
        tempdir_factory, store, in_git_dir, cap_out,
):
    path = make_repo(tempdir_factory, 'script_hooks_repo')
    config = make_config_from_repo(path)
    write_config('.', config)

    _pre_clone(store, cap_out)
    ret = diagnose(C.CONFIG_FILE, store, output_json=True)
    assert ret == 0
    data = json.loads(cap_out.get())

    assert len(data) == 1
    repo_group = data[0]
    assert repo_group['repo'] == f'file://{path}'
    assert repo_group['rev'] is not None

    hooks = repo_group['hooks']
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook['id'] == 'bash_hook'
    assert hook['name'] == 'Bash hook'
    assert hook['language'] == 'unsupported_script'
    assert hook['language_version'] == 'default'
    assert hook['additional_dependencies'] == []
    assert hook['install_dir'] is None
    assert hook['healthy'] is None


def test_diagnose_local_repo(store, in_git_dir, cap_out):
    write_config('.', sample_local_config())

    ret = diagnose(C.CONFIG_FILE, store)
    assert ret == 0
    out = cap_out.get()

    assert 'repo: local' in out
    assert 'hook: do_not_commit' in out
    assert 'language:        pygrep' in out
    assert 'install_dir:     n/a' in out
    assert 'healthy:         n/a' in out
    # local repos have no rev
    for line in out.splitlines():
        assert not line.startswith('rev:')


def test_diagnose_local_repo_json(store, in_git_dir, cap_out):
    write_config('.', sample_local_config())

    ret = diagnose(C.CONFIG_FILE, store, output_json=True)
    assert ret == 0
    data = json.loads(cap_out.get())

    assert len(data) == 1
    assert data[0]['repo'] == 'local'
    assert data[0]['rev'] is None
    hook = data[0]['hooks'][0]
    assert hook['id'] == 'do_not_commit'
    assert hook['language'] == 'pygrep'
    assert hook['install_dir'] is None
    assert hook['healthy'] is None


def test_diagnose_meta_repo(store, in_git_dir, cap_out):
    write_config('.', sample_meta_config())

    ret = diagnose(C.CONFIG_FILE, store)
    assert ret == 0
    out = cap_out.get()

    assert 'repo: meta' in out
    assert 'hook: check-useless-excludes' in out
    assert 'language:        unsupported' in out


def test_diagnose_python_hook_not_installed(
        tempdir_factory, store, in_git_dir, cap_out,
):
    path = make_repo(tempdir_factory, 'python_hooks_repo')
    config = make_config_from_repo(path)
    write_config('.', config)

    _pre_clone(store, cap_out)
    ret = diagnose(C.CONFIG_FILE, store, output_json=True)
    assert ret == 0
    data = json.loads(cap_out.get())

    hook = data[0]['hooks'][0]
    assert hook['id'] == 'foo'
    assert hook['language'] == 'python'
    assert hook['install_dir'] is not None
    assert 'py_env' in hook['install_dir']
    assert hook['healthy'] == 'not installed'


def test_diagnose_python_hook_not_installed_table(
        tempdir_factory, store, in_git_dir, cap_out,
):
    path = make_repo(tempdir_factory, 'python_hooks_repo')
    config = make_config_from_repo(path)
    write_config('.', config)

    ret = diagnose(C.CONFIG_FILE, store)
    assert ret == 0
    out = cap_out.get()

    assert 'language:        python' in out
    assert 'healthy:         UNHEALTHY - not installed' in out


def test_diagnose_mixed_repos(
        tempdir_factory, store, in_git_dir, cap_out,
):
    path = make_repo(tempdir_factory, 'script_hooks_repo')
    config = {
        'repos': [
            make_config_from_repo(path),
            sample_local_config(),
        ],
    }
    write_config('.', config)

    _pre_clone(store, cap_out)
    ret = diagnose(C.CONFIG_FILE, store, output_json=True)
    assert ret == 0
    data = json.loads(cap_out.get())

    assert len(data) == 2
    assert data[0]['repo'] == f'file://{path}'
    assert data[0]['rev'] is not None
    assert data[0]['hooks'][0]['id'] == 'bash_hook'
    assert data[1]['repo'] == 'local'
    assert data[1]['rev'] is None
    assert data[1]['hooks'][0]['id'] == 'do_not_commit'


def test_diagnose_hook_no_env(store, in_git_dir):
    """Hooks with no ENVIRONMENT_DIR report null install_dir and healthy."""
    write_config('.', sample_local_config())

    hooks = all_hooks(load_config(C.CONFIG_FILE), store)
    info = _diagnose_hook(hooks[0])
    assert info['install_dir'] is None
    assert info['healthy'] is None
