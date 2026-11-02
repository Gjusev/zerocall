"""Tests for the decision cache core: guards, record/lookup, persistence."""
from pathlib import Path

import pytest

from zerocall import DecisionCache, Guard, evaluate_guard, signature


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    return tmp_path


def test_record_lookup_roundtrip(ws, tmp_path):
    cache = DecisionCache(root=ws, store=tmp_path / "cache.json")
    sig = signature("task", "a.py")
    cache.record(sig, {"op": "write_file", "path": "a.py", "content": "x = 2\n"}, [Guard.file_hash_text("a.py", "x = 2\n")])
    res = cache.lookup(sig)
    assert res is not None
    assert res.action["content"] == "x = 2\n"
    assert res.reusable is False  # file on disk is still x = 1, not the post-state


def test_reusable_when_post_state_matches(ws, tmp_path):
    cache = DecisionCache(root=ws, store=tmp_path / "cache.json")
    sig = signature("task", "a.py")
    guards = [Guard.file_hash_text("a.py", "x = 1\n")]
    cache.record(sig, {"op": "write_file", "path": "a.py", "content": "x = 1\n"}, guards)
    assert cache.lookup(sig).reusable is True


def test_lookup_miss_returns_none(ws, tmp_path):
    cache = DecisionCache(root=ws, store=tmp_path / "cache.json")
    assert cache.lookup(signature("nope")) is None


def test_guard_kinds(ws):
    assert evaluate_guard(Guard.file_exists("a.py"), ws).ok
    assert not evaluate_guard(Guard.file_exists("missing.py"), ws).ok
    assert evaluate_guard(Guard.file_hash_of(ws, "a.py"), ws).ok
    (ws / "a.py").write_text("x = 42\n", encoding="utf-8")
    assert not evaluate_guard(Guard.file_hash(ws / "a.py", "deadbeef"), ws).ok


def test_path_traversal_blocked(ws):
    outside = ws.parent / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    assert not evaluate_guard(Guard.file_exists("../outside.txt"), ws).ok
    assert not evaluate_guard(Guard.file_hash("../outside.txt", "any"), ws).ok


def test_command_guard(ws):
    ok = evaluate_guard(Guard.command_exit_code("parses", ["a.py"]), ws)
    assert ok.ok, ok.detail
    bad = evaluate_guard(Guard.command_exit_code("parses", ["broken.py"]), ws)
    assert not bad.ok


def test_command_guard_allowlist(ws):
    # non-whitelisted snippet must be refused without running anything
    g = Guard.command_exit_code("rm -rf", ["a.py"])
    res = evaluate_guard(g, ws)
    assert not res.ok and "allowlist" in res.detail
    # argument escaping the root must be refused
    res = evaluate_guard(Guard.command_exit_code("parses", ["../outside.py"]), ws)
    assert not res.ok


def test_dir_listing_guard(ws):
    from zerocall import Guard

    g = Guard.dir_listing_of(ws, ".", "*.py")
    assert evaluate_guard(g, ws).ok
    (ws / "zzz.py").write_text("z = 1\n", encoding="utf-8")
    res = evaluate_guard(g, ws)
    assert not res.ok and res.detail == "listing differs"


def test_unknown_guard_kind_fails_closed(ws):
    res = evaluate_guard(Guard("wget", {"url": "http://x"}), ws)
    assert not res.ok


def test_persistence_across_instances(ws, tmp_path):
    store = tmp_path / "cache.json"
    sig = signature("task", "a.py")
    DecisionCache(root=ws, store=store).record(
        sig, {"op": "noop"}, [Guard.file_exists("a.py")]
    )
    cache2 = DecisionCache(root=ws, store=store)
    assert len(cache2) == 1
    assert cache2.lookup(sig).reusable is True


def test_corrupt_store_is_ignored(ws, tmp_path):
    store = tmp_path / "cache.json"
    store.write_text("{not json", encoding="utf-8")
    cache = DecisionCache(root=ws, store=store)
    assert len(cache) == 0
