"""Crash marker file parsing shared by runners and the pytest plugin.

The pytest plugin writes a JSON file with status='running' for the currently
executing test. If pytest dies due to a hard crash (SIGSEGV/SIGABRT), the file
remains and can be parsed by the outer runner to attribute the crash.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CrashInfo:
    test_name: str
    test_class: str
    nodeid: str
    reason: str
    crash_time: str
    duration: float
    gpu_id: str
    pid: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "test_class": self.test_class,
            "nodeid": self.nodeid,
            "reason": self.reason,
            "crash_time": self.crash_time,
            "duration": self.duration,
            "gpu_id": self.gpu_id,
            "pid": self.pid,
        }


def _extract_test_class(nodeid: str) -> str:
    if "::" not in nodeid:
        return "UnknownClass"
    parts = nodeid.split("::")
    if len(parts) >= 3:
        return parts[1]
    if len(parts) == 2:
        return parts[1]
    return "UnknownClass"


def check_for_crash_file(last_running_file: str) -> Optional[Dict[str, Any]]:
    """Return crash info dict if crash detected, otherwise None.

    A crash is detected when the last_running_file exists and contains JSON with
    status='running'. This file should be deleted on normal test completion by
    the plugin.
    """
    if not os.path.exists(last_running_file):
        return None

    try:
        with open(last_running_file, "r", encoding="utf-8") as f:
            crash_data = json.load(f)

        if crash_data.get("status") != "running":
            return None

        start_time = datetime.fromisoformat(crash_data["start_time"])
        duration = (datetime.now() - start_time).total_seconds()

        # Avoid false positives from extremely short runtimes.
        if duration < 0.1:
            return None

        test_identifier = crash_data.get(
            "nodeid", crash_data.get("test_name", "unknown_test")
        )
        nodeid = crash_data.get("nodeid", test_identifier)

        info = CrashInfo(
            test_name=test_identifier,
            test_class=_extract_test_class(test_identifier),
            nodeid=nodeid,
            reason="Test crashed (segfault, abort, or fatal error)",
            crash_time=datetime.now().isoformat(),
            duration=duration,
            gpu_id=str(crash_data.get("gpu_id", "unknown")),
            pid=str(crash_data.get("pid", "unknown")),
        )
        return info.as_dict()

    except (json.JSONDecodeError, KeyError, ValueError):
        # Invalid marker file: treat as no-crash.
        try:
            os.remove(last_running_file)
        except OSError:
            pass
        return None
    except OSError:
        return None

