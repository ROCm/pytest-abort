"""Filesystem helpers for runner-managed log directories.

These functions are intended to be called by an outer test runner process
(e.g. run_single_gpu.py / run_multi_gpu.py), not from inside a pytest worker.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Optional


def archive_logs_dir_if_nonempty(
    log_dir: str, *, timestamp: Optional[str] = None, timestamp_fmt: str = "%Y-%m-%d_%H-%M-%S"
) -> Optional[str]:
    """If log_dir exists and is non-empty, move it to log_dir_<timestamp>.

    Returns the archive path if an archive happened, otherwise None.
    """
    log_dir = os.path.abspath(log_dir)
    if not (os.path.exists(log_dir) and os.path.isdir(log_dir)):
        return None

    try:
        entries = os.listdir(log_dir)
    except OSError:
        return None

    if not entries:
        return None

    if timestamp is None:
        timestamp = datetime.now().strftime(timestamp_fmt)

    archive_path = f"{log_dir}_{timestamp}"
    shutil.move(log_dir, archive_path)
    return archive_path


def ensure_logs_dir(log_dir: str, *, archive_if_nonempty: bool = False) -> None:
    """Ensure log_dir exists, optionally archiving an existing non-empty dir."""
    log_dir = os.path.abspath(log_dir)
    if archive_if_nonempty:
        try:
            archive_logs_dir_if_nonempty(log_dir)
        except Exception:  # pylint: disable=broad-exception-caught
            # Best-effort: do not fail the run if archiving fails.
            pass
    os.makedirs(log_dir, exist_ok=True)

