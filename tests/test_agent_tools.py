"""Sanity tests for the dataset-aware agent tools and context management."""
from __future__ import annotations

import pytest

from kdv.agent.context import ConversationContext
from kdv.agent.tools import TableSlot, ToolContext, build_default_registry
from kdv.excel.reader import ExcelTable, concat_tables
from kdv.viz.column_stats import summarize_columns


@pytest.fixture
def small_dataset():
    cols = ["供应商", "金额", "月份"]
    rows = [
        {"供应商": "A", "金额": 12000, "月份": "2024-01"},
        {"供应商": "B", "金额": 9000, "月份": "2024-01"},
        {"供应商": "A", "金额": 13500, "月份": "2024-02"},
        {"供应商": "B", "金额": 8500, "月份": "2024-02"},
    ]
    stats = summarize_columns(cols, rows)
    return ToolContext(columns=cols, rows=rows, stats=stats)


def test_list_columns_lists_all(small_dataset):
    reg = build_default_registry()
    out = reg.get("list_columns").handler(small_dataset, {})
    assert "供应商" in out and "金额" in out and "月份" in out


def test_aggregate_with_group_by(small_dataset):
    reg = build_default_registry()
    out = reg.get("aggregate").handler(
        small_dataset, {"column": "金额", "op": "sum", "group_by": "供应商"}
    )
    assert "A" in out and "B" in out
    # A总: 25500, B总: 17500
    assert "25500" in out
    assert "17500" in out


def test_filter_rows_by_threshold(small_dataset):
    reg = build_default_registry()
    out = reg.get("filter_rows").handler(
        small_dataset, {"column": "金额", "op": "gt", "value": 10000}
    )
    # 12000 and 13500 should match → 2/4
    assert "2/4" in out


def test_correlate_returns_pearson(small_dataset):
    reg = build_default_registry()
    # Add a correlated column to both rows AND columns list
    for r in small_dataset.rows:
        r["折扣"] = r["金额"] * 0.05
    small_dataset.columns.append("折扣")
    small_dataset.stats = summarize_columns(small_dataset.columns, small_dataset.rows)

    out = reg.get("correlate").handler(
        small_dataset, {"col_a": "金额", "col_b": "折扣"}
    )
    assert "1.0000" in out or "0.99" in out  # near-perfect linear


def test_reconcile_internal_vs_external(small_dataset):
    reg = build_default_registry()
    out = reg.get("reconcile").handler(
        small_dataset,
        {
            "group_column": "供应商",
            "value_column": "金额",
            "external_values": {"A": 26000, "B": 18000},
            "agg": "sum",
            "label": "对账",
        },
    )
    # Internal A=25500, ext=26000, diff=-500
    assert "-500" in out
    # Total diff: internal 43000 vs external 44000 = -1000
    assert "-1000" in out
    # Auto-chart added
    assert any(c.kind == "reconcile_bar" for c in small_dataset.charts)


def test_add_chart_validates_kind(small_dataset):
    reg = build_default_registry()
    bad = reg.get("add_chart").handler(
        small_dataset, {"kind": "spaghetti", "columns": ["供应商"], "title": "x"}
    )
    assert "不支持" in bad
    ok = reg.get("add_chart").handler(
        small_dataset, {"kind": "donut", "columns": ["供应商"], "title": "供应商分布"}
    )
    assert "已添加" in ok
    assert any(c.title == "供应商分布" for c in small_dataset.charts)


def test_record_insight_appends(small_dataset):
    reg = build_default_registry()
    reg.get("record_insight").handler(
        small_dataset, {"title": "异常", "body": "供应商 A 月度上涨 12.5%", "severity": "warning"}
    )
    assert len(small_dataset.insights) == 1
    assert small_dataset.insights[0].severity == "warning"


