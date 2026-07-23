"""Regression tests for best-effort braille-send telemetry logging.

A non-writable study-log directory on a managed server (root-owned bind
mount, non-root container user, redeploy resetting ownership) must never turn
a successful render into an HTTP 400. These tests lock in that guarantee.
"""

import app.server as server


def test_write_braille_event_survives_unwritable_path(tmp_path, monkeypatch):
    # Make the log path uncreatable: an ancestor is a regular file, so
    # BRAILLE_LOG_PATH.parent.mkdir(...) raises OSError (NotADirectoryError).
    # This mirrors an unwritable/read-only log dir on a managed server.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file, not a directory")
    unwritable = blocker / "logs" / "braille_send_events.jsonl"
    monkeypatch.setattr(server, "BRAILLE_LOG_PATH", unwritable)

    # Must NOT raise: telemetry is best-effort and cannot break /render.
    server._write_braille_event({"event": "braille_send", "sequence": 1})


def test_write_braille_event_writes_when_path_is_writable(tmp_path, monkeypatch):
    target = tmp_path / "logs" / "braille_send_events.jsonl"
    monkeypatch.setattr(server, "BRAILLE_LOG_PATH", target)

    server._write_braille_event({"event": "braille_send", "sequence": 7})

    assert target.exists()
    assert '"sequence": 7' in target.read_text(encoding="utf-8")


def test_resolve_braille_log_path_honors_writable_env(tmp_path, monkeypatch):
    target = tmp_path / "custom-logs" / "events.jsonl"
    monkeypatch.setenv("BRAILLE_LOG_PATH", str(target))

    assert server._resolve_braille_log_path() == target


def test_resolve_braille_log_path_ignores_unwritable_env(tmp_path, monkeypatch):
    # Explicit but uncreatable path (ancestor is a file) must be rejected in
    # favor of an auto-detected writable directory rather than pinned blindly.
    blocker = tmp_path / "ro_file"
    blocker.write_text("file")
    monkeypatch.setenv("BRAILLE_LOG_PATH", str(blocker / "nope" / "events.jsonl"))

    resolved = server._resolve_braille_log_path()

    assert resolved != blocker / "nope" / "events.jsonl"
    assert resolved.name == "braille_send_events.jsonl"
