from __future__ import annotations

import json
import os.path
import time
from collections.abc import MutableMapping
from unittest import mock

import pytest

import pre_commit.constants as C
from pre_commit.commands.run import run
from pre_commit.run_report import HookResult
from pre_commit.run_report import RunReport
from pre_commit.run_report import RunResult
from testing.fixtures import add_config_to_repo
from testing.fixtures import make_consuming_repo
from testing.fixtures import modify_config
from testing.util import cwd
from testing.util import run_opts


# ── data-model unit tests ──────────────────────────────────────────────


class TestHookResult:
    def test_to_dict(self):
        hr = HookResult(
            hook_id='myid',
            hook_name='My Hook',
            status='passed',
            files=('a.py', 'b.py'),
            duration_s=1.23,
            return_code=0,
            files_modified=False,
            diff=None,
        )
        d = hr.to_dict()
        assert d['hook_id'] == 'myid'
        assert d['hook_name'] == 'My Hook'
        assert d['status'] == 'passed'
        assert d['files'] == ['a.py', 'b.py']
        assert d['duration_s'] == 1.23
        assert d['return_code'] == 0
        assert d['files_modified'] is False
        assert d['diff'] is None

    def test_to_dict_with_diff(self):
        hr = HookResult(
            hook_id='fmt',
            hook_name='Formatter',
            status='failed',
            files=('c.py',),
            duration_s=0.5,
            return_code=0,
            files_modified=True,
            diff='diff --git a/c.py b/c.py\n',
        )
        d = hr.to_dict()
        assert d['files_modified'] is True
        assert d['diff'] == 'diff --git a/c.py b/c.py\n'
        assert d['status'] == 'failed'


class TestRunReport:
    def _make_report(self):
        return RunReport(
            results=[
                HookResult('a', 'A', 'passed', ('x.py',), 0.1, 0, False, None),
                HookResult('b', 'B', 'failed', ('y.py',), 0.2, 1, False, None),
            ],
            retval=1,
        )

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d['retval'] == 1
        assert len(d['results']) == 2
        assert d['results'][0]['hook_id'] == 'a'
        assert d['results'][1]['hook_id'] == 'b'

    def test_to_json_roundtrip(self):
        report = self._make_report()
        j = report.to_json()
        d = json.loads(j)
        assert d['retval'] == 1
        assert d['results'][0]['status'] == 'passed'
        assert d['results'][1]['status'] == 'failed'

    def test_write_json(self, tmp_path):
        report = self._make_report()
        path = str(tmp_path / 'report.json')
        report.write_json(path)
        with open(path) as f:
            d = json.load(f)
        assert d['retval'] == 1
        assert len(d['results']) == 2


class TestRunResult:
    def test_is_int(self):
        r = RunResult(0)
        assert isinstance(r, int)
        assert r == 0
        assert not r

    def test_nonzero(self):
        r = RunResult(1)
        assert r == 1
        assert bool(r) is True

    def test_bitwise_or(self):
        r = RunResult(0)
        assert r | 1 == 1
        assert 0 | r == 0

    def test_report_attribute(self):
        report = RunReport(results=[], retval=0)
        r = RunResult(0, report)
        assert r.report is report

    def test_report_default_none(self):
        r = RunResult(0)
        assert r.report is None

    def test_works_as_return_code(self):
        # Simulates the main() usage: raise SystemExit(run(...))
        r = RunResult(0)
        assert int(r) == 0


# ── integration tests ──────────────────────────────────────────────────


def stage_a_file(filename='foo.py'):
    from pre_commit.util import cmd_output
    open(filename, 'a').close()
    cmd_output('git', 'add', filename)


def _do_run(cap_out, store, repo, args, environ={}, config_file=C.CONFIG_FILE):
    with cwd(repo):
        ret = run(config_file, store, args, environ=environ)
    printed = cap_out.get_bytes()
    return ret, printed


@pytest.fixture
def repo_with_passing_hook(tempdir_factory):
    git_path = make_consuming_repo(tempdir_factory, 'script_hooks_repo')
    with cwd(git_path):
        yield git_path


@pytest.fixture
def repo_with_failing_hook(tempdir_factory):
    git_path = make_consuming_repo(tempdir_factory, 'failing_hook_repo')
    with cwd(git_path):
        yield git_path


