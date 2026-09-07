import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from unittest.mock import Mock, call, patch

import pytest
from pytest import CaptureFixture, MonkeyPatch

from runrms.config import ForwardModelConfig
from runrms.exceptions import RmsRuntimeError
from runrms.executor import ForwardModelExecutor


def _create_config(  # noqa: PLR0913 Too many arguments in function definition (8 > 5)
    iens: int,
    run_path: str,
    project: str,
    workflow: str,
    allow_no_env: bool,
    config_file: str,
    version: str = "14.2.2",
    target_file: str | None = None,
) -> ForwardModelConfig:
    args = Mock()
    args.iens = iens
    args.run_path = run_path
    args.project = project
    args.workflows = [workflow]
    args.version = version
    args.readonly = False
    args.import_path = "import/path"
    args.export_path = "export/path"
    args.allow_no_env = allow_no_env
    args.target_file = target_file
    args.setup = config_file
    args.threads = 1

    config = ForwardModelConfig(args)
    config._site_config.exe = f"{os.getcwd()}/bin/rms"
    return config


def test_run_class(fm_executor_env: Path, capsys: CaptureFixture[str]) -> None:
    action: dict[str, str | int] = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )
    rms = ForwardModelExecutor(config)
    assert rms.run() == 0

    # -----------------------------------------------------------------

    action = {"exit_status": 1}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    workflow_log = rms.config.run_path / "workflow.log"
    workflow_log.touch()
    rms_log = rms.config.run_path / "2024_RMS.log"
    rms_log.touch()

    assert rms.run() == 1
    captured = capsys.readouterr()
    assert "failed with exit status: 1. Typically this means" in captured.err
    assert f"* {workflow_log.resolve()}\n* {rms_log.resolve()}" in captured.err

    # -----------------------------------------------------------------

    action = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    with pytest.raises(RmsRuntimeError) as e:
        assert rms.run() == 0
        assert e.match("target-file")

    # -----------------------------------------------------------------

    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0


