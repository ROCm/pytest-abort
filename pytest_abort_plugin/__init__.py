"""Pytest plugin for ROCm/JAX abort (hard crash) detection.

This plugin writes a small JSON file indicating the currently running test.
If the pytest process crashes (segfault/abort), the file remains and can be
consumed by the outer test runner to attribute the crash to the last test.
"""

