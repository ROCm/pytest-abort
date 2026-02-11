# pytest-abort

`pytest-abort` is a pytest plugin + helper library to **attribute hard crashes (SIGSEGV/SIGABRT) to the last running test** and to keep pytest-html reports **mergeable and renderable** by sanitizing `data-jsonblob` payloads before `pytest_html_merger` runs.

## What this repo contains

- **Pytest plugin**: `pytest_abort.plugin`
  - Writes a JSON “last-running test” marker file before each test starts.
  - Deletes the marker file on normal test completion.
  - If pytest hard-crashes, cleanup never runs and the marker file remains with `status="running"`.

- **Crash marker parser**: `pytest_abort.crash_file`
  - `check_for_crash_file(path) -> dict | None`

- **Runner helpers (library module)**: `pytest_abort.abort_handling`
  - `handle_abort(json_file, html_file, last_running_file, testfile, crash_info=None) -> bool`
  - `append_abort_to_json(...)`, `append_abort_to_html(...)`
  - `sanitize_html_file_jsonblob(path)`, `sanitize_all_html_jsonblobs(log_dir)`

## Key concept: the last-running marker file

The plugin writes a small JSON file (path supplied by the runner) like:

```json
{
  "test_name": "test_foo",
  "nodeid": "tests/test_bar.py::TestBar::test_foo",
  "start_time": "2026-01-14T23:59:59.123456",
  "status": "running",
  "pid": 12345,
  "gpu_id": "0"
}
```

If pytest exits normally, the file is deleted. If pytest is killed by a segfault/abort, the file remains and the outer runner can attribute the crash.

## Installation

Editable install:

```bash
python3 -m pip install -e .
```

If you plan to use `-n` (parallel execution) and/or per-test timeouts, you likely also want:

```bash
python3 -m pip install -U pytest-xdist pytest-timeout
```

## Build a wheel

Build the wheel from the repo root:

```bash
python3 -m pip install --upgrade build
python3 -m build
```

The wheel is produced under:

- `./dist/` (for example: `./dist/pytest_abort-0.1.0-py3-none-any.whl`)

Install the wheel:

```bash
python3 -m pip install ./dist/*.whl
```

## Using the plugin directly (debugging)

Recommended (env var):

```bash
PYTEST_ABORT_LAST_RUNNING_FILE=/tmp/last_running.json \
python3 -m pytest -q tests/test_something.py
```

Note:
- If `pytest-abort` is **installed** (editable or wheel), pytest auto-loads it via the `pytest11` entry point, so you **do not** need `-p ...`.
- If the plugin is **installed** and you want to load it explicitly anyway, use `-p pytest_abort`.
- If the plugin is **not installed** and you’re loading it via `PYTHONPATH` only, use `-p pytest_abort.plugin`.
- Don’t use `-p ...` when it’s already auto-loaded, or pytest will error with “Plugin already registered”.

Optional CLI override:

```bash
python3 -m pytest \
  --last-running-file /tmp/last_running.json \
  -q tests/test_something.py
```

## Crashed-tests log (optional)

You can also write a shared **crashed tests log** (JSONL: one JSON object per line).

- Set `PYTEST_ABORT_CRASHED_TESTS_LOG=/path/to/crashed_tests.jsonl`
- For `pytest -n` (xdist), also set `PYTEST_ABORT_LAST_RUNNING_DIR=/path/to/dir` (so each worker writes its own marker file).

Example:

```bash
export PYTEST_ABORT_LAST_RUNNING_DIR=/tmp/last_running
export PYTEST_ABORT_CRASHED_TESTS_LOG=/tmp/crashed_tests.jsonl

pytest -n 8 tests
```

Notes:
- In xdist, the **master process** appends to the crashed-tests log when a worker goes down.
- In runner flows (like `run_single_gpu.py`), `handle_abort(...)` appends to the crashed-tests log if `PYTEST_ABORT_CRASHED_TESTS_LOG` is set.

## Postprocessing reports from the crash log

If your outer runner produces “final” reports (JSON/HTML/CSV) but pytest hard-crashed in the middle, you can patch those reports **after the fact** using the crashed-tests JSONL log.

This is useful when:
- some test sessions crashed before `pytest-json-report` / `pytest-html` / `pytest-csv` could fully write their output, or
- you want to attribute crashes from multiple runs into a single consolidated report artifact.

### CLI: `pytest-abort-postprocess`

When `pytest-abort` is installed, this repo exposes:
- `pytest-abort-postprocess` (entry point: `pytest_abort.postprocess:main`)

Usage (paths are optional; crash log is required):

```bash
export PYTEST_ABORT_CRASHED_TESTS_LOG=/path/to/crashed_tests.jsonl

pytest-abort-postprocess \
  --json-report /path/to/tests-report.json \
  --html-report /path/to/tests-report.html \
  --csv-report /path/to/tests-report.csv
```

Or explicitly:

```bash
pytest-abort-postprocess \
  --crash-log /path/to/crashed_tests.jsonl \
  --json-report /path/to/tests-report.json \
  --html-report /path/to/tests-report.html \
  --csv-report /path/to/tests-report.csv
```

What it does:
- Reads `crashed_tests.jsonl` and collects **unique** `nodeid`s (order-preserving, whitespace-trimmed).
- Appends a synthetic “crashed/failed” entry for each missing `nodeid` into:
  - the `pytest-json-report` JSON file (creates the file if missing)
  - the `pytest-html` report (creates a minimal standalone report if missing)
  - the `pytest-csv` report (creates the file + header if missing)
