from kdv.viz.column_stats import summarize_columns
from kdv.viz.recommender import recommend_charts


def test_recommends_pie_for_categorical():
    rows = [{"cat": v} for v in ["A", "B", "A", "C", "B"] * 4]
    stats = summarize_columns(["cat"], rows)
    kinds = {s.kind for s in recommend_charts(stats)}
    assert "donut" in kinds and "bar_h" in kinds


def test_recommends_scatter_and_corr_for_numerics():
    rows = [{"x": i, "y": i * 2.0, "z": i + 1.5} for i in range(20)]
    stats = summarize_columns(["x", "y", "z"], rows)
    kinds = {s.kind for s in recommend_charts(stats)}
    assert "scatter" in kinds
    assert "heatmap_corr" in kinds
    assert "histogram" in kinds


def test_recommends_timeseries_when_date_present():
    rows = [
        {"date": f"2024-01-{i:02d}", "v": float(i)}
        for i in range(1, 11)
    ]
    stats = summarize_columns(["date", "v"], rows)
    kinds = {s.kind for s in recommend_charts(stats)}
    assert "timeseries" in kinds
