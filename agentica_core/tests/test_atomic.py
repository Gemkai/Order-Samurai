"""Atomic-write contract: the destination file must never disappear mid-write.

atomic_json_write exists to prevent a concurrent reader (the TS reflex-engine
watching wid_payload.json) from ever seeing a torn OR missing file. os.replace
already replaces the destination atomically on POSIX and Windows, so nothing may
remove the destination before the rename — doing so opens a window where the file
is absent and a reader gets FileNotFoundError.
"""
import json
import os

from agentica_core import atomic


def test_overwrite_leaves_destination_present_at_the_moment_of_replace(tmp_path, monkeypatch):
    target = tmp_path / "payload.json"
    target.write_text('{"old": true}', encoding="utf-8")

    real_replace = os.replace
    observed = {}

    def spy_replace(src, dst, *args, **kwargs):
        observed["dest_present_at_replace"] = os.path.exists(dst)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)

    atomic.atomic_json_write(target, {"new": True})

    # The rename must have run, and the destination must still have existed at
    # that instant (no unlink-first gap).
    assert observed.get("dest_present_at_replace") is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_writes_new_file_when_destination_absent(tmp_path):
    target = tmp_path / "fresh.json"
    atomic.atomic_json_write(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not target.with_suffix(target.suffix + ".tmp").exists()
