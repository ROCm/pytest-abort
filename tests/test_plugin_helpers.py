from __future__ import annotations

import json

from pytest_abort import plugin


def test_atomic_write_json_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "marker.json"
    payload = {"a": 1}

    plugin._atomic_write_json(str(p), payload)  # pylint: disable=protected-access
    assert json.loads(p.read_text(encoding="utf-8")) == payload

