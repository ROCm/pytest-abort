"""Outer-process retry wrapper for pytest runs that can hard-crash.

This is intentionally not a pytest hook plugin: it is meant to be invoked from
shell/CI to re-run pytest with `--deselect` for nodeids that previously crashed.

It uses the JSONL crash log written by the plugin:
  - PYTEST_ABORT_CRASHED_TESTS_LOG
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set


def _read_crashed_nodeids_jsonl(path: Path) -> List[str]:
    if not path.exists():
        return []
    nodeids: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        nid = rec.get("nodeid")
        if isinstance(nid, str) and nid:
            nodeids.append(nid)
    return nodeids


def _unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _build_deselect_args(nodeids: Iterable[str]) -> List[str]:
    args: List[str] = []
    for nid in nodeids:
        args.append(f"--deselect={nid}")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="Retry pytest runs by deselecting nodeids recorded as crashed in a JSONL crash log."
    )
    parser.add_argument(
        "--crash-log",
        default=os.environ.get("PYTEST_ABORT_CRASHED_TESTS_LOG", ""),
        help="Path to crashed-tests JSONL (default: PYTEST_ABORT_CRASHED_TESTS_LOG).",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=5,
        help="Maximum number of pytest invocations (default: 5).",
    )
    parser.add_argument(
        "--clear-crash-log",
        action="store_true",
        help="Truncate crash log before the first run.",
    )
    parser.add_argument(
        "pytest_cmd",
        nargs=argparse.REMAINDER,
        help="Pytest command to run (include 'pytest' and all args).",
    )

    args = parser.parse_args(argv)
    if not args.pytest_cmd:
        print("ERROR: provide a pytest command to run, e.g. `pytest-abort-retry -- pytest -n 8 tests`")
        return 2

    # Users commonly pass: pytest-abort-retry ... -- pytest ...
    # argparse keeps the leading "--" in REMAINDER in some shells; strip it.
    if args.pytest_cmd and args.pytest_cmd[0] == "--":
        args.pytest_cmd = args.pytest_cmd[1:]

    crash_log = Path(args.crash_log) if args.crash_log else None
    if crash_log is None:
        print("ERROR: --crash-log not set and PYTEST_ABORT_CRASHED_TESTS_LOG is empty")
        return 2

    crash_log.parent.mkdir(parents=True, exist_ok=True)
    if args.clear_crash_log:
        crash_log.write_text("", encoding="utf-8")

    deselect_nodeids: List[str] = []
    start_count = len(_read_crashed_nodeids_jsonl(crash_log))

    for run_idx in range(1, args.max_runs + 1):
        cmd = list(args.pytest_cmd) + _build_deselect_args(deselect_nodeids)
        print(f"\n=== pytest-abort-retry: run {run_idx}/{args.max_runs} ===")
        print("Command:", " ".join(cmd))

        rc = subprocess.call(cmd)

        crashed = _unique_keep_order(_read_crashed_nodeids_jsonl(crash_log))
        if crashed:
            deselect_nodeids = crashed

        new_count = len(crashed) - start_count
        print(f"Crash log: total={len(crashed)} (new since start={max(new_count, 0)})")

        # If no crashes recorded at all, or no new crashes were added since previous iteration,
        # stop retrying. (We still return the pytest return code.)
        if run_idx == 1 and not crashed:
            return rc

        if run_idx > 1:
            prev = set(deselect_nodeids)
            curr = set(crashed)
            if curr == prev:
                return rc

    return rc


if __name__ == "__main__":
    raise SystemExit(main())