# ----------------------------------------------------------------------
# ConversationContext compaction
# ----------------------------------------------------------------------
def test_context_compaction_replaces_middle():
    ctx = ConversationContext(
        model="gpt-4o-mini",
        max_context_tokens=2000,
        output_reserve_tokens=400,
        keep_recent=2,
        safety_margin=0.5,
    )
    ctx.add_system("you are an agent")
    for i in range(8):
        ctx.add_user(f"long message {i} " + "X" * 200)
        ctx.add_assistant(f"reply {i} " + "Y" * 200)

    n_before = len(ctx.messages)
    assert ctx.needs_compaction()
    n = ctx.compact_with(lambda _prompt: "summary text")
    assert n > 0
    # head (system) + 1 summary + last 2 messages
    assert len(ctx.messages) <= 1 + 1 + 2
    assert "summary text" in ctx.messages[1].content
    assert n_before > len(ctx.messages)


async def test_compact_async_chunks_huge_middle_into_multiple_calls():
    """Big middles must be split into per-chunk LLM calls so each call stays
    small/fast — without this guard the compaction prompt itself was as big
    as the context being compacted, which is what made super-long tables
    blow up with chained timeouts.
    """
    ctx = ConversationContext(
        model="gpt-4o-mini",
        max_context_tokens=2000,
        output_reserve_tokens=400,
        keep_recent=2,
        safety_margin=0.5,
    )
    ctx.add_system("system")
    # 10 messages × ~1200 chars each → middle joins to ~12k chars,
    # well above the 6000-char single-shot budget → must be chunked.
    big = "Z" * 1200
    for i in range(5):
        ctx.add_user(f"u{i} {big}")
        ctx.add_assistant(f"a{i} {big}")

    calls: list[str] = []

    async def fake_summarizer(prompt: str) -> str:
        calls.append(prompt)
        # Every chunk gets a short summary mentioning its own index.
        idx = len(calls)
        return f"chunk-{idx}-summary"

    n = await ctx.compact_async(fake_summarizer)
    assert n > 0
    # Multiple summarizer calls = chunked path (single-shot would be 1).
    assert len(calls) >= 2, f"expected hierarchical chunking, got {len(calls)} call(s)"
    # No single chunk prompt may exceed the budget (with some slack for the
    # prefix instructions added per chunk).
    for prompt in calls:
        assert len(prompt) < 8000, f"chunk prompt too big: {len(prompt)} chars"
    # All chunk summaries land in the synthesized middle message.
    synth = ctx.messages[1]
    assert synth.role == "system"
    for i in range(1, len(calls) + 1):
        assert f"chunk-{i}-summary" in synth.content


async def test_compact_async_returns_zero_on_empty_summary():
    """Empty summary must keep the context untouched — otherwise we'd
    silently destroy the conversation and leave the agent unable to recover.
    """
    ctx = ConversationContext(
        model="gpt-4o-mini",
        max_context_tokens=2000,
        output_reserve_tokens=400,
        keep_recent=2,
        safety_margin=0.5,
    )
    ctx.add_system("sys")
    for i in range(6):
        ctx.add_user(f"u{i} " + "X" * 200)
        ctx.add_assistant(f"a{i} " + "Y" * 200)

    msgs_before = list(ctx.messages)

    async def empty_summarizer(_prompt: str) -> str:
        return "   "  # whitespace counts as empty

    n = await ctx.compact_async(empty_summarizer)
    assert n == 0
    assert ctx.messages == msgs_before


