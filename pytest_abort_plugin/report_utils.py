"""Report utilities for test runners.

These helpers are designed to be called from an outer runner process.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
from typing import Dict, Optional, Tuple

from .abort_handling import sanitize_all_html_jsonblobs


def combine_json_reports(log_dir: str, *, out_file: Optional[str] = None) -> str:
    """Combine all *_log.json files in log_dir into one compiled report."""
    log_dir = os.path.abspath(log_dir)
    if out_file is None:
        out_file = os.path.join(log_dir, "final_compiled_report.json")

    all_json_files = [f for f in os.listdir(log_dir) if f.endswith("_log.json")]
    combined_data = []
    for json_file in all_json_files:
        with open(os.path.join(log_dir, json_file), "r", encoding="utf-8") as infile:
            combined_data.append(json.load(infile))

    with open(out_file, "w", encoding="utf-8") as outfile:
        json.dump(combined_data, outfile, indent=4)
    return out_file


def convert_compiled_json_to_csv(json_file: str, csv_file: str) -> int:
    """Convert a compiled JSON test report (list of reports) to CSV format."""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        test_results = []
        for report in data:
            if "tests" in report:
                for item in report["tests"]:
                    test_results.append(
                        {
                            "name": item.get("nodeid", ""),
                            "outcome": item.get("outcome", ""),
                            "duration": (
                                item.get("call", {}).get("duration", 0)
                                if "call" in item
                                else 0
                            ),
                            "keywords": ";".join(item.get("keywords", [])),
                        }
                    )

        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["name", "outcome", "duration", "keywords"]
            )
            writer.writeheader()
            writer.writerows(test_results)

        return len(test_results)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return -1


def merge_html_reports(
    log_dir: str,
    *,
    out_file: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = False,
    timeout: int = 300,
) -> Tuple[bool, str]:
    """Run pytest_html_merger on log_dir."""
    log_dir = os.path.abspath(log_dir)
    if out_file is None:
        out_file = os.path.join(log_dir, "final_compiled_report.html")

    cmd = ["pytest_html_merger", "-i", log_dir, "-o", out_file]
    result = subprocess.run(
        cmd, shell=shell, capture_output=True, env=env, check=False, timeout=timeout
    )
    if result.returncode != 0:
        return False, (result.stderr.decode() if hasattr(result.stderr, "decode") else str(result.stderr))
    return True, ""


def generate_final_report(log_dir: str, *, shell: bool = False, env_vars: Optional[Dict[str, str]] = None) -> None:
    """Generate final HTML+JSON+CSV reports for a log directory."""
    if env_vars is None:
        env_vars = {}
    env = os.environ.copy()
    env.update(env_vars)

    modified, total, failed = sanitize_all_html_jsonblobs(log_dir)
    if total:
        print(f"Sanitized HTML jsonblobs: modified={modified}/{total}, failed={failed}")

    ok, err = merge_html_reports(log_dir, env=env, shell=shell)
    if not ok:
        print("HTML merger failed, but continuing with JSON report generation...")
        if err:
            print(err)

    combined_json_file = combine_json_reports(log_dir)
    combined_csv_file = os.path.join(os.path.abspath(log_dir), "final_compiled_report.csv")
    convert_compiled_json_to_csv(combined_json_file, combined_csv_file)

