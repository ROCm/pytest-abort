from __future__ import annotations

import json

from pytest_abort.abort_handling import postprocess_reports_from_crash_log


def test_postprocess_adds_unique_crashes_and_is_idempotent(tmp_path):
    crash_log = tmp_path / "crashed_tests.jsonl"
    json_report = tmp_path / "tests-report.json"
    html_report = tmp_path / "tests-report.html"
    csv_report = tmp_path / "tests-report.csv"

    nid1 = "tests/test_mod.py::test_crash"
    nid2 = "tests/test_other.py::TestCls::test_crash2"

    crash_log.write_text(
        "\n".join(
            [
                json.dumps({"nodeid": nid1, "crash_time": "2026-01-01T00:00:00", "duration": 1.0}),
                json.dumps({"nodeid": f" {nid1} ", "crash_time": "2026-01-01T00:00:01", "duration": 2.0}),
                json.dumps({"nodeid": nid2, "crash_time": "2026-01-01T00:00:02", "duration": 3.0}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    # First run: should create/patch all reports with 2 unique crashes.
    postprocess_reports_from_crash_log(
        str(crash_log),
        json_report_file=str(json_report),
        html_report_file=str(html_report),
        csv_report_file=str(csv_report),
    )

    data1 = json.loads(json_report.read_text(encoding="utf-8"))
    assert len(data1["tests"]) == 2
    assert {t["nodeid"] for t in data1["tests"]} == {nid1, nid2}

    csv_text1 = csv_report.read_text(encoding="utf-8")
    assert nid1 in csv_text1
    assert nid2 in csv_text1

    html_text1 = html_report.read_text(encoding="utf-8")
    assert nid1 in html_text1
    assert nid2 in html_text1

    # Second run: should not duplicate.
    postprocess_reports_from_crash_log(
        str(crash_log),
        json_report_file=str(json_report),
        html_report_file=str(html_report),
        csv_report_file=str(csv_report),
    )

    data2 = json.loads(json_report.read_text(encoding="utf-8"))
    assert len(data2["tests"]) == 2

    csv_text2 = csv_report.read_text(encoding="utf-8")
    assert csv_text2.count(nid1) == 1
    assert csv_text2.count(nid2) == 1

    html_text2 = html_report.read_text(encoding="utf-8")
    # A nodeid may appear multiple times within a single pytest-html report
    # (e.g., visible row + embedded data-jsonblob). Idempotence means running
    # postprocess again does not change the HTML file.
    assert html_text2 == html_text1

