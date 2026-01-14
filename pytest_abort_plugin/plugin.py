"""Pytest plugin: write a 'last running test' JSON file for crash attribution.

The outer runners (`run_single_gpu.py` / `run_multi_gpu.py`) set:
  - JAX_ROCM_LAST_RUNNING_FILE: absolute path to write JSON into

If pytest is terminated by a hard crash (segfault/abort), the file remains
with status="running". The runners parse it to report the crashed test.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import pytest


ENV_LAST_RUNNING_FILE = "JAX_ROCM_LAST_RUNNING_FILE"


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def _get_last_running_file(config: pytest.Config) -> Optional[str]:
    # Prefer env var so runners don't need to modify pytest args.
    p = os.environ.get(ENV_LAST_RUNNING_FILE)
    if p:
        return p

    # Optional CLI override (nice for debugging).
    opt = getattr(config.option, "rocm_last_running_file", None)
    if opt:
        return opt
    return None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("rocm-abort-detector")
    group.addoption(
        "--rocm-last-running-file",
        dest="rocm_last_running_file",
        default=None,
        help="Path to write last-running-test JSON for crash attribution.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem):  # pylint: disable=unused-argument
    """Track currently running test.

    We write before running the test. On normal completion (pass/fail/skip),
    we delete the file. On hard crash, pytest never reaches cleanup.
    """
    last_running_file = _get_last_running_file(item.config)
    if not last_running_file:
        # Plugin is inert unless runner provides a path.
        outcome = yield
        return outcome

    payload: Dict[str, Any] = {
        "test_name": item.name,
        "nodeid": item.nodeid,
        "start_time": datetime.now().isoformat(),
        "status": "running",
        "pid": os.getpid(),
        "gpu_id": os.environ.get("HIP_VISIBLE_DEVICES", "unknown"),
    }
    try:
        _atomic_write_json(last_running_file, payload)
    except OSError:
        # Don't fail the test run if we can't write.
        pass

    try:
        outcome = yield
        return outcome
    finally:
        # If pytest is still alive here, it wasn't a hard crash: remove marker.
        try:
            if os.path.exists(last_running_file):
                os.remove(last_running_file)
        except OSError:
            pass

