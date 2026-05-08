"""Generate a realistic test dataset for Krystal Data Vision.

Run::

    python samples/generate_sample_data.py

Produces ``samples/客户反馈_测试数据.xlsx`` (~400 rows) with:
    - mixed types (datetime, categorical, numeric, boolean, free text)
    - intentional patterns: VIP 高满意度、夜间反馈处理慢、线上渠道金额最大
    - light anomalies for the agent to surface
    - groupable column (渠道) + numeric (业务额) for reconcile demos
    - sentiment-bearing 中文 text for per-row classification mode
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

random.seed(42)  # deterministic output between runs

OUT = Path(__file__).resolve().parent / "客户反馈_测试数据.xlsx"
N_ROWS = 400

# ----------------------------------------------------------------------
CHANNELS = ["线上商城", "线下门店", "客服电话", "微信小程序", "天猫旗舰店"]
PRODUCT_CATEGORIES = ["家居家纺", "厨房用具", "智能家电", "美妆个护", "母婴用品", "运动户外"]
CUSTOMER_TYPES = ["VIP", "普通会员", "新客户", "流失召回"]
TAGS = ["质量问题", "物流慢", "客服态度", "价格贵", "包装破损", "功能正常", "性价比高", "推荐购买"]

POSITIVE_TEMPLATES = [
    "{prod}收到了，{adj}！包装很{pkg}，物流也很快，下次还会再买。",
    "{prod}质量{adj}，比预想的好太多，给客服小姐姐点赞，态度{att}。",
    "用了一周，{prod}{verb}得很好，没有出现任何问题，强烈推荐！",
    "{prod}是回头客买的，依然{adj}，性价比高，会推荐朋友。",
    "客服解决问题{att}，{prod}本身也{adj}，五星好评。",
]

NEUTRAL_TEMPLATES = [
    "{prod}收到了，整体还行，{adj}吧，没有特别惊艳。",
    "{prod}和描述一致，没毛病，但也没什么亮点，3 分。",
    "用着还可以，{prod}的{aspect}一般般，期待后续有改进。",
    "包装有点{neg_pkg}，但里面{prod}是好的，将就用了。",
]

NEGATIVE_TEMPLATES = [
    "{prod}收到时{neg_pkg}，联系客服半天没回复，态度{neg_att}。",
    "用了两天{prod}就{neg_verb}了，质量太差，要求退货退款。",
    "{prod}和图片严重不符，{neg_aspect}，已申请退货，浪费时间。",
    "客服一直{neg_att}，{prod}问题也没解决，非常失望。",
    "{prod}漏发了一个配件，等了一周还没补发，太影响使用了。",
    "{prod}有刺鼻气味，怀疑是甲醛超标，下不了嘴/不敢用。",
]

POS_ADJ = ["很满意", "超值", "惊喜", "棒极了", "非常好"]
NEG_ADJ = ["失望", "差", "糟糕", "不行", "完全不值"]
PKG = ["精致", "结实", "完整", "干净"]
NEG_PKG = ["挤压变形", "破损", "脏兮兮", "胶带没封好"]
ATT = ["专业耐心", "友好", "及时", "贴心"]
NEG_ATT = ["冷漠", "不耐烦", "推卸责任", "不专业"]
VERB = ["运行", "工作", "使用"]
NEG_VERB = ["坏掉", "出故障", "停转", "断裂"]
ASPECT = ["做工", "外观", "续航", "声音", "手感"]
NEG_ASPECT = ["颜色不对", "尺寸不符", "做工粗糙", "材质廉价"]


def _gen_text(sentiment: str, product: str) -> str:
    pool = {
        "正向": POSITIVE_TEMPLATES,
        "中性": NEUTRAL_TEMPLATES,
        "负向": NEGATIVE_TEMPLATES,
    }[sentiment]
    tmpl = random.choice(pool)
    return tmpl.format(
        prod=product,
        adj=random.choice(POS_ADJ if sentiment == "正向" else NEG_ADJ if sentiment == "负向" else POS_ADJ + NEG_ADJ),
        pkg=random.choice(PKG),
        neg_pkg=random.choice(NEG_PKG),
        att=random.choice(ATT),
        neg_att=random.choice(NEG_ATT),
        verb=random.choice(VERB),
        neg_verb=random.choice(NEG_VERB),
        aspect=random.choice(ASPECT),
        neg_aspect=random.choice(NEG_ASPECT),
    )


def _gen_customer_name() -> str:
    surnames = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
    names = ["小明", "晓雯", "建国", "梅", "强", "伟", "敏", "静", "丽", "勇", "杰", "娟", "涛", "艳", "超", "霞", "斌"]
    return random.choice(surnames) + random.choice(names)


def _gen_customer_type_for_sentiment(s: str) -> str:
    # VIP 略偏向正向，新客户略偏向负向（数据集里有点真实倾向）
    if s == "正向":
        return random.choices(CUSTOMER_TYPES, weights=[3, 3, 2, 1])[0]
    if s == "负向":
        return random.choices(CUSTOMER_TYPES, weights=[1, 2, 3, 2])[0]
    return random.choice(CUSTOMER_TYPES)


def _gen_handle_minutes(sentiment: str, channel: str, dt: datetime) -> int:
    base = {"正向": 18, "中性": 35, "负向": 75}[sentiment]
    # 夜间反馈处理慢（agent 可以发现的模式）
    if dt.hour < 6 or dt.hour >= 22:
        base = int(base * 1.6)
    # 微信小程序处理较快
    if channel == "微信小程序":
        base = int(base * 0.7)
    # 客服电话最慢
    if channel == "客服电话":
        base = int(base * 1.4)
    return max(2, int(random.gauss(base, base * 0.35)))


def _gen_satisfaction(sentiment: str) -> int:
    return {
        "正向": random.choices([5, 4, 3], weights=[6, 3, 1])[0],
        "中性": random.choices([4, 3, 2], weights=[2, 5, 3])[0],
        "负向": random.choices([2, 1], weights=[4, 6])[0],
    }[sentiment]


def _gen_amount(channel: str, customer_type: str) -> float:
    # 线上商城金额略高；VIP 客单价更高
    base = {
        "线上商城": 480,
        "线下门店": 320,
        "客服电话": 280,
        "微信小程序": 220,
        "天猫旗舰店": 410,
    }[channel]
    if customer_type == "VIP":
        base *= 1.6
    elif customer_type == "新客户":
        base *= 0.7
    return round(max(10, random.gauss(base, base * 0.45)), 2)


def _gen_product(category: str) -> str:
    PRODUCTS = {
        "家居家纺": ["四件套", "毛巾", "枕头", "羽绒被", "地毯", "蚊帐"],
        "厨房用具": ["不粘锅", "刀具组", "电饭煲", "保鲜盒", "玻璃杯"],
        "智能家电": ["扫地机器人", "空气净化器", "智能音箱", "蓝牙耳机", "电动牙刷"],
        "美妆个护": ["精华液", "口红", "面膜", "防晒霜", "洗发水"],
        "母婴用品": ["奶粉", "纸尿裤", "婴儿车", "学步鞋", "辅食机"],
        "运动户外": ["跑鞋", "瑜伽垫", "登山杖", "户外帐篷", "运动手环"],
    }
    return random.choice(PRODUCTS[category])


# ----------------------------------------------------------------------
def main() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户反馈"

    headers = [
        "反馈ID",
        "提交时间",
        "客户ID",
        "客户姓名",
        "客户类型",
        "产品类别",
        "购买产品",
        "渠道",
        "满意度评分",
        "处理时长_分钟",
        "是否解决",
        "是否VIP",
        "业务额_元",
        "标签",
        "反馈内容",
    ]
    ws.append(headers)
    fill = PatternFill("solid", fgColor="5B6CFF")
    font = Font(bold=True, color="FFFFFF")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")

    end = datetime(2026, 5, 1)
    start = end - timedelta(days=120)

    for i in range(N_ROWS):
        sentiment = random.choices(["正向", "中性", "负向"], weights=[6, 2, 2])[0]
        category = random.choice(PRODUCT_CATEGORIES)
        product = _gen_product(category)
        channel = random.choice(CHANNELS)
        cust_type = _gen_customer_type_for_sentiment(sentiment)
        dt = start + timedelta(
            days=random.randint(0, 119),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )
        sat = _gen_satisfaction(sentiment)
        handle_min = _gen_handle_minutes(sentiment, channel, dt)
        amount = _gen_amount(channel, cust_type)
        resolved = "是" if sentiment != "负向" or random.random() < 0.4 else "否"
        is_vip = "TRUE" if cust_type == "VIP" else "FALSE"

        # 0–3 标签
        n_tags = random.choices([0, 1, 2, 3], weights=[1, 4, 4, 2])[0]
        tag_pool = (
            ["性价比高", "推荐购买", "功能正常"] if sentiment == "正向"
            else ["质量问题", "物流慢", "客服态度", "包装破损", "价格贵"] if sentiment == "负向"
            else TAGS
        )
        tags = "、".join(random.sample(tag_pool, min(n_tags, len(tag_pool))))

        row = [
            f"FB-{2026000 + i:07d}",
            dt.strftime("%Y-%m-%d %H:%M:%S"),
            f"C-{random.randint(10000, 99999)}",
            _gen_customer_name(),
            cust_type,
            category,
            product,
            channel,
            sat,
            handle_min,
            resolved,
            is_vip,
            amount,
            tags,
            _gen_text(sentiment, product),
        ]
        ws.append(row)

    # column widths for readability
    widths = [12, 20, 12, 10, 10, 12, 12, 14, 10, 12, 8, 8, 12, 28, 60]
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    wb.save(OUT)
    print(f"已生成测试数据：{OUT}")
    print(f"  · 共 {N_ROWS} 行 × {len(headers)} 列")
    print(f"  · 时间范围：{start.date()} ~ {end.date()}")
    print(f"  · 涵盖：客户类型 / 渠道 / 产品类别 / 评分 / 时长 / 金额 / 中文反馈文本")


if __name__ == "__main__":
    main()
