import glob
import os
import re
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

from runrms.config import ForwardModelConfig
from runrms.exceptions import RmsRuntimeError

from ._rms_executor import RmsExecutionMode, RmsExecutor


@contextmanager
def pushd(path: str | Path) -> Generator[None, None, None]:
    """pushd functionality"""
    cwd_ = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(cwd_)


class ForwardModelExecutor(RmsExecutor[ForwardModelConfig]):
    """
    Class for executing runrms as forward model job
    """

    def __init__(self, config: ForwardModelConfig) -> None:
        super().__init__(config)
        if (license_file := self.config.site_config.batch_lm_license_file) is not None:
            self.update_exec_env("LM_LICENSE_FILE", license_file)

    def _exec_rms(self) -> int:
        """Execute RMS with given environment"""
        args = self.pre_rms_args()
        args += [
            str(self.config.executable),
            "-project",
            str(self.config.project.path),
            "-seed",
            str(self.config.seed),
            "-readonly",
            "-nomesa",
            "-export_path",
            str(self.config.export_path),
            "-import_path",
            str(self.config.import_path),
            "-batch",
            str(self.config.workflow),
        ]

        if self.config.version:
            args += ["-v", str(self.config.version)]

        if self.config.threads:
            args += ["-threads", str(self.config.threads)]

        comp_process = subprocess.run(args=args, check=False)
        return comp_process.returncode

    def _find_job_failures(self, logfile_path: Path) -> str:
        """Reads through a .log file and returns logs regarding failing jobs.

        Any failure logs must be at the end of the log file as a failing job
        breaks the workflow and will be the last thing that is logged.

        The method first finds the logs for the last job that was started and returns
        the findings as a formatted string if any job failures are found.
        """
        current_job_line_no = 0

        # Find the line number where the logging of the last job starts
        with open(logfile_path) as logfile:
            for line_no, line in enumerate(logfile, start=1):
                if line.strip().startswith("<pre>"):
                    current_job_line_no = line_no
            last_job_line_no = current_job_line_no
            if last_job_line_no == 0:
                return ""

        # Collect the logs for the last job and look for failures
        with open(logfile_path) as logfile:
            job_failed_msg = ""
            for line_no, line in enumerate(logfile, start=1):
                if line_no >= last_job_line_no:
                    cleaned_text = re.sub(r"<[^>]*>", "", line)
                    if cleaned_text != "":
                        job_failed_msg += cleaned_text

        return job_failed_msg if "failed" in job_failed_msg else ""

    def print_failure(self, exit_status: int) -> None:
        run_path = self.config.run_path.resolve()
        # Reverse sort so workflow.log is (probably) first
        log_files = sorted(
            (
                log_file
                for log_file in glob.glob(f"{run_path}/*.log")
                if not re.fullmatch(
                    r"\d{8}-\d{6}-[A-Za-z0-9]{6}-RMS\.log",
                    os.path.basename(log_file),
                )
            ),
            reverse=True,
        )

        if exit_status == 137:
            # When the OOM-killer strikes, the RMS process (or maybe one of its
            # subprocesses) will get a SIGKILL (9) signal, which is often reported
            # as 128+9=137.
            fail_msg = dedent(
                f"""
        The RMS run failed with exit status: {exit_status}.

        This often means that the compute node ran out of memory and RMS or one of its
        subprocesses was terminated.
                """
            )

        elif not log_files:
            fail_msg = dedent(
                f"""
        The RMS run failed with exit status: {exit_status} and no log files were
        found in:

        * {run_path}

        This may mean that the compute node ran out of memory and RMS or one of its
        subprocesses was terminated, or that some other error has occurred.
                """
            )

        else:
            fail_msg = dedent(
                f"""
        The RMS run failed with exit status: {exit_status}. Typically this means a
        job in an RMS workflow has failed.
                """
            )

        if log_files:
            fail_msg += dedent(
                """
        For more details try checking these log files:

        * RMS.stderr.NN and RMS.stdout.NN
        * rms/model/workflow.log
        * Other named log files in rms/model, e.g. workflow_sim2seis.log

        The following log files were found in this realization's run path:

                """
            )
            fail_msg += "\n".join([f"* {f}" for f in log_files])

            for log_file in log_files:
                try:
                    job_failed_msg = self._find_job_failures(Path(log_file))
                except Exception:
                    job_failed_msg = ""
                if job_failed_msg:
                    fail_msg += dedent(
                        f"""
    \nThe following logs regarding failed jobs were found in the log file {log_file}:"
                        """
                    )
                    fail_msg += job_failed_msg
                    break

        print(fail_msg, file=sys.stderr)

    @property
    def exec_mode(self) -> RmsExecutionMode:
        """Executing in batch mode."""
        return RmsExecutionMode.batch

    def run(self) -> int:
        """Main executor entry point."""
        if not os.path.exists(self.config.run_path):
            os.makedirs(self.config.run_path)

        if (
            self.config._version_given != self.config.version
            and not self.config.allow_no_env
        ):
            raise RmsRuntimeError(
                "RMS environment not specified for version: "
                f"{self.config._version_given}"
            )

        with pushd(self.config.run_path):
            now = time.strftime("%d-%m-%Y %H:%M:%S", time.localtime(time.time()))
            with open("RMS_SEED_USED", "a+", encoding="utf-8") as filehandle:
                filehandle.write(f"{now} ... {self.config.seed}\n")

            if not os.path.exists(self.config.export_path):
                os.makedirs(self.config.export_path)

            if not os.path.exists(self.config.import_path):
                os.makedirs(self.config.import_path)

            exit_status = self._exec_rms()

        if exit_status != 0:
            self.print_failure(exit_status)
            return exit_status

        if self.config.target_file is None:
            return exit_status

        if not os.path.isfile(self.config.target_file):
            raise RmsRuntimeError(
                "The RMS run did not produce the expected file: "
                f"{self.config.target_file}"
            )

        if self.config.target_file_mtime is None:
            return exit_status

        if os.path.getmtime(self.config.target_file) == self.config.target_file_mtime:
            raise RmsRuntimeError(
                f"The target file: {self.config.target_file} is unmodified - "
                "interpreted as failure"
            )
        return exit_status
