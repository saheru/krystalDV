# Windows 打包说明

> 给最终客户：你**不需要看这份文档**。直接双击 `kdv.exe` 即可。

## 给开发者：构建分发包

### 一键构建（推荐）

在 Windows 上，双击运行：

```
build\build_windows.bat
```

它会自动：
1. 创建虚拟环境（首次）
2. 安装依赖
3. 跑单元测试
4. 用 PyInstaller 打包
5. 把 `dist\kdv\` 压缩成 `dist\kdv.zip`

完成后两个产物：
- `dist\kdv\kdv.exe` —— 直接双击运行
- `dist\kdv.zip` —— 发给最终用户

### 手动构建

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pyinstaller build\kdv.spec --clean --noconfirm
```

## 给最终用户的使用说明

1. 解压 `kdv.zip` 到任意目录（建议放在 `C:\Program Files\KrystalDataVision\` 或桌面）
2. 双击 `kdv.exe` 启动
3. 第一次启动后：
   - 在『LLM 配置』页面填入你的 Base URL 和 API Key
   - 在『分析模型』页面上传输出模板 Excel（含字段定义或示例数据）
   - 在『运行分析』页面上传数据 Excel，点击"开始分析"
   - 完成后自动跳转到『结果可视化』页面查看图表与洞察

**无需 Python、无需终端、无需服务器。** 整个程序就是一个独立 exe + 资源文件夹。

## 程序数据存放位置

- 配置文件：`%APPDATA%\krystaldatavision\`
  - `presets.json` — LLM 配置
  - `models.json` — 分析模型模板
  - `settings.json` — 界面偏好
- API key：Windows 凭据管理器（系统加密）
- 缓存：`%LOCALAPPDATA%\krystaldatavision\Cache\runs.sqlite`

卸载只需删除 `dist\kdv\` 文件夹和 `%APPDATA%\krystaldatavision\` 即可。

## 常见构建问题

- **`matplotlib` 字体报错**：spec 已 `collect_data_files("matplotlib")`，应自动包含
  `mpl-data`。如还有问题，确认你 pip 装的是 `matplotlib >= 3.8`。
- **exe 体积太大**：spec 已排除 QtWebEngine / Qt3D / QtCharts 等。如还需精简，
  在 `excludes` 中追加你不需要的模块。
- **第一次启动慢**：onefolder 模式首次解压 PyInstaller bootloader。属正常现象，
  之后启动速度恢复正常。我们有意没用 onefile 模式（onefile 每次启动都要解压）。
- **缺图标**：把 `assets\icons\kdv.ico` 放好；或在 spec 里删掉 `icon=` 参数。
