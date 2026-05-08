"""Generate a supplier-billing reconciliation dataset.

Run::

    python samples/generate_billing_data.py

Produces ``samples/渠道商应付明细_测试数据.xlsx`` (~250 行) — the *internal*
view of what each channel partner should be paid each month.

Use case (matches the user's chat-history requirement):
    1. 用户上传这份内部明细
    2. 在 Agent 中粘贴各渠道商真实对账单金额
    3. Agent 调用 reconcile 工具，给出每个供应商**月度差异**和**总差异**

To make the demo interesting, the script bakes in realistic discrepancies:
    - 美团：实际多收 ~3% (服务费没算)
    - 饿了么：实际比内部少（内部多算了一笔）
    - 抖音外卖：基本一致 (差额 < 0.5%)
    - 京东到家：某月少收 ~10% (漏单)
    - 顺丰同城：与内部完全一致
    - 闪送：实际多收 ~5% (运费上调没同步系统)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

random.seed(42)

OUT = Path(__file__).resolve().parent / "渠道商应付明细_测试数据.xlsx"

# ----------------------------------------------------------------------
# Internal-system "expected payable" structure per channel.
# (mean amount per single transaction, transactions per month)
CHANNELS = {
    "美团":     {"avg": 380, "tx_per_month": 45, "service_types": ["配送服务", "平台佣金", "营销推广"]},
    "饿了么":   {"avg": 330, "tx_per_month": 32, "service_types": ["配送服务", "平台佣金"]},
    "抖音外卖": {"avg": 270, "tx_per_month": 28, "service_types": ["平台佣金", "流量补贴"]},
    "京东到家": {"avg": 410, "tx_per_month": 18, "service_types": ["配送服务", "平台佣金"]},
    "顺丰同城": {"avg": 220, "tx_per_month": 24, "service_types": ["配送服务"]},
    "闪送":     {"avg": 95,  "tx_per_month": 38, "service_types": ["配送服务", "加急加价"]},
}

MONTHS = ["2026-02", "2026-03", "2026-04"]
STATUSES = ["已结算", "已结算", "已结算", "对账中", "争议中"]


# ----------------------------------------------------------------------
def main() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "渠道商应付明细"

    headers = [
        "流水ID",
        "日期",
        "月份",
        "渠道商",
        "服务类型",
        "订单数",
        "应付金额_元",
        "状态",
        "备注",
    ]
    ws.append(headers)
    fill = PatternFill("solid", fgColor="5B6CFF")
    font = Font(bold=True, color="FFFFFF")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")

    txid = 60001
    for month_str in MONTHS:
        year, mon = month_str.split("-")
        first = datetime(int(year), int(mon), 1)
        for channel, conf in CHANNELS.items():
            for _ in range(conf["tx_per_month"]):
                day = random.randint(1, 27)
                hour = random.randint(8, 22)
                minute = random.randint(0, 59)
                dt = first.replace(day=day, hour=hour, minute=minute)
                stype = random.choice(conf["service_types"])
                # 1-12 orders per transaction
                norders = random.randint(1, 12)
                # gaussian around channel mean
                amount = max(50, random.gauss(conf["avg"], conf["avg"] * 0.35)) * (norders / 6)
                amount = round(amount, 2)
                status = random.choices(STATUSES, weights=[5, 5, 5, 1, 1])[0]
                note = ""
                if status == "争议中":
                    note = random.choice(["金额有疑义", "服务次数对不上", "等待对方确认"])
                elif status == "对账中":
                    note = "等待月度对账"
                row = [
                    f"BL-{txid:06d}",
                    dt.strftime("%Y-%m-%d %H:%M"),
                    month_str,
                    channel,
                    stype,
                    norders,
                    amount,
                    status,
                    note,
                ]
                ws.append(row)
                txid += 1

    widths = [12, 18, 10, 12, 12, 8, 14, 10, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    wb.save(OUT)

    # ---- compute internal totals per (channel, month) for reference -----
    by_cm: dict[tuple[str, str], float] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        cm = (row[3], row[2])
        by_cm[cm] = by_cm.get(cm, 0) + (row[6] or 0)

    print(f"已生成对账测试数据：{OUT}")
    print(f"  · 共 {ws.max_row - 1} 行明细 · {len(CHANNELS)} 个渠道商 × {len(MONTHS)} 个月")
    print()
    print("内部应付总额（聚合后）：")
    print("  渠道       2026-02      2026-03      2026-04      合计")
    print("  " + "-" * 66)
    for ch in CHANNELS:
        m1 = by_cm.get((ch, "2026-02"), 0)
        m2 = by_cm.get((ch, "2026-03"), 0)
        m3 = by_cm.get((ch, "2026-04"), 0)
        total = m1 + m2 + m3
        print(f"  {ch:<8}  {m1:>10,.0f}  {m2:>10,.0f}  {m3:>10,.0f}  {total:>10,.0f}")

    # Build "供应商真实账单" with realistic discrepancies for the README
    # (saved next to the data file as a TXT for easy paste).
    actual = {}
    actual["美团"] = {m: round(by_cm[("美团", m)] * 1.03, 0) for m in MONTHS}            # +3%
    actual["饿了么"] = {m: round(by_cm[("饿了么", m)] * 0.96, 0) for m in MONTHS}        # -4%
    actual["抖音外卖"] = {m: round(by_cm[("抖音外卖", m)] * 1.003, 0) for m in MONTHS}   # ~0.3%
    # 京东到家 漏一笔大单 in 2026-03
    actual["京东到家"] = {
        "2026-02": round(by_cm[("京东到家", "2026-02")], 0),
        "2026-03": round(by_cm[("京东到家", "2026-03")] * 0.90, 0),
        "2026-04": round(by_cm[("京东到家", "2026-04")], 0),
    }
    actual["顺丰同城"] = {m: round(by_cm[("顺丰同城", m)], 0) for m in MONTHS}           # 完全一致
    actual["闪送"] = {m: round(by_cm[("闪送", m)] * 1.05, 0) for m in MONTHS}            # +5%

    actual_path = OUT.parent / "渠道商对账单_粘贴版.txt"
    with actual_path.open("w", encoding="utf-8") as f:
        f.write("# 渠道商真实对账单（直接复制粘贴到 Agent 任务里）\n\n")
        f.write("## 单月对账（2026-04）\n\n")
        f.write(
            "对账分析：以下是各渠道商 2026-04 月给我们的真实对账单金额，"
            "和我们系统记录做差异对比，找出每个渠道的差额和合计差额。\n\n"
        )
        for ch, by_m in actual.items():
            f.write(f"  {ch}: {by_m['2026-04']:.0f}\n")
        f.write("\n## 三月份汇总对账（用 Agent 多任务）\n\n")
        f.write("# 任务 1\n")
        f.write("以渠道商分组，做 2026-02 月对账分析。各渠道实际账单：\n")
        for ch, by_m in actual.items():
            f.write(f"  {ch}: {by_m['2026-02']:.0f}\n")
        f.write("\n# 任务 2\n")
        f.write("以渠道商分组，做 2026-03 月对账分析。各渠道实际账单：\n")
        for ch, by_m in actual.items():
            f.write(f"  {ch}: {by_m['2026-03']:.0f}\n")
        f.write("\n# 任务 3\n")
        f.write("以渠道商分组，做 2026-04 月对账分析。各渠道实际账单：\n")
        for ch, by_m in actual.items():
            f.write(f"  {ch}: {by_m['2026-04']:.0f}\n")
        f.write("\n# 任务 4\n")
        f.write(
            "汇总三个月每个渠道商的总差异（实际 - 内部），按差额绝对值排序，"
            "找出差异最大的渠道，做柱状图。\n"
        )
    print(f"\n对账单（含已计算的真实金额）已保存到：{actual_path}")
    print("  · 使用方式：Agent 模式下复制粘贴到分析目标即可")


if __name__ == "__main__":
    main()
