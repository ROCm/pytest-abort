"""Pytest plugin + helper library for abort (hard crash) detection.

This plugin writes a small JSON file indicating the currently running test.
If the pytest process crashes (segfault/abort), the file remains and can be
consumed by the outer test runner to attribute the crash to the last test.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pytest-abort")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]

