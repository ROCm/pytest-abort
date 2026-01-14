# pytest-abort-plugin

`pytest-abort-plugin` is a pytest plugin + helper library to **attribute hard crashes (SIGSEGV/SIGABRT) to the last running test** and to keep pytest-html reports **mergeable and renderable** by sanitizing `data-jsonblob` payloads before `pytest_html_merger` runs.

## What this repo contains

- **Pytest plugin**: `pytest_abort_plugin.plugin`
  - Writes a JSON “last-running test” marker file before each test starts.
  - Deletes the marker file on normal test completion.
  - If pytest hard-crashes, cleanup never runs and the marker file remains with `status="running"`.

- **Crash marker parser**: `pytest_abort_plugin.crash_file`
  - `check_for_crash_file(path) -> dict | None`

- **Runner helpers (library module)**: `pytest_abort_plugin.abort_handling`
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

## Using the plugin directly (debugging)

Recommended (env var):

```bash
JAX_ROCM_LAST_RUNNING_FILE=/tmp/last_running.json \
python3 -m pytest -p pytest_abort_plugin.plugin -q tests/test_something.py
```

Optional CLI override:

```bash
python3 -m pytest -p pytest_abort_plugin.plugin \
  --rocm-last-running-file /tmp/last_running.json \
  -q tests/test_something.py
```

## How rocm-jax uses it

### `run_single_gpu.py`

- Adds the `pytest-abort-plugin` repo directory to `PYTHONPATH` for the pytest subprocess
- Enables the plugin with `-p pytest_abort_plugin.plugin`
- Sets `JAX_ROCM_LAST_RUNNING_FILE` per test-file run (`logs/*_last_running.json`)
- On crash: re-runs remaining tests in the same file using `--deselect <crashed-nodeid>`
- Appends crash info into `*_log.json` + `*_log.html` using `pytest_abort_plugin.abort_handling.handle_abort(...)`
- Sanitizes per-file HTML jsonblobs before merging:
  - `sanitize_all_html_jsonblobs("./logs")` then `pytest_html_merger`

### `run_multi_gpu.py`

- Uses the same plugin and env-var mechanism for its pytest subprocesses
- Uses the same `./logs` directory and does **not** archive logs