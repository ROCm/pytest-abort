from __future__ import annotations

import json
import os

from pytest_abort.abort_handling import append_abort_to_json


def test_append_abort_to_json_creates_report_with_generic_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    json_file = tmp_path / "out.json"

    abort_info = {
        "test_name": "tests/test_demo.py::test_foo",
        "nodeid": "tests/test_demo.py::test_foo",
        "reason": "Test crashed",
        "abort_time": "2026-01-01T00:00:00",
        "duration": 1.0,
        "gpu_id": "0",
        "pid": "123",
        "test_class": "test_foo",
    }

    append_abort_to_json(str(json_file), testfile="test_demo", abort_info=abort_info)
    data = json.loads(json_file.read_text(encoding="utf-8"))

    assert data["exitcode"] == 1
    assert data["root"] == os.getcwd()
    assert data["summary"]["failed"] == 1
    assert data["tests"][0]["nodeid"] == abort_info["nodeid"]

