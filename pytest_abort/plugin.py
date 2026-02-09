"""Pytest plugin: write a 'last running test' JSON file for crash attribution.

Callers can set:
  - PYTEST_ABORT_LAST_RUNNING_FILE: absolute path to write JSON into

If pytest is terminated by a hard crash (segfault/abort), the file remains
with status="running". An outer process can parse it to report the crashed test.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

from .abort_handling import ENV_CRASHED_TESTS_LOG, append_crash_to_jsonl
from .crash_file import check_for_crash_file


# Generic env vars
ENV_LAST_RUNNING_FILE = "PYTEST_ABORT_LAST_RUNNING_FILE"
ENV_LAST_RUNNING_DIR = "PYTEST_ABORT_LAST_RUNNING_DIR"

OPT_LAST_RUNNING_FILE = "abort_last_running_file"
OPT_LAST_RUNNING_DIR = "abort_last_running_dir"


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def _get_last_running_file(config: pytest.Config) -> Optional[str]:
    # xdist worker: master can inject a per-worker path to avoid collisions.
    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict):
        injected = workerinput.get(OPT_LAST_RUNNING_FILE)
        if injected:
            return injected

    # Prefer env var so callers don't need to modify pytest args.
    p = os.environ.get(ENV_LAST_RUNNING_FILE)
    if p:
        return p

    # Optional: derive per-worker file from a directory.
    d = os.environ.get(ENV_LAST_RUNNING_DIR)
    if d:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        return os.path.join(d, f"last_running_{worker}.json")

    # Optional CLI override (nice for debugging).
    opt = getattr(config.option, OPT_LAST_RUNNING_FILE, None)
    if opt:
        return opt

    opt_dir = getattr(config.option, OPT_LAST_RUNNING_DIR, None)
    if opt_dir:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        return os.path.join(opt_dir, f"last_running_{worker}.json")
    return None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("abort-detector")
    group.addoption(
        "--last-running-file",
        dest=OPT_LAST_RUNNING_FILE,
        default=None,
        help="Path to write last-running-test JSON for crash attribution.",
    )
    group.addoption(
        "--last-running-dir",
        dest=OPT_LAST_RUNNING_DIR,
        default=None,
        help="Directory to write per-worker last-running JSON files (xdist-safe).",
    )


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist master: assign each worker a unique last-running file path."""
    # Only runs in the xdist master process.
    log_dir = os.environ.get(ENV_LAST_RUNNING_DIR) or getattr(
        node.config.option, OPT_LAST_RUNNING_DIR, None
    )
    if not log_dir:
        return
    os.makedirs(log_dir, exist_ok=True)
    node.workerinput[OPT_LAST_RUNNING_FILE] = os.path.join(
        log_dir, f"last_running_{node.gateway.id}.json"
    )


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """xdist master: if a worker dies, append its last-running test into crash log."""
    if not error:
        return
    crash_log_file = os.environ.get(ENV_CRASHED_TESTS_LOG)
    if not crash_log_file:
        return
    last_running_file = None
    if isinstance(getattr(node, "workerinput", None), dict):
        last_running_file = node.workerinput.get(OPT_LAST_RUNNING_FILE)
    if not last_running_file:
        return
    crash_info = check_for_crash_file(last_running_file, min_duration=0.0)
    if crash_info:
        append_crash_to_jsonl(crash_log_file, crash_info, source=f"xdist:{node.gateway.id}")


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

