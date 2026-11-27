"""The browser's refused attempts in a world with no door: a kit-owned file, cut at each start, capped, and read back
as a trial's blocked list, or as unreadable, never as empty."""

from __future__ import annotations

import json
from pathlib import Path

from fi.alk.harness import browser_blocked_log, browser_record
from fi.alk.harness.browser_blocked_log import BlockedFileLog, blocked_from_file


def _refused(log, host: str) -> None:
    from fi.alk.harness.egress_proxy import EgressProxy

    class _Proxy(EgressProxy):
        via = "browser"

    proxy = _Proxy([], log)
    try:
        proxy.refuse(host, "GET")
    finally:
        proxy.close()


def test_a_world_with_no_door_keeps_the_browser_s_refusals_in_a_kit_file_cut_at_each_trial(tmp_path) -> None:
    log = BlockedFileLog(tmp_path / "w0-blocked.jsonl")
    _refused(log, "www.example.com")
    _refused(log, "www.example.com")
    assert blocked_from_file(tmp_path / "w0-blocked.jsonl") == [
        {"host": "www.example.com", "method": "GET", "via": "browser", "count": 2}
    ]
    log.reset()
    assert blocked_from_file(tmp_path / "w0-blocked.jsonl") == []


def test_a_blocked_file_is_capped_and_says_so(tmp_path) -> None:
    log = BlockedFileLog(tmp_path / "w0-blocked.jsonl", cap=2)
    for host in ["a.example.com", "b.example.com", "c.example.com"]:
        _refused(log, host)
    found = blocked_from_file(tmp_path / "w0-blocked.jsonl")
    assert [one["host"] for one in found if "host" in one] == ["a.example.com", "b.example.com"]
    assert {"truncated": True} in found


def test_refusals_past_the_cap_add_no_more_lines_to_the_blocked_file(tmp_path) -> None:
    path = tmp_path / "w0-blocked.jsonl"
    log = BlockedFileLog(path, cap=2)
    for index in range(6):
        _refused(log, f"h{index}.example.com")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[-1]) == {"truncated": True}


def test_a_blocked_log_holds_no_more_in_memory_than_in_its_file(tmp_path) -> None:
    log = BlockedFileLog(tmp_path / "w0-blocked.jsonl", cap=2)
    for index in range(6):
        _refused(log, f"h{index}.example.com")
    assert len(log.rows()) <= 2


def test_a_blocked_file_that_could_not_be_cut_is_unreadable_never_the_last_trial_s(tmp_path, monkeypatch) -> None:
    path = tmp_path / "w0-blocked.jsonl"
    log = BlockedFileLog(path)
    _refused(log, "last.example.com")

    def cannot(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(browser_blocked_log, "write_text", cannot)
    log.reset()
    assert blocked_from_file(path) is None


def test_a_blocked_file_one_byte_past_its_limit_reads_as_unreadable_even_cut_at_a_line_end(
    tmp_path, monkeypatch
) -> None:
    line = json.dumps({"arguments": {"host": "a.example.com", "method": "GET", "via": "browser"}}) + "\n"
    big = tmp_path / "w0-blocked.jsonl"
    big.write_text(line * 3)
    # Every line whole, and one byte more than any file the kit writes.
    monkeypatch.setattr(browser_blocked_log, "KIT_FILE_BYTES", len(line) * 3 - 1)
    assert blocked_from_file(big) is None
    monkeypatch.setattr(browser_blocked_log, "KIT_FILE_BYTES", len(line) * 3)
    assert blocked_from_file(big) is not None


def test_a_kit_file_larger_than_its_limit_is_never_read_whole(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(browser_blocked_log, "KIT_FILE_BYTES", 64)
    asked: list[int] = []
    path_open = Path.open

    class _Counted:
        def __init__(self, handle) -> None:
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            self.handle.close()

        def read(self, size: int = -1) -> bytes:
            asked.append(size)
            return self.handle.read(size)

    def counted_open(path, mode="r", *args, **kwargs):
        handle = path_open(path, mode, *args, **kwargs)
        return _Counted(handle) if mode == "rb" else handle

    monkeypatch.setattr(Path, "open", counted_open)
    big = tmp_path / "w0-blocked.jsonl"
    big.write_text(json.dumps({"arguments": {"host": "a.example.com"}, "pad": "x" * 100}) + "\n")
    assert blocked_from_file(big) is None
    record = tmp_path / "w0-record.json"
    record.write_text(json.dumps({"complete": True, "rows": [], "pad": "x" * 100}))
    assert browser_record.sealed_record(record, timeout=0.1) is None
    # Never more than one byte past the limit is asked for, so a file of any size costs no more to refuse.
    assert asked and all(0 <= size <= 65 for size in asked)


def test_a_blocked_file_that_cannot_be_read_is_unreadable_never_empty(tmp_path) -> None:
    assert blocked_from_file(tmp_path / "missing.jsonl") is None
    (tmp_path / "bad.jsonl").write_text("{not json\n")
    assert blocked_from_file(tmp_path / "bad.jsonl") is None
