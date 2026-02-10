from __future__ import annotations

import json
import os
from typing import List

from pytest_abort.retry import main as retry_main


def test_retry_wrapper_deselects_crashed_then_succeeds(tmp_path, capsys, monkeypatch):
    """
    Simulate a run where one test "crashes" and prevents the rest of the file
    from completing. The retry wrapper should then re-run pytest with a
    --deselect for the crashed nodeid, allowing the remaining (passing) tests
    to run and succeed.
    """

    crash_log = tmp_path / "crashed_tests.jsonl"
    run_log = tmp_path / "retry_wrapper_run_log.jsonl"
    crashed_nodeid = "tests/test_mod.py::test_crash"
    passing_nodeid = "tests/test_mod.py::test_ok"

    calls: List[List[str]] = []

    def fake_subprocess_call(cmd):
        # cmd is a List[str]
        calls.append(list(cmd))

        if len(calls) == 1:
            # First run: simulate a hard-crash having been detected/logged by
            # writing the crashed nodeid to the crash log.
            crash_log.write_text(json.dumps({"nodeid": crashed_nodeid}) + "\n", encoding="utf-8")
            # And record what "executed" before the crash: only the crashed test.
            run_log.write_text(
                json.dumps({"run": 1, "cmd": cmd, "executed": [crashed_nodeid]}) + "\n",
                encoding="utf-8",
            )
            return 1

        # Second run: the wrapper should deselect the crashed nodeid only.
        assert f"--deselect={crashed_nodeid}" in cmd
        assert all(not a.startswith(f"--deselect={passing_nodeid}") for a in cmd)
        # Record that the remaining passing test is what runs now.
        with open(run_log, "a", encoding="utf-8") as f:
            f.write(
                json.dumps({"run": 2, "cmd": cmd, "executed": [passing_nodeid]}) + "\n"
            )
        return 0

    monkeypatch.setattr("pytest_abort.retry.subprocess.call", fake_subprocess_call)

    # Use a "pytest ..." prefix to exercise the wrapper's rewrite to:
    # "<sys.executable> -m pytest ..."
    rc = retry_main(
        [
            "--crash-log",
            str(crash_log),
            "--max-runs",
            "5",
            "--",
            "pytest",
            "-q",
            "tests/test_mod.py",
        ]
    )
    out = capsys.readouterr().out
    if os.environ.get("PYTEST_ABORT_SHOW_RETRY_SUMMARY") == "1":
        # capsys captures even under `-s`; explicitly emit for debugging.
        with capsys.disabled():
            print(out, end="")

    assert rc == 0
    assert len(calls) == 2
    assert "pytest-abort-retry summary" in out
    assert crashed_nodeid in out
    # Confirm (via our run log) that after the crash, the "post-crash" test ran and passed.
    records = [
        json.loads(line)
        for line in run_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["run"] for r in records] == [1, 2]
    assert records[0]["executed"] == [crashed_nodeid]
    assert records[1]["executed"] == [passing_nodeid]

