"""Tests for the agent task-list parser."""
from kdv.agent.manager import parse_tasks


def test_empty_input_returns_empty():
    assert parse_tasks("") == []
    assert parse_tasks("   \n  ") == []


def test_single_line_one_task():
    assert parse_tasks("找出销售额最高的前 10 行") == ["找出销售额最高的前 10 行"]


def test_one_task_per_line_when_no_structure():
    text = "任务A\n任务B\n任务C"
    assert parse_tasks(text) == ["任务A", "任务B", "任务C"]


def test_explicit_task_delimiters():
    text = """\
# 任务 1
以渠道商分组，做 2026-02 月对账分析。各渠道实际账单：
美团: 19144
饿了么: 11446

# 任务 2
以渠道商分组，做 2026-03 月对账分析。
美团: 21432
饿了么: 12287"""
    tasks = parse_tasks(text)
    assert len(tasks) == 2
    # Each task body keeps its multi-line content intact
    assert "以渠道商分组" in tasks[0]
    assert "美团: 19144" in tasks[0]
    assert "饿了么: 11446" in tasks[0]
    # No "美团" line should be a standalone task
    assert "美团: 19144" not in tasks  # not as a separate task
    # Second task is fully captured
    assert "以渠道商分组" in tasks[1]
    assert "美团: 21432" in tasks[1]


def test_blank_line_separated_paragraphs():
    text = """\
找出销售额最高的前 10 行
做柱状图

把销售按月份做时序图

渠道A 12000、B 8500，做差额对比"""
    tasks = parse_tasks(text)
    assert len(tasks) == 3
    assert tasks[0].startswith("找出销售额")
    assert "柱状图" in tasks[0]
    assert tasks[1] == "把销售按月份做时序图"
    assert "渠道A 12000" in tasks[2]


def test_markdown_headings_are_stripped_when_blank_line_separates():
    """When a heading is followed by a body separated by blank line, the
    body becomes one task and the heading is dropped."""
    text = """\
# 渠道商对账（这一行是注释）

这是真正的任务内容
继续描述"""
    tasks = parse_tasks(text)
    assert len(tasks) == 1
    assert "这是真正的任务内容" in tasks[0]
    assert "渠道商对账" not in tasks[0]


def test_markdown_headings_inline_fall_back_to_line_by_line():
    """Without a blank line or explicit delimiter, treat each non-comment
    line as its own task. Heading lines are filtered out."""
    text = """\
# 一句注释
任务 A 描述
任务 B 描述"""
    tasks = parse_tasks(text)
    assert tasks == ["任务 A 描述", "任务 B 描述"]


def test_user_screenshot_paste_format():
    """The exact format of samples/渠道商对账单_粘贴版.txt that broke things."""
    text = """\
# 渠道商真实对账单（直接复制粘贴到 Agent 任务里）

## 单月对账（2026-04）

对账分析：以下是各渠道商 2026-04 月给我们的真实对账单金额，和我们系统记录做差异对比，找出每个渠道的差额和合计差额。

  美团: 21351
  饿了么: 10394
  抖音外卖: 11047
  京东到家: 8923
  顺丰同城: 5611
  闪送: 3924

## 三月份汇总对账（用 Agent 多任务）

# 任务 1
以渠道商分组，做 2026-02 月对账分析。各渠道实际账单：
  美团: 19144
  饿了么: 11446

# 任务 2
以渠道商分组，做 2026-03 月对账分析。各渠道实际账单：
  美团: 21432
  饿了么: 12287

# 任务 3
以渠道商分组，做 2026-04 月对账分析。各渠道实际账单：
  美团: 21351
  饿了么: 10394

# 任务 4
汇总三个月每个渠道商的总差异（实际 - 内部），按差额绝对值排序，找出差异最大的渠道，做柱状图。"""
    tasks = parse_tasks(text)
    # Should be 4 tasks (the markdown headings are stripped).
    assert len(tasks) == 4
    # No task is a bare "美团: 19144" line
    for t in tasks:
        assert t.strip() not in ("美团: 19144", "美团: 21351", "饿了么: 11446")
    # First is the 2026-02 task (preceded by initial preamble that gets dropped)
    assert "2026-02" in tasks[0]
    assert "美团: 19144" in tasks[0]
    assert "2026-03" in tasks[1]
    assert "美团: 21432" in tasks[1]
    assert "2026-04" in tasks[2]
    assert "汇总三个月" in tasks[3]
