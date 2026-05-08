from pathlib import Path

from kdv.analysis.projects import ProjectSnapshot, ProjectStore


def _store(tmp_path: Path) -> ProjectStore:
    db = tmp_path / "runs.sqlite"
    return ProjectStore(db)


def test_save_list_load_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snap = ProjectSnapshot(
        name="测试项目",
        preset_name="p1",
        model_id="gpt-4o-mini",
        analysis_model_name="客服情感",
        mode="row_by_row",
        source_path="/tmp/data.xlsx",
        columns=["a", "b"],
        rows=[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
        row_outputs=[{"label": "ok"}, {"label": "ok"}],
        row_errors=[None, None],
        summary_markdown="## 概览\n\n- 一切正常",
        prompt_tokens_total=100,
        completion_tokens_total=50,
        duration_ms_total=2300,
    )
    store.save(snap)
    metas = store.list()
    assert len(metas) == 1
    assert metas[0].name == "测试项目"
    assert metas[0].n_rows == 2
    loaded = store.load(snap.project_id)
    assert loaded is not None
    assert loaded.summary_markdown == snap.summary_markdown
    assert loaded.rows == snap.rows
    store.close()


def test_rename_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snap = ProjectSnapshot(name="原名", mode="summary")
    store.save(snap)
    store.rename(snap.project_id, "新名")
    metas = store.list()
    assert metas[0].name == "新名"
    store.delete(snap.project_id)
    assert store.list() == []
    store.close()


def test_list_orders_by_updated_at_desc(tmp_path: Path) -> None:
    import time

    store = _store(tmp_path)
    a = ProjectSnapshot(name="A", mode="summary")
    store.save(a)
    time.sleep(1.1)
    b = ProjectSnapshot(name="B", mode="summary")
    store.save(b)
    metas = store.list()
    assert [m.name for m in metas] == ["B", "A"]
    store.close()
