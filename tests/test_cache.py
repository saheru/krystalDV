from pathlib import Path

from kdv.analysis.cache import RunCache, hash_row


def test_cache_put_get_resume(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite"
    with RunCache(db) as cache:
        cache.start_run("r1", preset_id="p1", model_id="m1", total_rows=2, mode="row_by_row")
        row = {"a": 1, "b": "hi"}
        h = hash_row(row)
        cache.put_row("r1", 0, input_hash=h, output={"out": "x"}, error=None)
        assert cache.get_row("r1", 0, h) == {"out": "x"}
        # different hash → cache miss
        assert cache.get_row("r1", 0, "deadbeef") is None
        # error rows are not returned as success
        cache.put_row("r1", 1, input_hash="abc", output=None, error="boom")
        assert cache.get_row("r1", 1, "abc") is None
        cache.finish_run("r1", "summary md")

    # reopen → still readable
    with RunCache(db) as cache:
        rows = cache.list_rows("r1")
        assert {r["row_index"] for r in rows} == {0, 1}


def test_hash_row_stable_under_key_order():
    a = hash_row({"x": 1, "y": 2})
    b = hash_row({"y": 2, "x": 1})
    assert a == b