def test_run_class_with_existing_target_file(fm_executor_env: Path) -> None:
    target_file = os.path.join(fm_executor_env, "rms_target_file")
    action = {
        "exit_status": 0,
        "target_file": target_file,
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    with open(target_file, "w") as f:
        f.write("This is a dummy target file")

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        target_file=target_file,
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0


def test_run_wrapper(
    fm_executor_env: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    # Overwrite configured wrapper
    wrapper_file_name = f"{fm_executor_env}/bin/disable_foo"
    with open(wrapper_file_name, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("exec ${@:1}\n")
    st = os.stat(wrapper_file_name)
    os.chmod(wrapper_file_name, st.st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{fm_executor_env}/bin:{os.environ['PATH']}")

    action: dict[str, str | int] = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0

    # -----------------------------------------------------------------

    action = {"exit_status": 1}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    (rms.config.run_path / "workflow.log").touch()

    assert rms.run() == 1
    captured = capsys.readouterr()
    assert "failed with exit status: 1. Typically this means" in captured.err

    # -----------------------------------------------------------------

    action = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    with pytest.raises(RmsRuntimeError):
        rms.run()

    # -----------------------------------------------------------------

    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    assert rms.run() == 0


def test_run_version_env(
    test_env_wrapper: Callable[..., str],
    fm_executor_env: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    wrapper_file_name = f"{fm_executor_env}/bin/rms_wrapper"
    with open(wrapper_file_name, "w") as f:
        rms_wrapper = test_env_wrapper(
            expected_path_prefix="/some/path",
            expected_pythonpath="/abc/pythonpath",
        )
        f.write(rms_wrapper)

    st = os.stat(wrapper_file_name)
    os.chmod(wrapper_file_name, st.st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fm_executor_env}/bin:{os.environ['PATH']}")

    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=False,
        version="14.2.2",
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0


def test_license_file_from_wrapper_overwrites(
    test_env_wrapper: Callable[..., str],
    fm_executor_env: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Tests that the batch license file configuration is preferred. This is set by
    the fm_executor_env, so we unset the environment variable to check.

    The configuration has
        batch_lm_license_file: /license/file.lic
    We want this to overwrite the license file set."""

    monkeypatch.setenv("LM_LICENSE_FILE", "/overwrite/this.lic")
    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=False,
        version="14.2.2",
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 1
    assert rms._exec_env.get("LM_LICENSE_FILE", False) == "/license/file.lic"


def test_user_rms_plugins_library_env_var_is_not_preferred(
    test_env_wrapper: Callable[..., str],
    fm_executor_env: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Tests that if a user sets the RMS_PLUGINS_LIBRARY environment variable in their
    ERT configuration, it is _not_ preferred over the one in the site configuration.
    This is to ensure a consistent and reproducible environment."""

    # Mock set from ert
    monkeypatch.setenv("RMS_PLUGINS_LIBRARY", "/preferred/user/foo")
    with open(f"{fm_executor_env}/bin/disable_foo", "w", encoding="utf-8") as f:
        disable_foo = test_env_wrapper(
            expected_rms_plugins="/preferred/user/foo",
            expected_lm_license_file="foo.lic",
        )
        f.write(disable_foo)

    patch("sys.argv", ["bin/rms"])
    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=False,
        version="14.2.2",
        target_file="some_file",
        config_file="runrms.yml",
    )
    rms = ForwardModelExecutor(config)

    assert rms.run() == 0
    assert rms._exec_env.get("RMS_PLUGINS_LIBRARY", False) == "/foo/plugins"


def test_run_allow_no_env(fm_executor_env: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"{fm_executor_env}/bin:{os.environ['PATH']}")

    action = {
        "exit_status": 0,
        "target_file": os.path.join(fm_executor_env, "some_file"),
    }
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=False,
        version="14.2.2",
        target_file="some_file",
        config_file="runrms.yml",
    )

    config._version_given = "non-existing"
    rms = ForwardModelExecutor(config)
    with pytest.raises(RmsRuntimeError, match="non-existing"):
        assert rms.run() == 0

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        version="14.2.2",
        target_file="some_file",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0


def test_rms_job_script_parser(fm_executor_env: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("RMS_TEST_VAR", "fdsgfdgfdsgfds")

    action = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    rms_exec = "runrms"
    subprocess.check_call(
        [
            rms_exec,
            "project",
            "--batch",
            "workflow",
            "--run-path",
            "run_path",
            "--iens",
            "0",
            "--version",
            "14.2.2",
            "--import-path",
            "./",
            "--export-path",
            "./",
            "--setup",
            "runrms.yml",
        ]
    )

    subprocess.check_call(
        [
            rms_exec,
            "project",
            "-batch",
            "workflow",
            "--run-path",
            "run_path",
            "--iens",
            "0",
            "--version",
            "14.2.2",
            "--allow-no-env",
            "--setup",
            "runrms.yml",
        ]
    )


@pytest.mark.parametrize("exit_status", [1, 2, 137])
def test_print_failure_when_no_logs_found_in_rms_model(
    exit_status: int, fm_executor_env: Path, capsys: CaptureFixture[str]
) -> None:
    action = {"exit_status": exit_status}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )
    rms = ForwardModelExecutor(config)
    assert rms.run() == exit_status

    captured = capsys.readouterr()
    assert f"failed with exit status: {exit_status}" in captured.err
    if exit_status == 137:
        assert (
            "This often means that the compute node ran out of memory" in captured.err
        )
    else:
        assert f"* {fm_executor_env}/run_path" in captured.err
        assert "This may mean that the compute node ran out of memory" in captured.err

    for line in captured.err.split("\n"):
        assert line.startswith("\t") is False


def test_print_failure_when_logs_found_in_rms_model(
    fm_executor_env: Path, capsys: CaptureFixture[str]
) -> None:
    action = {"exit_status": 1}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )
    (fm_executor_env / "run_path" / "2025_RMS.log").touch()
    (fm_executor_env / "run_path" / "workflow.log").touch()
    (fm_executor_env / "run_path" / "20260715-092055-zdtHhM-RMS.log").touch()

    rms = ForwardModelExecutor(config)
    assert rms.run() == 1

    captured = capsys.readouterr()
    assert "failed with exit status: 1" in captured.err
    assert f"* {fm_executor_env}/run_path" in captured.err
    assert "workflow.log" in captured.err
    assert "2025_RMS.log" in captured.err
    assert "20260715-092055-zdtHhM-RMS.log" not in captured.err
    assert "YYYYMMDD-HHMMSS-XXXXXX-RMS.log" not in captured.err

    for line in captured.err.split("\n"):
        assert line.startswith("\t") is False


def test_print_failure_when_job_failure_in_logs(
    fm_executor_env: Path,
    workflow_log: Callable[[Path], Path],
    capsys: CaptureFixture[str],
) -> None:
    """Job failure logs should be printed when job failures in the logfiles."""
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_log(run_path)

    rms = ForwardModelExecutor(config)
    action = {"exit_status": 1}
    with open(run_path / "action.json", "w") as f:
        f.write(json.dumps(action))

    with patch.object(
        rms, "_find_job_failures", return_value="Failed job"
    ) as mocked_find_job_failures:
        rms.run()
        captured = capsys.readouterr()

        assert "workflow.log" in captured.err
        assert (
            "The following logs regarding failed jobs were found in the log file"
            in captured.err
        )
        assert "Failed job" in captured.err

        mocked_find_job_failures.assert_called_once()


def test_print_failure_skips_logfiles_for_complete_rms_run(
    fm_executor_env: Path,
) -> None:
    """The logfiles for the complete RMS run should not be checked.

    Skip looking for job failures in log files on the format
    YYYYMMDD-HHMMSS-XXXXXX-RMS.log as these are unstructured
    and will not give any matching results.
    """
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = Path(fm_executor_env / "run_path")
    (run_path / "20260101-000000-zdtHhM-RMS.log").touch()
    (run_path / "20260715-092055-zdtHhM-RMS.log").touch()
    (run_path / "20262412-135454-jPbOrM-RMS.log").touch()
    (run_path / "20261705-133409-NgliXA-RMS.log").touch()

    rms = ForwardModelExecutor(config)
    action = {"exit_status": 1}
    with open(run_path / "action.json", "w") as f:
        f.write(json.dumps(action))

    with patch.object(
        rms, "_find_job_failures", return_value="Failed"
    ) as mocked_find_job_failures:
        rms.run()
        mocked_find_job_failures.assert_not_called()

    (run_path / "workflow.log").touch()
    with patch.object(
        rms, "_find_job_failures", return_value=""
    ) as mocked_find_job_failures:
        rms.run()
        mocked_find_job_failures.assert_called_once_with(run_path / "workflow.log")


def test_print_failure_print_first_logfile_when_more_than_one_log_has_failures(
    fm_executor_env: Path,
    workflow_log: Callable[[Path], Path],
    capsys: CaptureFixture[str],
) -> None:
    """Only the first logfile with failures should be printed.

    When the workflow.log file exists and has failures, only failure logs
    from this file should be printed as those are the most relevant ones.

    If workflow.log does not have any failures it will look for failures
    in the other logfiles, printing the first failures it can find.
    """
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_log_path: Path = workflow_log(run_path)

    other_log_path = run_path / "other_log.log"
    with open(other_log_path, "w", encoding="utf-8") as f:
        f.write(
            dedent(
                """
                <pre>
                Job 1 - Some other job that failed - for project realization 0
                </pre>
            """
            )
        )

    rms = ForwardModelExecutor(config)
    action = {"exit_status": 1}
    with open(run_path / "action.json", "w") as f:
        f.write(json.dumps(action))

    with patch.object(
        rms, "_find_job_failures", return_value="Failed job"
    ) as mocked_find_job_failures:
        rms.run()
        captured = capsys.readouterr()

        assert "workflow.log" in captured.err
        assert "other_log" in captured.err

        assert (
            "The following logs regarding failed jobs were found in the log file"
            in captured.err
        )
        mocked_find_job_failures.assert_called_once_with(workflow_log_path)

        # Verify that deleting workflow.log will print failure from other file
        mocked_find_job_failures.reset_mock()
        workflow_log_path.unlink()
        rms.run()

        assert (
            "The following logs regarding failed jobs were found in the log file"
            in captured.err
        )
        mocked_find_job_failures.assert_called_once_with(other_log_path)


def test_print_failure_no_job_failure_printed_when_no_job_failure_in_logs(
    capsys: CaptureFixture[str], fm_executor_env: Path
) -> None:
    """No job failure logs should be printed when no job failures in the logfiles."""
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_path = run_path / "workflow.log"
    other_log_path = run_path / "other_log.log"
    workflow_path.touch()
    other_log_path.touch()

    rms = ForwardModelExecutor(config)
    action = {"exit_status": 1}
    with open(run_path / "action.json", "w") as f:
        f.write(json.dumps(action))

    with patch.object(
        rms, "_find_job_failures", return_value=""
    ) as mocked_find_job_failures:
        rms.run()
        captured = capsys.readouterr()

        mocked_find_job_failures.assert_has_calls(
            [call(workflow_path), call(other_log_path)]
        )
        assert (
            "The following logs regarding failed jobs were found in the log file"
            not in captured.err
        )


def test_print_failure_handles_find_job_failures_exceptions(
    capsys: CaptureFixture[str], fm_executor_env: Path
) -> None:
    """Exceptions occurring while looking for job failures should be handled gracefully.

    Any exceptions occurring while searching through the log files for job failures,
    should be handled gracefully and not affect any of the other printing operations.
    """
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_path = run_path / "workflow.log"
    other_log_path = run_path / "other_log.log"
    workflow_path.touch()
    other_log_path.touch()

    rms = ForwardModelExecutor(config)
    action = {"exit_status": 1}
    with open(run_path / "action.json", "w") as f:
        f.write(json.dumps(action))

    with patch.object(
        rms, "_find_job_failures", side_effect=FileNotFoundError("Log file not found.")
    ) as mocked_find_job_failures:
        rms.run()
        captured = capsys.readouterr()

        mocked_find_job_failures.assert_has_calls(
            [call(workflow_path), call(other_log_path)]
        )
        assert (
            "The following logs regarding failed jobs were found in the log file"
            not in captured.err
        )


def test_find_job_failures_returns_failures(
    fm_executor_env: Path, workflow_log: Callable[[Path], Path]
) -> None:
    """Test that job failures are found and returned in expected format."""
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_log(run_path)

    rms = ForwardModelExecutor(config)
    job_failed_msg = rms._find_job_failures(run_path / "workflow.log")

    assert "Job: failing_python_job.py failed." in job_failed_msg
    assert (
        "ModuleNotFoundError: No module named 'non_existing_package'" in job_failed_msg
    )
    assert "Failed job" in job_failed_msg


def test_find_job_failures_removes_html_tags(
    fm_executor_env: Path, workflow_log: Callable[[Path], Path]
) -> None:
    """Test that html tags are removed from the returned job failure string."""
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    run_path = fm_executor_env / "run_path"
    workflow_log(run_path)

    rms = ForwardModelExecutor(config)
    logfile_path = run_path / "workflow.log"
    job_failed_msg = rms._find_job_failures(logfile_path)

    failed_msg_lines = [x for x in job_failed_msg.splitlines() if x != ""]
    assert "<pre>" not in failed_msg_lines

    # Check that each message line matches a log line when html tags are removed
    with open(logfile_path) as logfile:
        for line in logfile:
            if any(fail_line in line for fail_line in failed_msg_lines):
                if "<" in line and ">" in line:
                    assert line not in failed_msg_lines
                    line_without_html_tag = re.sub(r"<[^>]*>", "", line)
                    assert line_without_html_tag.strip() in failed_msg_lines
                else:
                    assert line.strip() in failed_msg_lines


def test_find_job_failures_empty_string_when_no_job_failures(
    fm_executor_env: Path,
) -> None:
    """Test that the failing message string is empty when no job failures in logs."""
    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        config_file="runrms.yml",
    )

    logfile_path = fm_executor_env / "run_path/workflow.log"
    with open(logfile_path, "w", encoding="utf-8") as f:
        f.write(
            dedent(
                """
                <pre>
                Job 1 - Some job that runs OK - for project realization 0
                </pre>
            """
            )
        )

    rms = ForwardModelExecutor(config)
    job_failed_msg = rms._find_job_failures(logfile_path)

    assert job_failed_msg == ""


def test_lm_license_server_overwritten_during_batch(fm_executor_env: Path) -> None:
    action = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        version="14.2.2",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0

    with open("run_path/env.json") as f:
        env = json.load(f)

    assert env["LM_LICENSE_FILE"] == "/license/file.lic"


def test_runrms_exec_mode_set_during_batch(fm_executor_env: Path) -> None:
    action = {"exit_status": 0}
    with open("run_path/action.json", "w") as f:
        f.write(json.dumps(action))

    config = _create_config(
        iens=0,
        project="project",
        workflow="workflow",
        run_path="run_path",
        allow_no_env=True,
        version="14.2.2",
        config_file="runrms.yml",
    )

    rms = ForwardModelExecutor(config)
    assert rms.run() == 0

    with open("run_path/env.json") as f:
        env = json.load(f)

    assert env["RUNRMS_EXEC_MODE"] == "batch"
