# Krystal Data Vision

Windows 平台基于 LLM 的 Excel 数据分析工具。

> **给最终用户**：解压 `kdv.zip` → 双击 `kdv.exe`。无需安装 Python、无需终端、无需服务器。

## 功能亮点

- **OpenAI 兼容 API**：支持 OpenAI / DeepSeek / 智谱 / Moonshot / Azure 等所有走 `/v1/chat/completions` 的服务
- **Excel 驱动分析模型**：上传输出模板 Excel（含示例数据，或带『字段定义』sheet）即可一键生成分析模型
- **两种分析模式**：逐行批量结构化分析 + 整表汇总洞察，可同时启用
- **健壮的运行**：asyncio 并发 · 失败重试 · SQLite 结果缓存（断点恢复）
- **多预设管理**：保存多个 LLM 服务和多个分析模型模板
- **多图表 + 多分析工具可视化**：柱/折/面积/散点/直方/箱线/饼/环/雷达/相关性热力/词云/树状图/透视表/统计摘要 等
- **现代界面**：扁平卡片设计 · 流畅过渡动画 · API key 用 Windows 凭据管理器加密保存

## 给最终用户的使用流程

1. 解压 `kdv.zip` 到任意目录
2. 双击 `kdv.exe` 启动
3. 在『LLM 配置』页面填入 Base URL 与 API Key，点"测试连接"
4. 在『分析模型』页面上传输出模板 Excel
5. 在『运行分析』页面上传数据 Excel，选择模式，点击"开始分析"
6. 自动跳转到『结果可视化』页面，并在源 Excel 旁生成 `_分析结果_*.xlsx`

## 开发与打包

### 本地开发运行

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
python main.py
```

### 运行测试

```bash
pytest
```

### 打包成 Windows 双击 exe（一键）

在 Windows 上：

```cmd
build\build_windows.bat
```

完成后：
- `dist\kdv\kdv.exe` —— 直接双击运行
- `dist\kdv.zip` —— 发给最终用户

详见 [`build/README_BUILD.md`](build/README_BUILD.md)。

## 项目结构

```
krystaldatavision/
├── main.py                  入口
├── pyproject.toml           依赖
├── src/kdv/
│   ├── config/              配置（预设、API key）
│   ├── llm/                 OpenAI 兼容客户端 + 结构化输出
│   ├── excel/               读 / 写 / 模板解析 / 类型推断
│   ├── analysis/            编排（runner / cache / 并发）
│   ├── viz/                 多种图表 + 列统计 + 推荐器
│   └── ui/                  PySide6 界面（4 页 + 全局 QSS + 动效）
├── tests/                   单元测试
└── build/                   PyInstaller spec + 一键构建脚本
```

## 程序数据存放位置（Windows）

- `%APPDATA%\krystaldatavision\` — 配置 / 模型 / 偏好
- Windows 凭据管理器 — API key（系统加密）
- `%LOCALAPPDATA%\krystaldatavision\Cache\` — 运行缓存（可断点恢复）

卸载：删除 `dist\kdv\` 与 `%APPDATA%\krystaldatavision\` 即可。