- Is intended to be **idempotent** (running it again should not duplicate entries).

Crash-log format:
- JSONL (one JSON object per line)
- Must contain at least `{"nodeid": "path/to/test_file.py::test_name"}` per crash
- Optional fields like `crash_time` / `duration` / `reason` / `gpu_id` are preserved when present

### Library API

The CLI is a thin wrapper around:
- `pytest_abort.abort_handling.postprocess_reports_from_crash_log(...)`

If you prefer:

```python
from pytest_abort.abort_handling import postprocess_reports_from_crash_log

postprocess_reports_from_crash_log(
    "/path/to/crashed_tests.jsonl",
    json_report_file="/path/to/tests-report.json",
    html_report_file="/path/to/tests-report.html",
    csv_report_file="/path/to/tests-report.csv",
)
```

## Crash recovery for xdist runs (optional helper)

If you want a “crash recovery” loop for `pytest -n ...`, you can use the included outer-process wrapper:

```bash
pytest-abort-retry --max-runs 5 --clear-crash-log -- \
  pytest -n 8 --max-worker-restart=50 --tb=short --maxfail=20 tests examples
```

Per-test timeout example (requires `pytest-timeout`):

```bash
pytest-abort-retry --max-runs 5 --clear-crash-log -- \
  pytest -n 8 --max-worker-restart=50 --tb=short --maxfail=20 \
    --timeout=600 --timeout-method=thread \
    tests examples
```

This will:
- run pytest
- read `PYTEST_ABORT_CRASHED_TESTS_LOG`
- re-run pytest with `--deselect=<nodeid>` for crashed nodeids until stable (or `--max-runs`).

Note:
- The wrapper supports the standard `... -- pytest ...` form.
- To avoid “wrong pytest binary / wrong environment” problems (missing `-n`, missing `--timeout`, etc.), the wrapper rewrites a leading `pytest ...` to run as `python -m pytest ...` using the wrapper’s interpreter.


## Integration notes (outer runner)

If you run pytest from an outer process (CI wrapper, custom runner, etc.), a common pattern is:

- Add this package to the environment (editable install or wheel)
- Set `PYTEST_ABORT_LAST_RUNNING_FILE` (or `PYTEST_ABORT_LAST_RUNNING_DIR` for xdist)
- Produce per-run JSON/HTML artifacts with `pytest-json-report` and `pytest-html` (best-effort)
- If a hard crash occurred, patch/repair the per-run artifacts from the outer process via `handle_abort(...)`
- Before merging many HTML reports, sanitize `data-jsonblob` payloads so merge/HTML rendering stays robust

## How rocm-jax uses it

The [`rocm-jax`](https://github.com/ROCm/rocm-jax) test runners use `pytest-abort` to attribute hard crashes to the last-running test and to keep pytest-html reports mergeable.

### `run_single_gpu.py`

- Ensures `pytest_abort` is importable by the pytest subprocess (either by installing `pytest-abort` into the environment, or by adding the repo checkout to `PYTHONPATH`)
- Enables the plugin via the installed `pytest11` entry point (no `-p` needed when installed). If using `PYTHONPATH` only, load explicitly with `-p pytest_abort.plugin`.
- Sets `PYTEST_ABORT_LAST_RUNNING_FILE` per test-file run (`logs/*_last_running.json`)
- On crash: re-runs remaining tests in the same file using `--deselect <crashed-nodeid>`
- Appends crash info into `*_log.json` + `*_log.html` using `pytest_abort.abort_handling.handle_abort(...)`
- Sanitizes per-file HTML jsonblobs before merging:
  - `sanitize_all_html_jsonblobs("./logs")` then `pytest_html_merger`

### `run_multi_gpu.py`

- Uses the same plugin and env-var mechanism for its pytest subprocesses
- Uses the same `./logs` directory and does **not** archive logs

## Using the helper library from a runner (example)

Hard crashes can prevent `pytest-html` / `pytest-json-report` from finishing their output files. The pattern used by the ROCm runners is:

- Run pytest with `--html=...` and `--json-report-file=...` (best-effort)
- Detect a hard crash via the last-running marker file
- Call `pytest_abort.abort_handling.handle_abort(...)` **from the runner process** to ensure the per-testfile `*_log.json` and `*_log.html` exist and contain a synthetic “crashed” test entry

Minimal example:

```python
import os
import subprocess

from pytest_abort.abort_handling import handle_abort, sanitize_all_html_jsonblobs

json_log = "logs/example_log.json"
html_log = "logs/example_log.html"
last_running = "logs/example_last_running.json"

env = os.environ.copy()
env["PYTEST_ABORT_LAST_RUNNING_FILE"] = os.path.abspath(last_running)

subprocess.run(
    [
        "python3",
        "-m",
        "pytest",
        # Plugin is auto-loaded via pytest11 entry point when installed.
        "--json-report",
        f"--json-report-file={json_log}",
        f"--html={html_log}",
        "tests/some_test_file.py",
    ],
    env=env,
    check=False,
)

# If a crash happened, this ensures JSON/HTML logs exist and are patched:
handle_abort(json_log, html_log, last_running, testfile="some_test_file")

# Before merging many per-file HTML reports:
sanitize_all_html_jsonblobs("logs")
```