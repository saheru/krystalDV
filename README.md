# Krystal Data Vision

Windows 平台基于 LLM 的 Excel 数据分析工具。

> **给最终用户**：解压 `kdv.zip` → 双击 `kdv.exe`。无需安装 Python、无需终端、无需服务器。

## 技术栈

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 运行时 | Python 3.11+ | 主语言 |
| GUI | [PySide6](https://doc.qt.io/qtforpython-6/) (Qt 6) | 原生 Windows 桌面界面 |
| 异步桥接 | [qasync](https://github.com/CabbageDevelopment/qasync) | asyncio 与 Qt 事件循环协同 |
| HTTP 客户端 | [httpx](https://www.python-httpx.org/) | 异步调用 OpenAI 兼容接口 |
| 重试 | [tenacity](https://tenacity.readthedocs.io/) | 指数退避，处理 429 / 5xx |
| 数据校验 | [pydantic v2](https://docs.pydantic.dev/) | 配置 / 模型 / 输出结构化校验 |
| Excel I/O | [openpyxl](https://openpyxl.readthedocs.io/) | 读写 .xlsx，含样式与多 sheet |
| 数据计算 | [numpy](https://numpy.org/) + [pandas](https://pandas.pydata.org/) | 列统计、相关性矩阵、时序聚合 |
| 交互图表 | [PyQtGraph](https://www.pyqtgraph.org/) | 柱/折/面积/散点/直方/箱线图（高性能） |
| 静态图表 | [Matplotlib](https://matplotlib.org/) | 饼/环/雷达/相关性热力/树状/词云 |
| 词云（可选） | [wordcloud](https://github.com/amueller/word_cloud) | 文本字段词云（缺失时回退为标签云） |
| Markdown 渲染 | [markdown](https://python-markdown.github.io/) | 整表汇总洞察渲染 |
| 凭据存储 | [keyring](https://github.com/jaraco/keyring) | API key 存到 Windows 凭据管理器 |
| 路径定位 | [platformdirs](https://github.com/platformdirs/platformdirs) | 跨平台 AppData / Cache 路径 |
| 系统主题 | [darkdetect](https://github.com/albertosottile/darkdetect) | 检测系统浅深色（保留扩展位） |
| 图标字体 | [QtAwesome](https://github.com/spyder-ide/qtawesome) | 矢量图标 |
| 测试 | pytest + pytest-asyncio | 单元测试 |
| 打包 | [PyInstaller](https://pyinstaller.org/) (onefolder, `--windowed`) | 打成无终端、双击启动的 Windows 可执行 |

**架构特点**：

- 全异步：UI 主线程跑 Qt 事件循环，LLM 调用/Excel 读写都走 asyncio；并发数可配。
- 单文件分发：onefolder 模式产出 `dist/kdv/` 整个目录，最终用户解压即用，**无需安装 Python**。
- 数据零外发：所有 Excel 处理与缓存都在用户本地完成，仅 LLM 请求体走出网络。

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

### 打包

#### 自动构建（推荐）—— GitHub Actions

仓库已配置好 `.github/workflows/`：

| 工作流 | 触发 | 产物 |
| --- | --- | --- |
| `build-windows.yml` | push 到 main / 打 `v*` tag / 手动触发 | `kdv-windows.zip` |
| `build-mac.yml` | push 到 main / 打 `v*` tag / 手动触发 | `kdv-mac.zip` (Apple Silicon) |
| `claude-review.yml` | 每个 PR | Claude 自动评审 + 评论 |
| `claude.yml` | 评论 / issue 里 `@claude` | Claude 答问题 / 改代码 |
| `claude-test-triage.yml` | 构建失败时 | Claude 分析日志 + 给出修复建议 |

### Claude 自动 review / 修复（可选）

要启用 `claude-*.yml` 三个工作流，给仓库加一个 secret：

1. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Name = `ANTHROPIC_API_KEY`，Value = 你在 [console.anthropic.com](https://console.anthropic.com) 创建的 API key
3. 之后：
   - 提 PR 时 Claude 会自动评审改动并发评论
   - 在 PR/issue 评论里 `@claude 帮我把 X 改成 Y` 它就会动手改并提交
   - CI 构建失败时 Claude 会自动分析失败日志并发评论给出修复建议

按 token 计费；想省钱把 workflow 里的 `model: claude-opus-4-7` 换成 `claude-haiku-4-5-20251001`。

每次 push 后到 GitHub 仓库的 **Actions 标签页**，点对应 workflow 的最新一次运行 → 滚到底部 **Artifacts** 区下载 zip。打 `v*` 标签（如 `git tag v0.1.0 && git push --tags`）会自动创建带 zip 附件的 GitHub Release。

#### 本地构建

**Windows**（必须在 Windows 机器上跑，PyInstaller 不支持交叉编译）：

```cmd
build\build_windows.bat
```

**macOS**：

```bash
bash build/build_mac.sh
```

完成后：
- Windows: `dist\kdv\kdv.exe` + `dist\kdv.zip`
- macOS: `dist/kdv.app` + `dist/kdv-mac.zip`

详见 [`build/README_BUILD.md`](build/README_BUILD.md)。

### Mac 用户首次启动提示

未签名的 .app 在别人 Mac 上首次打开会被 Gatekeeper 拦截。两种解法：

- 终端执行（推荐，一次性）：`xattr -dr com.apple.quarantine /Applications/kdv.app`
- 或：右键 .app → **打开** → 弹窗里点"打开"，之后双击可正常启动

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
