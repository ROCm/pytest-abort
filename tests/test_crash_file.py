from __future__ import annotations

import json
from datetime import datetime, timedelta

from pytest_abort.crash_file import check_for_crash_file


def test_check_for_crash_file_detects_running(tmp_path):
    p = tmp_path / "last_running.json"
    payload = {
        "test_name": "test_foo",
        "nodeid": "tests/test_demo.py::test_foo",
        "start_time": (datetime.now() - timedelta(seconds=5)).isoformat(),
        "status": "running",
        "pid": 123,
        "gpu_id": "0",
    }
    p.write_text(json.dumps(payload), encoding="utf-8")

    info = check_for_crash_file(str(p), min_duration=0.0)
    assert info is not None
    assert info["nodeid"] == payload["nodeid"]
    assert info["test_name"] == payload["nodeid"]
    assert info["pid"] == "123"


def test_check_for_crash_file_ignores_non_running(tmp_path):
    p = tmp_path / "last_running.json"
    payload = {
        "test_name": "test_foo",
        "start_time": (datetime.now() - timedelta(seconds=5)).isoformat(),
        "status": "done",
    }
    p.write_text(json.dumps(payload), encoding="utf-8")

    assert check_for_crash_file(str(p), min_duration=0.0) is None

