"""Post-process pytest reports with hard-crash records.

This is intended to be run by an outer process (CI wrapper / runner) after a
pytest session, to attribute hard crashes to specific tests in generated reports.

Inputs:
  - A crash log JSONL file (PYTEST_ABORT_CRASHED_TESTS_LOG) written by the plugin
    and/or outer runners. Each line is a JSON object with at least a "nodeid".

Outputs:
  - Append synthetic "crashed" failures into:
      - pytest-json-report JSON
      - pytest-html report
      - pytest-csv report
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from .abort_handling import postprocess_reports_from_crash_log


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Patch pytest JSON/HTML/CSV reports with crashed nodeids from a JSONL crash log."
    )
    parser.add_argument(
        "--crash-log",
        default=os.environ.get("PYTEST_ABORT_CRASHED_TESTS_LOG", ""),
        help="Path to crashed-tests JSONL (default: PYTEST_ABORT_CRASHED_TESTS_LOG).",
    )
    parser.add_argument(
        "--json-report",
        default="",
        help="Path to pytest-json-report output to patch (optional).",
    )
    parser.add_argument(
        "--html-report",
        default="",
        help="Path to pytest-html report to patch (optional).",
    )
    parser.add_argument(
        "--csv-report",
        default="",
        help="Path to pytest-csv report to patch (optional).",
    )
    args = parser.parse_args(argv)

    if not args.crash_log:
        print("ERROR: --crash-log not set and PYTEST_ABORT_CRASHED_TESTS_LOG is empty")
        return 2

    postprocess_reports_from_crash_log(
        args.crash_log,
        json_report_file=args.json_report or None,
        html_report_file=args.html_report or None,
        csv_report_file=args.csv_report or None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

