from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from pytest_abort.abort_handling import append_crash_to_jsonl
from pytest_abort.crash_file import check_for_crash_file
from pytest_abort.retry import main as retry_main


def test_retry_integration_aborted_then_passing_test_succeeds(tmp_path, capfd, monkeypatch):
    """
    Integration-style test that actually runs a pytest subprocess:

    - First run: a test calls os.abort() (simulating SIGABRT). The process dies
      before later tests run.
    - Outer process reads the last-running marker file and appends the crashed
      nodeid into the JSONL crash log.
    - Retry wrapper reruns pytest with --deselect=<crashed-nodeid>, allowing the
      later passing test to run and pass.
    """

    repo_root = Path(__file__).resolve().parents[1]
    test_file = tmp_path / "test_mod.py"
    test_file.write_text(
        textwrap.dedent(
            """
            import os
            import time

            def test_crash():
                time.sleep(0.2)
                os.abort()

            def test_ok():
                assert True
            """
        ).lstrip(),
        encoding="utf-8",
    )

    last_running = tmp_path / "last_running.json"
    crash_log = tmp_path / "crashed_tests.jsonl"

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}:{env.get('PYTHONPATH', '')}"
    env["PYTEST_ABORT_LAST_RUNNING_FILE"] = str(last_running)
    # Ensure we control plugin loading for the subprocess. In dev/editable
    # environments the plugin may also be auto-loaded via the pytest11 entrypoint,
    # which would conflict with an explicit "-p pytest_abort.plugin".
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    # Run pytest in a subprocess; it should abort (non-zero rc).
    cmd1 = [sys.executable, "-m", "pytest", "-q", "-p", "pytest_abort.plugin", str(test_file)]
    p1 = subprocess.run(cmd1, env=env, capture_output=True, text=True, check=False)
    print("first-run-rc:", p1.returncode)
    print("first-run-stdout:\n", p1.stdout)
    print("first-run-stderr:\n", p1.stderr)
    assert p1.returncode != 0

    crash_info = check_for_crash_file(str(last_running), min_duration=0.0)
    assert crash_info is not None
    append_crash_to_jsonl(str(crash_log), crash_info, source="test")

    # Ensure the retry run also has the plugin marker env var available.
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    monkeypatch.setenv("PYTEST_ABORT_LAST_RUNNING_FILE", env["PYTEST_ABORT_LAST_RUNNING_FILE"])
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

    # Retry: this should deselect the crashed nodeid and succeed.
    rc = retry_main(
        [
            "--crash-log",
            str(crash_log),
            "--max-runs",
            "3",
            "--",
            "pytest",
            "-q",
            "-p",
            "pytest_abort.plugin",
            str(test_file),
        ]
    )
    out = capfd.readouterr().out
    print("retry-wrapper-output:\n", out)

    assert rc == 0
    assert "--deselect=" in out
    # And the passing test should have executed (pytest prints "1 passed" in -q mode).
    assert "1 passed" in out