# ----------------------------------------------------------------------
# Multi-table workbook support
# ----------------------------------------------------------------------
def _make_two_table_ctx() -> ToolContext:
    """Two tables on the same key — used for cross-table tool tests."""
    a = TableSlot(
        table_id="invoices.xlsx/Sheet1",
        columns=["渠道", "金额"],
        rows=[
            {"渠道": "美团", "金额": 19144},
            {"渠道": "饿了么", "金额": 11446},
            {"渠道": "抖音外卖", "金额": 8894},
        ],
        stats=summarize_columns(["渠道", "金额"], []),
    )
    b = TableSlot(
        table_id="ledger.xlsx/Sheet1",
        columns=["渠道", "金额"],
        rows=[
            {"渠道": "美团", "金额": 19200},   # +56
            {"渠道": "饿了么", "金额": 11000},  # -446
            {"渠道": "顺丰同城", "金额": 6436},  # only in B
        ],
        stats=summarize_columns(["渠道", "金额"], []),
    )
    return ToolContext(tables=[a, b])


def test_legacy_single_table_constructor_still_works():
    cols = ["x", "y"]
    rows = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    ctx = ToolContext(columns=cols, rows=rows, stats=summarize_columns(cols, rows))
    # Legacy field aliases still work...
    assert ctx.columns == cols and ctx.rows == rows
    # ...and a TableSlot was synthesised so multi-table tools also work.
    assert len(ctx.tables) == 1
    assert ctx.tables[0].columns == cols


def test_list_tables_lists_all_loaded_tables():
    ctx = _make_two_table_ctx()
    reg = build_default_registry()
    out = reg.get("list_tables").handler(ctx, {})
    assert "invoices.xlsx/Sheet1" in out
    assert "ledger.xlsx/Sheet1" in out
    # Multi-table reminder must be present so the LLM knows to pass `table=`.
    assert "table=" in out


def test_aggregate_picks_table_via_table_arg():
    """Aggregating against table B with a `table` arg returns B's numbers,
    not A's — proves the dispatch is wired through every tool, not just the
    new cross-table ones."""
    ctx = _make_two_table_ctx()
    reg = build_default_registry()
    out = reg.get("aggregate").handler(
        ctx, {"table": "ledger.xlsx/Sheet1", "column": "金额", "op": "sum"},
    )
    # B sum = 19200 + 11000 + 6436 = 36636
    assert "36636" in out
    # And the response cites the right table id.
    assert "ledger.xlsx/Sheet1" in out


def test_cross_reconcile_diff_per_group():
    ctx = _make_two_table_ctx()
    reg = build_default_registry()
    out = reg.get("cross_reconcile").handler(
        ctx,
        {
            "table_a": "invoices.xlsx/Sheet1",
            "table_b": "ledger.xlsx/Sheet1",
            "group_column": "渠道",
            "value_column": "金额",
        },
    )
    # 美团: 19144-19200 = -56, 饿了么: 11446-11000=+446, 抖音外卖: only in A,
    # 顺丰同城: only in B → B-side row should also surface.
    assert "美团" in out and "顺丰同城" in out
    assert "-56" in out or "-56.00" in out


def test_join_tables_inner_join_sample():
    ctx = _make_two_table_ctx()
    reg = build_default_registry()
    out = reg.get("join_tables").handler(
        ctx,
        {
            "table_a": "invoices.xlsx/Sheet1",
            "table_b": "ledger.xlsx/Sheet1",
            "key": "渠道",
        },
    )
    # 美团 + 饿了么 are in both → expect 2 matched rows.
    assert "2 行匹配" in out


def test_concat_tables_adds_table_id_column():
    a = ExcelTable(columns=["x", "y"], rows=[{"x": 1, "y": 2}],
                   sheet_name="s1", source_path="a.xlsx", table_id="a/s1")
    b = ExcelTable(columns=["x", "z"], rows=[{"x": 9, "z": 8}],
                   sheet_name="s1", source_path="b.xlsx", table_id="b/s1")
    merged = concat_tables([a, b])
    assert "_table_id" in merged.columns
    assert {r["_table_id"] for r in merged.rows} == {"a/s1", "b/s1"}
    # Union of columns; missing values become None.
    assert {"x", "y", "z"} <= set(merged.columns)
    assert merged.rows[0]["z"] is None  # row from a has no z
    assert merged.rows[1]["y"] is None  # row from b has no y
