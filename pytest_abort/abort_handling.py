"""Shared abort handling + report patching used by ROCm test runners.

This is *not* a pytest hook plugin module. It's a library module that runners
import to:
  - parse crash marker files
  - append abort info into pytest-json-report + pytest-html artifacts
  - sanitize per-file html jsonblobs before running pytest_html_merger
"""

from __future__ import annotations

import glob
import html
import json
import os
import re
import traceback
import unicodedata
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .crash_file import check_for_crash_file

# Generic env vars
ENV_CRASHED_TESTS_LOG = "PYTEST_ABORT_CRASHED_TESTS_LOG"

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


def sanitize_for_json(text: Optional[str]) -> Optional[str]:
    """Remove control characters that break JSON parsing, preserving \\n/\\r/\\t."""
    if not text:
        return text
    return "".join(
        ch if unicodedata.category(ch)[0] != "C" or ch in "\n\r\t" else " "
        for ch in text
    )


def _sanitize_str_for_html_jsonblob(text: str) -> str:
    """Sanitize strings for pytest-html jsonblob so merger/UI won't break."""
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "<br/>")
    text = text.replace("\t", "  ")
    return "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)


def _sanitize_obj_for_html_jsonblob(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_obj_for_html_jsonblob(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_obj_for_html_jsonblob(v) for v in obj]
    if isinstance(obj, str):
        return _sanitize_str_for_html_jsonblob(obj)
    return obj


def _escape_control_chars_in_json_strings(json_text: str) -> str:
    """Escape raw control chars that appear *inside* JSON string literals.

    This repairs malformed JSON where a string contains literal newlines/tabs/etc
    (e.g. JSONDecodeError: Invalid control character). We only escape when we're
    inside a JSON string (between quotes), so whitespace between tokens is left
    untouched.
    """
    out = []
    in_string = False
    escaped = False

    for ch in json_text:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue

        # Inside a JSON string literal.
        if escaped:
            out.append(ch)
            escaped = False
            continue

        if ch == "\\":
            out.append(ch)
            escaped = True
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            continue

        code = ord(ch)
        if code < 0x20:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{code:04x}")
        else:
            out.append(ch)

    return "".join(out)


def sanitize_html_file_jsonblob(html_path: str) -> bool:
    """Parse + sanitize a single pytest-html report's data-jsonblob in place.

    Returns True if modified.
    """
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except OSError:
        return False

    # Fast skip for files without a jsonblob.
    if 'data-jsonblob="' not in html_content and "data-jsonblob='" not in html_content:
        return False

    # Most pytest-html reports use a double-quoted attribute.
    m = re.search(r'data-jsonblob="([^"]*)"', html_content, flags=re.DOTALL)
    attr_quote = '"'
    if not m:
        # Be tolerant: handle single-quoted variants.
        m = re.search(r"data-jsonblob='([^']*)'", html_content, flags=re.DOTALL)
        attr_quote = "'"
    if not m:
        return False

    raw_attr = m.group(1)
    json_text = html.unescape(raw_attr)

    # If this jsonblob doesn't contain anything we might transform, avoid the
    # expensive json.loads + deep-walk. (We still repair malformed blobs below.)
    maybe_needs_sanitize = (
        "\n" in json_text
        or "\r" in json_text
        or "\t" in json_text
        or "\\n" in json_text
        or "\\r" in json_text
        or "\\t" in json_text
        or "\\u000" in json_text
        or "\\u001" in json_text
    )

    try:
        data = json.loads(json_text) if maybe_needs_sanitize else None
    except (json.JSONDecodeError, ValueError) as exc:
        # Repair common corruption: literal control chars inside JSON strings.
        fixed_text = _escape_control_chars_in_json_strings(json_text)
        try:
            data = json.loads(fixed_text)
        except (json.JSONDecodeError, ValueError) as exc2:
            raise ValueError(
                f"Could not parse data-jsonblob in {html_path}: {exc2}"
            ) from exc2

    if data is None:
        # No likely transformations needed and blob looked syntactically fine.
        return False

    sanitized = _sanitize_obj_for_html_jsonblob(data)
    dumped = json.dumps(sanitized, ensure_ascii=False)
    new_attr = html.escape(dumped, quote=True)
    if new_attr == raw_attr:
        return False

    # Preserve the original quote type used in the attribute.
    if attr_quote == "'":
        # html.escape(..., quote=True) escapes both " and ', so safe.
        pass

    new_html = html_content[: m.start(1)] + new_attr + html_content[m.end(1) :]
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(new_html)
    except OSError:
        return False
    return True


def sanitize_all_html_jsonblobs(log_dir: str) -> Tuple[int, int, int]:
    """Sanitize all *_log.html files in log_dir.

    Returns (modified, total, failed).
    """
    html_files = glob.glob(os.path.join(log_dir, "*_log.html"))
    modified = 0
    failed = 0
    for p in sorted(html_files):
        try:
            if sanitize_html_file_jsonblob(p):
                modified += 1
        except Exception:  # pylint: disable=broad-exception-caught
            failed += 1
    return modified, len(html_files), failed


def append_crash_to_jsonl(crash_log_file: str, crash_info: Dict[str, Any], *, source: str) -> None:
    """Append a crash record to a JSONL file (one JSON object per line)."""
    payload = dict(crash_info)
    payload["source"] = source
    payload["logged_at"] = datetime.now().isoformat()

    os.makedirs(os.path.dirname(crash_log_file) or ".", exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with open(crash_log_file, "a", encoding="utf-8") as f:
        # Best-effort cross-process safety (Linux): lock the file while appending.
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        try:
            f.write(line)
            f.flush()
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def append_abort_to_json(json_file: str, testfile: str, abort_info: Dict[str, Any]) -> None:
    """Append abort info to pytest-json-report formatted JSON."""
    test_identifier = abort_info["test_name"]
    test_class = abort_info.get("test_class", "UnknownClass")

    if "::" in test_identifier and test_identifier.startswith("tests/"):
        test_nodeid = test_identifier
    elif "::" in test_identifier:
        test_nodeid = f"tests/{testfile}.py::{test_identifier}"
    else:
        test_nodeid = f"tests/{testfile}.py::{test_identifier}"

    abort_reason_clean = abort_info.get("reason", "Unknown abort reason") or ""
    abort_reason_clean = "".join(
        ch if unicodedata.category(ch)[0] != "C" or ch == "\n" else " "
        for ch in abort_reason_clean
    )

    abort_longrepr = (
        f"Test aborted: {abort_reason_clean}\n"
        f"Test Class: {test_class}\n"
        f"Abort detected at: {abort_info.get('abort_time', '')}\n"
        f"GPU ID: {abort_info.get('gpu_id', 'unknown')}"
    )

    abort_test = {
        "nodeid": test_nodeid,
        "lineno": 1,
        "outcome": "failed",
        "keywords": [abort_info["test_name"], testfile, "abort", test_class, ""],
        "setup": {"duration": 0.0, "outcome": "passed"},
        "call": {
            "duration": abort_info.get("duration", 0),
            "outcome": "failed",
            "longrepr": abort_longrepr,
        },
        "teardown": {"duration": 0.0, "outcome": "skipped"},
    }

    if os.path.exists(json_file):
        with open(json_file, "r", encoding="utf-8") as f:
            report_data = json.load(f)
        report_data.setdefault("tests", []).append(abort_test)
        summary = report_data.get("summary", {})
        summary["failed"] = summary.get("failed", 0) + 1
        summary["total"] = summary.get("total", 0) + 1
        summary["collected"] = summary.get("collected", 0) + 1
        if "unskipped_total" in summary:
            summary["unskipped_total"] = summary["unskipped_total"] + 1
        report_data["summary"] = summary
        report_data["exitcode"] = 1
    else:
        current_time = datetime.now().timestamp()
        report_data = {
            "created": current_time,
            "duration": abort_info.get("duration", 0),
            "exitcode": 1,
            "root": "/rocm-jax/jax",
            "environment": {},
            "summary": {
                "passed": 0,
                "failed": 1,
                "total": 1,
                "collected": 1,
                "unskipped_total": 1,
            },
            "tests": [abort_test],
        }

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4)


def append_abort_to_html(html_file: str, testfile: str, abort_info: Dict[str, Any]) -> None:
    """Append abort info to pytest-html report (best-effort)."""
    if os.path.exists(html_file):
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                html_content = f.read()
        except OSError:
            html_content = ""

        abort_row = _create_abort_row_html(testfile, abort_info)
        if "</table>" in html_content:
            results_table_start = html_content.find('<table id="results-table">')
            results_table_end = html_content.find("</table>", results_table_start)
            if results_table_end != -1:
                html_content = (
                    html_content[:results_table_end]
                    + f"{abort_row}\n    "
                    + html_content[results_table_end:]
                )
                html_content = _update_html_summary_counts(html_content)
                html_content = _update_html_json_data(html_content, testfile, abort_info)
                html_content = re.sub(
                    r'class="summary__reload__button\s*"',
                    'class="summary__reload__button hidden"',
                    html_content,
                )
                try:
                    with open(html_file, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    return
                except OSError:
                    pass

    # Fallback: create a standalone HTML report that pytest_html_merger can read.
    _create_new_html_file(html_file, testfile, abort_info)


def _create_abort_row_html(testfile: str, abort_info: Dict[str, Any]) -> str:
    """Create an HTML row (tbody) for an abort/crash entry."""
    test_identifier = abort_info["test_name"]
    test_class = abort_info.get("test_class", "UnknownClass")

    if "::" in test_identifier and test_identifier.startswith("tests/"):
        display_name = test_identifier
    elif "::" in test_identifier:
        display_name = f"tests/{testfile}.py::{test_identifier}"
    else:
        display_name = f"tests/{testfile}.py::{test_identifier}"

    duration = float(abort_info.get("duration", 0) or 0)
    abort_time = abort_info.get("abort_time", "")
    gpu_id = abort_info.get("gpu_id", "unknown")

    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    seconds = int(duration % 60)
    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    abort_reason = sanitize_for_json(abort_info.get("reason", "Test aborted or crashed."))
    test_class_clean = sanitize_for_json(str(test_class))
    abort_time_clean = sanitize_for_json(str(abort_time))
    gpu_id_clean = sanitize_for_json(str(gpu_id))

    log_content = (
        f"Test aborted: {abort_reason}\n"
        f"Test Class: {test_class_clean}\n"
        f"Abort detected at: {abort_time_clean}\n"
        f"GPU ID: {gpu_id_clean}"
    )

    return f"""
                <tbody class="results-table-row">
                    <tr class="collapsible">
                        <td class="col-result">Failed</td>
                        <td class="col-name">{display_name}</td>
                        <td class="col-duration">{duration_str}</td>
                        <td class="col-links"></td>
                    </tr>
                    <tr class="extras-row">
                        <td class="extra" colspan="4">
                            <div class="extraHTML"></div>
                            <div class="logwrapper">
                                <div class="logexpander"></div>
                                <div class="log">{log_content}</div>
                            </div>
                        </td>
                    </tr>
                </tbody>"""


def _update_html_summary_counts(html_content: str) -> str:
    """Update pytest-html summary counts for an appended failed test."""
    malformed_pattern = r"(\d+/\d+ test done\.)"
    if re.search(malformed_pattern, html_content):
        html_content = re.sub(malformed_pattern, "1 tests took 00:00:01.", html_content)

    count_pattern = r"(\d+) tests? ran in"
    match = re.search(count_pattern, html_content)
    if match:
        current_count = int(match.group(1))
        html_content = re.sub(count_pattern, f"{current_count + 1} tests ran in", html_content)

    count_pattern2 = r"(\d+) tests? took"
    match = re.search(count_pattern2, html_content)
    if match:
        current_count = int(match.group(1))
        html_content = re.sub(count_pattern2, f"{current_count + 1} tests took", html_content)

    failed_pattern = r"(\d+) Failed"
    match = re.search(failed_pattern, html_content)
    if match:
        current_failed = int(match.group(1))
        html_content = re.sub(failed_pattern, f"{current_failed + 1} Failed", html_content)
    else:
        html_content = html_content.replace("0 Failed,", "1 Failed,")
        html_content = html_content.replace(
            'data-test-result="failed" disabled',
            'data-test-result="failed"',
        )
    return html_content


def _update_html_json_data(html_content: str, testfile: str, abort_info: Dict[str, Any]) -> str:
    """Update pytest-html data-jsonblob by adding the aborted test entry."""
    jsonblob_pattern = r'data-jsonblob="([^"]*)"'
    match = re.search(jsonblob_pattern, html_content)
    if not match:
        return html_content

    try:
        raw_attr = match.group(1)
        json_str = html.unescape(raw_attr)
        try:
            existing_json = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            # Repair malformed jsonblobs produced by crashes/merges.
            existing_json = json.loads(_escape_control_chars_in_json_strings(json_str))

        if "tests" not in existing_json or not isinstance(existing_json.get("tests"), dict):
            existing_json["tests"] = {}

        test_name = abort_info["test_name"]
        duration = float(abort_info.get("duration", 0) or 0)
        abort_time = abort_info.get("abort_time", "")
        gpu_id = abort_info.get("gpu_id", "unknown")

        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        test_id = f"test_{len(existing_json['tests'])}"

        abort_reason = sanitize_for_json(abort_info.get("reason", "Test aborted or crashed."))
        abort_time_clean = sanitize_for_json(str(abort_time))
        gpu_id_clean = sanitize_for_json(str(gpu_id))

        log_msg = (
            f"Test aborted: {abort_reason}\n"
            f"Abort detected at: {abort_time_clean}\n"
            f"GPU ID: {gpu_id_clean}"
        )

        testid_display = (
            test_name
            if ("::" in test_name and test_name.startswith("tests/"))
            else f"tests/{testfile}.py::{test_name}"
        )

        new_test = {
            "testId": testid_display,
            "id": test_id,
            "log": log_msg,
            "extras": [],
            "resultsTableRow": [
                '<td class="col-result">Failed</td>',
                f'<td class="col-name">{testid_display}</td>',
                f'<td class="col-duration">{duration_str}</td>',
                '<td class="col-links"></td>',
            ],
            "tableHtml": [],
            "result": "failed",
            "collapsed": False,
        }
        existing_json["tests"][test_id] = new_test

        updated_json_str = html.escape(
            json.dumps(existing_json, ensure_ascii=False), quote=True
        )
        # Avoid regex replacement pitfalls with backslashes in the json blob:
        # do a single targeted replacement of the attribute content.
        html_content = (
            html_content[: match.start(1)]
            + updated_json_str
            + html_content[match.end(1) :]
        )
    except (json.JSONDecodeError, ValueError, TypeError) as ex:
        print(f"Warning: Could not update JSON data in HTML file: {ex}")

    return html_content


def _generate_html_template(template_data: Dict[str, str]) -> str:
    """Generate a minimal pytest-html compatible template for abort-only reports."""
    testfile = template_data["testfile"]
    test_name = template_data["test_name"]
    duration_str = template_data["duration_str"]
    current_time_str = template_data["current_time_str"]
    log_content = template_data["log_content"]
    json_blob = template_data["json_blob"]

    return f"""<!DOCTYPE html>
        <html>
          <head>
            <meta charset="utf-8"/>
            <title id="head-title">{testfile}_log.html</title>
            <link href="assets/style.css" rel="stylesheet" type="text/css"/>
          </head>
          <body onLoad="init()">
            <h1 id="title">{testfile}_log.html</h1>
            <p>Report generated on {current_time_str} by
               <a href="https://pypi.python.org/pypi/pytest-html">pytest-html</a> v4.1.1</p>
            <div id="environment-header">
              <h2>Environment</h2>
            </div>
            <table id="environment"></table>
            <div class="summary">
              <div class="summary__data">
                <h2>Summary</h2>
                <div class="additional-summary prefix">
                </div>
                <p class="run-count">1 tests took {duration_str}.</p>
                <p class="filter">(Un)check the boxes to filter the results.</p>
                <div class="summary__reload">
                  <div class="summary__reload__button hidden" onclick="location.reload()">
                    <div>There are still tests running. <br />Reload this page to get the latest results!</div>
                  </div>
                </div>
                <div class="summary__spacer"></div>
                <div class="controls">
                  <div class="filters">
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="failed" />
                    <span class="failed">1 Failed,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="passed" disabled/>
                    <span class="passed">0 Passed,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="skipped" disabled/>
                    <span class="skipped">0 Skipped,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="xfailed" disabled/>
                    <span class="xfailed">0 Expected failures,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="xpassed" disabled/>
                    <span class="xpassed">0 Unexpected passes,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="error" disabled/>
                    <span class="error">0 Errors,</span>
                    <input checked="true" class="filter" name="filter_checkbox" type="checkbox" data-test-result="rerun" disabled/>
                    <span class="rerun">0 Reruns</span>
                  </div>
                  <div class="collapse">
                    <button id="show_all_details">Show all details</button>&nbsp;/&nbsp;<button id="hide_all_details">Hide all details</button>
                  </div>
                </div>
              </div>
              <div class="additional-summary summary">
              </div>
              <div class="additional-summary postfix">
              </div>
            </div>
            <table id="results-table">
              <thead id="results-table-head">
                <tr>
                  <th class="sortable result initial-sort" data-column-type="result">Result</th>
                  <th class="sortable" data-column-type="name">Test</th>
                  <th class="sortable" data-column-type="duration">Duration</th>
                  <th class="sortable links" data-column-type="links">Links</th>
                </tr>
              </thead>
              <tbody class="results-table-row">
                <tr class="collapsible">
                  <td class="col-result">Failed</td>
                  <td class="col-name">{test_name}</td>
                  <td class="col-duration">{duration_str}</td>
                  <td class="col-links"></td>
                </tr>
                <tr class="extras-row">
                  <td class="extra" colspan="4">
                    <div class="extraHTML"></div>
                    <div class="logwrapper">
                      <div class="logexpander"></div>
                      <div class="log">{log_content}</div>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <div id="data-container" data-jsonblob="{json_blob}"></div>
            <script>
              function init() {{
                // Minimal init; pytest_html_merger mainly consumes data-jsonblob.
              }}
            </script>
          </body>
        </html>"""


def _create_new_html_file(html_file: str, testfile: str, abort_info: Dict[str, Any]) -> None:
    """Create a standalone HTML file for an abort-only report."""
    try:
        test_name = abort_info["test_name"]
        duration = float(abort_info.get("duration", 0) or 0)
        abort_time = abort_info.get("abort_time", "")
        gpu_id = abort_info.get("gpu_id", "unknown")

        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        abort_reason = sanitize_for_json(abort_info.get("reason", "Test aborted or crashed."))
        abort_time_clean = sanitize_for_json(str(abort_time))
        gpu_id_clean = sanitize_for_json(str(gpu_id))

        log_msg = (
            f"Test aborted: {abort_reason}\n"
            f"Abort detected at: {abort_time_clean}\n"
            f"GPU ID: {gpu_id_clean}"
        )

        json_data = {
            "environment": {},
            "tests": {
                "test_0": {
                    "testId": (
                        test_name
                        if ("::" in test_name and test_name.startswith("tests/"))
                        else f"tests/{testfile}.py::{test_name}"
                    ),
                    "id": "test_0",
                    "log": log_msg,
                    "extras": [],
                    "resultsTableRow": [
                        '<td class="col-result">Failed</td>',
                        f'<td class="col-name">{test_name}</td>',
                        f'<td class="col-duration">{duration_str}</td>',
                        '<td class="col-links"></td>',
                    ],
                    "tableHtml": [],
                    "result": "failed",
                    "collapsed": False,
                }
            },
            "renderCollapsed": ["passed"],
            "initialSort": "result",
            "title": f"{testfile}_log.html",
        }

        json_blob = html.escape(json.dumps(json_data, ensure_ascii=False), quote=True)
        current_time_str = datetime.now().strftime("%d-%b-%Y at %H:%M:%S")

        html_content = _generate_html_template(
            {
                "testfile": testfile,
                "test_name": test_name,
                "duration_str": duration_str,
                "current_time_str": current_time_str,
                "log_content": log_msg,
                "json_blob": json_blob,
            }
        )

        os.makedirs(os.path.dirname(html_file) or ".", exist_ok=True)
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()


def handle_abort(
    json_file: str,
    html_file: str,
    last_running_file: str,
    testfile: str,
    crash_info: Optional[Dict[str, Any]] = None,
) -> bool:
    """Detect crash and append info into JSON+HTML reports."""
    if crash_info is None:
        crash_info = check_for_crash_file(last_running_file)

    if os.path.exists(last_running_file):
        try:
            os.remove(last_running_file)
        except OSError:
            pass

    if not crash_info:
        return False

    try:
        crash_log_file = os.environ.get(ENV_CRASHED_TESTS_LOG)
        if crash_log_file:
            append_crash_to_jsonl(crash_log_file, crash_info, source="runner")
        append_abort_to_json(json_file, testfile, crash_info)
        append_abort_to_html(html_file, testfile, crash_info)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        return False
