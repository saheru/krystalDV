"""Sanity tests for the dataset-aware agent tools and context management."""
from __future__ import annotations

import pytest

from kdv.agent.context import ConversationContext
from kdv.agent.tools import ToolContext, build_default_registry
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