class TestRunResultIntegration:
    def test_run_returns_run_result(
            self, cap_out, store, repo_with_passing_hook,
    ):
        stage_a_file()
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, run_opts(),
        )
        assert isinstance(ret, RunResult)
        assert ret.report is not None
        assert isinstance(ret.report, RunReport)

    def test_passing_hook_report_fields(
            self, cap_out, store, repo_with_passing_hook,
    ):
        stage_a_file()
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, run_opts(),
        )
        assert ret == 0
        report = ret.report
        assert report.retval == 0
        assert len(report.results) == 1

        hr = report.results[0]
        assert hr.hook_id == 'bash_hook'
        assert hr.hook_name == 'Bash hook'
        assert hr.status == 'passed'
        assert hr.return_code == 0
        assert hr.files_modified is False
        assert hr.diff is None
        assert hr.duration_s is not None
        assert hr.duration_s >= 0
        assert 'foo.py' in hr.files

    def test_failing_hook_report_fields(
            self, cap_out, store, repo_with_failing_hook,
    ):
        stage_a_file()
        ret, _ = _do_run(
            cap_out, store, repo_with_failing_hook, run_opts(),
        )
        assert ret == 1
        report = ret.report
        assert report.retval == 1
        assert len(report.results) == 1

        hr = report.results[0]
        assert hr.hook_id == 'failing_hook'
        assert hr.status == 'failed'
        assert hr.return_code != 0

    def test_skipped_hook_report(
            self, cap_out, store, repo_with_passing_hook,
    ):
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, run_opts(),
            {'SKIP': 'bash_hook'},
        )
        assert ret == 0
        hr = ret.report.results[0]
        assert hr.status == 'skipped'
        assert hr.duration_s is None
        assert hr.return_code == 0
        assert hr.files_modified is False

    def test_no_files_skipped_report(
            self, cap_out, store, repo_with_passing_hook,
    ):
        # No staged files -> (no files to check) Skipped
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, run_opts(),
        )
        assert ret == 0
        hr = ret.report.results[0]
        assert hr.status == 'skipped'
        assert hr.duration_s is None

    def test_files_modified_report(
            self, cap_out, store, tempdir_factory,
    ):
        git_path = make_consuming_repo(
            tempdir_factory, 'modified_file_returns_zero_repo',
        )
        with cwd(git_path):
            stage_a_file('bar.py')
            ret, _ = _do_run(cap_out, store, git_path, run_opts())
        assert ret == 1
        modified_hooks = [
            r for r in ret.report.results if r.files_modified
        ]
        assert len(modified_hooks) >= 1
        for hr in modified_hooks:
            assert hr.diff is not None
            assert hr.status == 'failed'

    def test_duration_recorded(
            self, cap_out, store, repo_with_passing_hook,
    ):
        stage_a_file()
        with mock.patch.object(time, 'monotonic', side_effect=(1.0, 2.5)):
            ret, _ = _do_run(
                cap_out, store, repo_with_passing_hook, run_opts(),
            )
        assert ret.report.results[0].duration_s == 1.5


class TestReportJson:
    def test_report_json_passing(
            self, cap_out, store, repo_with_passing_hook, tmp_path,
    ):
        stage_a_file()
        report_path = str(tmp_path / 'report.json')
        opts = run_opts(report_json=report_path)
        ret, _ = _do_run(cap_out, store, repo_with_passing_hook, opts)
        assert ret == 0

        with open(report_path) as f:
            data = json.load(f)

        assert data['retval'] == 0
        assert len(data['results']) == 1
        assert data['results'][0]['hook_id'] == 'bash_hook'
        assert data['results'][0]['status'] == 'passed'

    def test_report_json_failing(
            self, cap_out, store, repo_with_failing_hook, tmp_path,
    ):
        stage_a_file()
        report_path = str(tmp_path / 'report.json')
        opts = run_opts(report_json=report_path)
        ret, _ = _do_run(cap_out, store, repo_with_failing_hook, opts)
        assert ret == 1

        with open(report_path) as f:
            data = json.load(f)

        assert data['retval'] == 1
        assert data['results'][0]['status'] == 'failed'
        assert data['results'][0]['return_code'] != 0
        assert data['results'][0]['files'] == ['foo.py']

    def test_report_json_not_written_when_unset(
            self, cap_out, store, repo_with_passing_hook, tmp_path,
    ):
        stage_a_file()
        report_path = str(tmp_path / 'report.json')
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, run_opts(),
        )
        assert not os.path.exists(report_path)

    def test_report_json_with_skip(
            self, cap_out, store, repo_with_passing_hook, tmp_path,
    ):
        report_path = str(tmp_path / 'report.json')
        opts = run_opts(report_json=report_path)
        ret, _ = _do_run(
            cap_out, store, repo_with_passing_hook, opts,
            {'SKIP': 'bash_hook'},
        )
        with open(report_path) as f:
            data = json.load(f)
        assert data['results'][0]['status'] == 'skipped'


class TestReportPreservesSemantics:
    def test_fail_fast_with_report(
            self, cap_out, store, repo_with_failing_hook, tmp_path,
    ):
        with modify_config() as config:
            config['repos'][0]['hooks'] *= 2
        stage_a_file()

        report_path = str(tmp_path / 'report.json')
        opts = run_opts(fail_fast=True, report_json=report_path)
        ret, printed = _do_run(
            cap_out, store, repo_with_failing_hook, opts,
        )
        assert ret == 1
        # fail_fast: only one hook ran
        assert printed.count(b'Failing hook') == 1
        # report still written
        with open(report_path) as f:
            data = json.load(f)
        assert data['retval'] == 1
        # only one result because fail_fast stopped after the first
        assert len(data['results']) == 1
        assert data['results'][0]['status'] == 'failed'

    def test_show_diff_on_failure_with_report(
            self, capfd, cap_out, store, tempdir_factory, tmp_path,
    ):
        git_path = make_consuming_repo(
            tempdir_factory, 'modified_file_returns_zero_repo',
        )
        with cwd(git_path):
            stage_a_file('bar.py')
            report_path = str(tmp_path / 'report.json')
            opts = run_opts(
                show_diff_on_failure=True,
                report_json=report_path,
            )
            ret, printed = _do_run(cap_out, store, git_path, opts)

        assert ret == 1
        assert b'All changes made by hooks:' in printed
        out, _ = capfd.readouterr()
        assert 'diff --git' in out

        # report is also written
        with open(report_path) as f:
            data = json.load(f)
        assert data['retval'] == 1
        assert any(r['files_modified'] for r in data['results'])

    def test_fail_fast_per_hook_with_report(
            self, cap_out, store, repo_with_failing_hook, tmp_path,
    ):
        with modify_config() as config:
            config['repos'][0]['hooks'] *= 2
            config['repos'][0]['hooks'][0]['fail_fast'] = True
        stage_a_file()

        report_path = str(tmp_path / 'report.json')
        opts = run_opts(report_json=report_path)
        ret, printed = _do_run(
            cap_out, store, repo_with_failing_hook, opts,
        )
        assert printed.count(b'Failing hook') == 1
        with open(report_path) as f:
            data = json.load(f)
        assert len(data['results']) == 1
