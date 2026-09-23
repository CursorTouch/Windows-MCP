# Windows-MCP 功能完善程度分析

> 代码核对基准：当前工作树 `0.8.5`，核对日期为 2026-09-23。本文的“真实工具名、注册模块、默认参数、依赖约束”均以仓库源码和 `pyproject.toml` 为准；影刀分类数量与同类 MCP 的 star 数属于外部对照数据，已单独标注来源。
>
> 重要说明：`src/windows_mcp/tools/snapshot.py` 的装饰器实际注册的是 `Snapshot`、`Screenshot`，其中的 `tool_name="Snapshot tool"` / `tool_name="Screenshot tool"` 只是分析埋点名称，不是 MCP 工具名。

## 1. 分析坐标

本文使用三套互补维度，避免把“有某个底层 API”误判成“已经有完整自动化能力”：

1. **Windows API 面**：看输入、UIA、窗口、剪贴板、显示、系统会话等底层域是否被真实调用。
2. **影刀指令库面**：以影刀帮助中心外部资料中的 2569 篇、20 个一级分类为需求坐标，逐类找本项目可替代能力。该数量不在本仓库中，无法用代码复核。
3. **同类 MCP 横切对照**：比较同类项目的工具密度、底层方案和差异化能力，重点看 OCR、模板匹配、批量操作、自愈定位、服务管理等。

## 2. 仓库真实能力

### 2.1 注册与数量

`src/windows_mcp/tools/__init__.py` 的真实模块清单：

- `_CORE_MODULES`：`app`、`display`、`shell`、`filesystem`、`snapshot`、`input`、`scrape`、`multi`、`clipboard`、`process`、`notification`、`registry`，共 **12 个核心模块、20 个工具名**。
- `_OPTIONAL_MODULES`：`ocr`、`web`、`web_agent`、`act`、`inputx`、`systemx`、`net`、`office`，共 **8 个可选模块、22 个工具名**。
- 因此源码中实际共有 **42 个工具名**。若某个可选模块导入失败，`_load()` 捕获异常并记录 warning，只跳过该模块；核心模块导入失败则原样抛出异常。
- 注意：`_OPTIONAL_MODULES` 是 8 个模块名，不是“7 个工具包”的直观计数。OCR、Office 的第三方文档/OCR 依赖是延迟导入，因此缺包时模块可能仍能注册，调用时才报依赖错误；`web` 和依赖它的 `web_agent` 会在导入 Playwright 失败时直接被跳过。

### 2.2 实际工具清单

| # | 工具包/模块 | 工具名 | 一句话职责 |
|---:|---|---|---|
| 1 | `tools/app.py` | `App` | 启动应用或指定可执行文件，并调整/切换窗口。 |
| 2 | `tools/display.py` | `DisplayInventory` | 读取显示器、工作区、分辨率、方向和 DPI 元数据。 |
| 3 | `tools/shell.py` | `PowerShell` | 执行 PowerShell 命令，是通用系统脚本兜底入口。 |
| 4 | `tools/filesystem.py` | `FileSystem` | 读写、复制、移动、删除、列目录、搜索和查看文件信息。 |
| 5 | `tools/snapshot.py` | `Snapshot` | 捕获桌面、窗口、UIA 元素、DOM/截图和可用显示器状态。 |
| 6 | `tools/snapshot.py` | `Screenshot` | 快速截图优先路径，返回光标、窗口摘要和图片。 |
| 7 | `tools/input.py` | `Click` | 按标签或坐标点击，可指定按钮、点击次数和位置。 |
| 8 | `tools/input.py` | `Type` | 按标签或坐标输入文本，支持清空、追加和回车。 |
| 9 | `tools/input.py` | `Scroll` | 按方向滚动页面或控件。 |
| 10 | `tools/input.py` | `Move` | 移动鼠标到标签/坐标。 |
| 11 | `tools/input.py` | `Shortcut` | 发送组合键快捷键。 |
| 12 | `tools/input.py` | `Wait` | 固定等待。 |
| 13 | `tools/input.py` | `WaitFor` | 等待文本、活动窗口、元素存在/启用或焦点满足。 |
| 14 | `tools/scrape.py` | `Scrape` | HTTP 抓取或从当前浏览器 DOM 提取网页内容。 |
| 15 | `tools/multi.py` | `MultiSelect` | 一次选择多个元素或坐标。 |
| 16 | `tools/multi.py` | `MultiEdit` | 一次编辑多个元素或坐标。 |
| 17 | `tools/clipboard.py` | `Clipboard` | 读取/设置 Unicode 文本剪贴板。 |
| 18 | `tools/process.py` | `Process` | 列出、筛选和终止进程。 |
| 19 | `tools/notification.py` | `Notification` | 通过 Windows Toast 发送通知。 |
| 20 | `tools/registry.py` | `Registry` | 注册表 get/set/delete/list。 |
| 21 | `tools/ocr.py` | `Ocr` | 在屏幕、区域、文件或 UIA 元素上执行本地 OCR，返回文本和坐标框。 |
| 22 | `tools/web.py` | `Web` | Playwright/CDP 浏览器自动化，含连接、导航、快照、动作、等待、截图、提取、JS、Cookie 和网络事件。 |
| 23 | `tools/web_agent.py` | `WebAgent` | 给定 URL 和目标，执行观察-决策-动作-校验循环。 |
| 24 | `tools/act.py` | `Act` | 一次调用完成动作、目标解析、等待、校验和有限重试。 |
| 25 | `tools/inputx.py` | `KeyDown` | 按下并保持一个键。 |
| 26 | `tools/inputx.py` | `KeyUp` | 释放一个键。 |
| 27 | `tools/inputx.py` | `Press` | 按键并自动释放。 |
| 28 | `tools/inputx.py` | `Hotkey` | 按顺序按下组合键并逆序释放。 |
| 29 | `tools/inputx.py` | `Hold` | 按住指定时长后释放。 |
| 30 | `tools/inputx.py` | `ReleaseKeys` | 释放服务追踪的全部按键，防止卡键。 |
| 31 | `tools/inputx.py` | `TypeHuman` | 带节奏抖动的文本输入。 |
| 32 | `tools/inputx.py` | `MoveHuman` | 使用曲线、缓动和可选过冲的鼠标移动。 |
| 33 | `tools/inputx.py` | `ClickHuman` | 拟人化移动后点击。 |
| 34 | `tools/inputx.py` | `DragHuman` | 拟人化移动后拖拽。 |
| 35 | `tools/systemx.py` | `WindowControl` | 列出、关闭、显示、隐藏、最小化、最大化、恢复和置顶窗口。 |
| 36 | `tools/systemx.py` | `ClipboardAdvanced` | 文本、图片、文件列表、HTML、清空和格式枚举。 |
| 37 | `tools/systemx.py` | `Archive` | 创建、列出、解压 zip/tar/tar.gz。 |
| 38 | `tools/systemx.py` | `SystemControl` | 锁屏、音量、静音、亮度、输入语言、屏保和电源会话控制。 |
| 39 | `tools/systemx.py` | `SystemProcess` | 启动进程、设置优先级、挂起/恢复、列出并启停服务。 |
| 40 | `tools/systemx.py` | `SystemDialog` | 显示消息、输入、选择、文件、目录和日期对话框。 |
| 41 | `tools/net.py` | `Net` | HTTP、断点下载、SMTP、IMAP、FTP/FTPS、企业聊天 webhook。 |
| 42 | `tools/office.py` | `Office` | Excel、Word、PDF、CSV 和 SQLite 文件操作。 |

### 2.3 底层资源与 Windows API

以下结论是对 `src/windows_mcp/` 的静态检索结果，不把 `uv.lock` 或第三方包内部实现算作本项目调用。

| API/资源域 | 代码中的真实使用 | 结论 |
|---|---|---|
| 输入注入 | `uia/core.py` 定义 `SendInput`；`inputx/service.py` 直接调用 Win32 `SendInput`。 | ✅ 使用 |
| 旧式输入 API | `uia/core.py` 仍定义并调用 `mouse_event`、`keybd_event`；`inputx` 的拟人化路径使用 `SendInput`。 | ✅ 使用 |
| 光标坐标 | `SetCursorPos`、`mouse_event`、UIA 控件中心点。 | ✅ 使用 |
| 多显示器 | `EnumDisplayMonitors`、`EnumDisplayDevicesW`、`GetMonitorInfoW`、`GetVirtualScreenRect`；`DisplayInventory` 暴露结果。 | ✅ 使用 |
| 窗口管理 | `EnumWindows`、`SetForegroundWindow`、`MoveWindow`、`win32gui.ShowWindow`、`SetWindowPos`、`WM_CLOSE`。 | ✅ 使用 |
| UIA | `comtypes`、`IUIAutomationElement`、`ControlFromPoint`、UIA tree/walker。 | ✅ 使用 |
| 剪贴板 | 基础 `Clipboard` 和高级 `ClipboardAdvanced` 都调用 `win32clipboard`。 | ✅ 使用 |
| DPI | `SetProcessDpiAwareness(PerMonitorDpiAware)`、`GetDpiForSystem`、`GetDpiForMonitor`；显示器项含 `effective_dpi`/`scale`。 | ✅ 使用 |
| 截图 | `dxcam`、`mss`、`PIL.ImageGrab`，按可用性回退。 | ✅ 使用 |
| 注册表 | `winreg` 在环境解析和虚拟桌面名称读取中使用；`Registry` 工具本身通过 PowerShell 执行。 | ✅ 使用 |
| COM Shell | `shell32.ShellExecuteW` 存在；没有通用 COM Shell/Office 自动化封装。 | ⚠️ 部分 |
| 进程 | `psutil`、`OpenProcess`、优先级、挂起/恢复、服务 API；核心 `Process` 仍主要是 list/kill。 | ✅ 使用 |
| 虚拟桌面 | `IVirtualDesktopManager` 及内部 COM 接口用于读取当前/全部桌面；没有面向 MCP 的创建、切换或移动窗口工具。 | ⚠️ 部分 |
| 通知 | PowerShell 调用 `Windows.UI.Notifications.ToastNotificationManager`。 | ✅ 使用 |
| 电源/会话 | `ExitWindowsEx`、`SetSuspendState`、关机权限和 `shutdown/restart/logoff/suspend/hibernate/abort`。 | ✅ 使用 |
| 音量/亮度 | 音量用 PowerShell 内联 Core Audio；亮度用 WMI `WmiMonitorBrightness` / `WmiMonitorBrightnessMethods`。 | ✅ 使用 |
| 压缩解压 | 标准库 `zipfile`、`tarfile`、`shutil`。 | ✅ 使用 |
| OCR | RapidOCR/ONNX Runtime 为主路径，`pytesseract` 和 `Windows.Media.Ocr` 为可选降级。 | ✅ 使用（依赖可选） |
| 服务/计划任务 | `psutil.win_service_iter()` 可列/启停服务；计划任务只用于本项目自身安装/卸载。 | ⚠️ 部分 |
| CDP/浏览器 | `playwright.sync_api`，默认 CDP `http://127.0.0.1:9222`。 | ✅ 使用 |
## 3. 矩阵一：对照 Windows API

状态定义：✅ 已形成工具路径；⚠️ 只有局部/只读/间接路径；❌ 未形成稳定自动化能力。

| Windows API 域 | 覆盖状态 | 依据 |
|---|---|---|
| 输入注入 | ✅ | `SendInput`、`mouse_event`、`keybd_event`；鼠标、键盘和文本工具均可达。 |
| 光标坐标 | ✅ | `SetCursorPos` 与坐标/标签解析；支持绝对坐标和 UIA 元素。 |
| 多显示器 | ✅ | 显示器枚举、虚拟桌面矩形、显示索引、DPI/缩放和截图 display 参数。 |
| 窗口管理 | ✅ | 基础 App 窗口切换/调整，加上 `WindowControl` 的关闭、显隐、最大化和置顶。 |
| UIA | ✅ | 完整 UIA 树、控件属性、焦点、元素动作和标签坐标解析。 |
| 剪贴板 | ✅ | 文本、图片、文件、HTML、清空和格式枚举，不再局限于文本。 |
| DPI | ✅ | 进程/线程 DPI awareness、每显示器 DPI、`DisplayInventory` 输出。 |
| 截图 | ✅ | dxcam → mss → Pillow 回退，支持区域/显示器/标注/缩放。 |
| 注册表 | ✅ | `Registry` 的 get/set/delete/list，底层由 PowerShell 而非直接 `winreg` 写命令。 |
| COM Shell | ⚠️ | 有 `ShellExecuteW` 和虚拟桌面 COM，但没有通用 Shell COM 对象、文件关联或 Office COM 抽象。 |
| 进程 | ✅ | 核心 list/kill 之外，`SystemProcess` 增加 start、priority、suspend、resume、service list/start/stop。 |
| 虚拟桌面 | ⚠️ | `Snapshot` 可读取当前和全部桌面；缺少创建、删除、重命名、切换、跨桌面移动 MCP 工具。 |
| 通知 | ✅ | Toast 通知；依赖 Windows PowerShell 5.1 的 WinRT 能力。 |
| 电源/会话 | ✅ | 锁屏、屏保、关机、重启、注销、睡眠、休眠、取消关机。 |
| 音量/亮度 | ✅ | 音量/静音和显示器亮度；有些设备可能不提供 WMI 亮度方法。 |
| 压缩解压 | ✅ | zip/tar/tar.gz，含路径穿越和链接防护。 |
| OCR | ✅ | 屏幕/区域/文件/元素四种模式，返回行/词坐标；需安装 OCR extra，运行时依赖硬件和模型。 |
| 服务/计划任务 | ⚠️ | Windows 服务可列出和启停；通用计划任务创建/查询/运行不是 MCP 工具。 |
| CDP | ✅ | 连接已有 Chrome、启动 Playwright Chromium、标签页状态、DOM 快照和浏览器动作。 |

## 4. 矩阵二：对照影刀指令库

影刀侧基线来自题目给出的外部资料：2569 篇、20 个一级分类。下表是能力映射，不是对影刀实现细节的代码级复现。覆盖率按“高/中/低/无”做定性判断。

| 影刀大类 | 本项目对应工具 | 覆盖评估 | 说明 |
|---|---|---|---|
| 魔法指令 | `PowerShell`、`WebAgent` | 低 | 可执行脚本或目标驱动网页任务，但没有影刀式自然语言指令工程和持久流程资产。 |
| 条件判断 | `WaitFor`、`Act` 的 `verify` | 低 | 单次动作条件校验存在，通用 if/else 图形流程不存在。 |
| 循环 | 无专门工具；`PowerShell` 可脚本化 | 低 | 没有循环引擎、循环变量和批量流程编排。 |
| 等待 | `Wait`、`WaitFor`、`Web(mode=wait)` | 高 | 固定等待、文本/窗口/元素等待、Web selector/text/url/networkidle 等待。 |
| 相似元素操作 | `MultiSelect`、`MultiEdit`、UIA 树、`Web` CSS `extract` | 中 | 可批量处理多个元素，但没有影刀式相似元素分组和稳定选择器模型。 |
| 网页自动化 | `Web`、`WebAgent`、`Scrape`、`Snapshot(use_dom=True)` | 高 | 含 CDP、索引 ref、JS、Cookie、网络捕获、等待、截图和提取。 |
| 桌面软件自动化 | `Snapshot`、`Click`、`Type`、`Act`、`Ocr` | 高 | UIA 优先，OCR 可补 canvas/自绘界面；无模板匹配和完整自愈选择器。 |
| 鼠标键盘 | `Click/Move/Scroll`、`Shortcut/KeyDown/KeyUp/Press/Hotkey/Hold/ReleaseKeys`、Human 系列 | 高 | 覆盖普通、长按、组合键和拟人化路径。 |
| 数据表格 | `Office`、`CSV`、`SQLite`、`PowerShell` | 中 | 支持文件级数据表，不是可视化数据表格/影刀表格对象。 |
| Excel-WPS表格 | `Office` | 中 | `openpyxl` 支持 `.xlsx/.xlsm`；无 WPS COM、`.xls`、宏执行或 Excel 进程级自动化。 |
| 对话框 | `SystemDialog`、`Snapshot`、`Act`、`WindowControl` | 高 | 原生消息/输入/选择/文件/文件夹/日期对话框，且可继续用 UIA 处理应用对话框。 |
| 数据处理 | `Office`、`Net`、`PowerShell` | 中 | CSV/Excel/JSON/SQLite/HTTP 基础处理可用，缺少 DataFrame、复杂 ETL 和连接器市场。 |
| 操作系统 | `SystemControl`、`SystemProcess`、`Archive`、`FileSystem`、`Registry`、`Notification`、`ClipboardAdvanced` | 高 | 文件、进程、服务、注册表、电源、音量、压缩和通知覆盖较广。 |
| 流程-应用 | `App`、`PowerShell`、项目自身的计划任务命令 | 低 | 没有图形化流程设计器、版本化流程、应用市场或执行队列。 |
| 人工智能AI | `WebAgent`、`Scrape` 的 sampling、`Ocr` | 中 | 有可插拔策略和网页 agent；本地模板、分类、NLP 大模型工具不完整。 |
| 网络 | `Net`、`Scrape`、`Web(mode=network)` | 高 | HTTP、下载、SMTP、IMAP、FTP、企业微信/钉钉/飞书 webhook，以及 XHR/fetch 捕获。 |
| 工作队列 | 无直接工具 | 无 | 没有队列、生产者消费者、任务领取、重试/死信和持久状态。 |
| 其他 | `Notification`、`ClipboardAdvanced`、`Archive`、`Ocr` | 中 | 有若干系统补充能力，但没有完整连接器目录。 |
| 手机操作自动化 | 无 | 无 | 只面向 Windows 桌面和浏览器。 |
| 自定义指令 | `register_policy`、模块式工具注册、`PowerShell` | 低 | 能扩展 policy 和 Python 工具模块，但没有影刀式低代码自定义指令包。 |

### 4.1 代表性细项

| 能力 | 影刀侧 | 本项目当前代码事实 |
|---|---|---|
| OCR | 有 | 已补齐 `Ocr`；不再是“无 OCR”。 |
| 图像模板匹配 | 有 | 仍无；截图和 OCR 不能替代模板匹配的像素级相似度定位。 |
| Cookie 管理 | 有 | 已补齐 `Web(mode=cookies, action=get/set/clear)`。 |
| 执行 JS | 有 | 已补齐 `Web(mode=eval)`。 |
| 网络监听 | 有 | 已补齐 `Web(mode=network)`，只捕获 XHR/fetch 的 request/response 摘要。 |
| 压缩解压 | 有 | 已补齐 `Archive`，支持 zip/tar/tar.gz。 |
| 锁屏 | 有 | 已补齐 `SystemControl(mode=lock)`。 |
| 输入法 | 有 | 仅有键盘布局枚举/切换；没有 IME 组合、候选词、拼音输入等控制。 |
| 屏保 | 有 | 已补齐 `SystemControl(mode=screensaver)`。 |
| 工作队列 | 有 | 仍无。 |
| 动作+校验+重试 | 通常由流程引擎编排 | `Act` 在单次工具调用内做动作、条件校验和有限重试；这是本项目侧差异化能力。 |
| 容错工具包注册 | 不是“本项目当前能力”的对照项 | 可选模块导入失败只跳过该模块并 warning，不拖垮服务器。 |
| OAuth / IP 白名单 | 不是“本项目当前能力”的对照项 | HTTP transport 有 OAuth 2.0 + PKCE、静态 auth key、IP/CIDR allowlist、TrustedHost。 |
## 5. 横向：同类 MCP 对照

star 数的来源：GitHub REST API `GET /repos/{owner}/{repo}`，抓取时间为 2026-09-23；star 会持续变化。工具数优先采用仓库 README 的明确声明；没有固定声明的项目记为“变量”，不把“包装了某个库的全部函数”硬算成固定值。

| 项目 | Star | 语言/底层 | 工具数 | 相对本项目的差异化能力 |
|---|---:|---|---:|---|
| [shanselman/FlaUI-MCP](https://github.com/shanselman/FlaUI-MCP) | 100 | C# / FlaUI + Windows UIA | 12（README 工具表） | 元素 ref 快照、批量动作 `windows_batch`、专门的 30 秒工具超时。 |
| [dddabtc/winremote-mcp](https://github.com/dddabtc/winremote-mcp) | 199 | Python / FastMCP + Windows API | 40+（仓库简介） | OCR、录屏 GIF、带标签截图、注册表/服务/计划任务/网络诊断，偏远程运维。 |
| [sandraschi/windows-computer-use-mcp](https://github.com/sandraschi/windows-computer-use-mcp) | 40 | Python / UIA + 视觉 | 22（README） | OCR、模板匹配、自动降级定位、宏录制、事件 watcher、SQLite 遥测。 |
| [mario-andreschak/mcp-windows-desktop-automation](https://github.com/mario-andreschak/mcp-windows-desktop-automation) | 118 | TypeScript / AutoIt | 变量（AutoIt 全函数包装） | 直接暴露 AutoIt 鼠标、键盘、窗口、进程和截图资源。 |
| [civyk-official/civyk-winwright](https://github.com/civyk-official/civyk-winwright) | 19 | PowerShell / UIA + CDP | 52（README） | 自愈 selector、语义快照/状态 diff、事件监听、服务/注册表/计划任务、桌面+浏览器混合。 |
| [shuyu-labs/Windows-MCP.Net](https://github.com/shuyu-labs/Windows-MCP.Net) | 229 | C# / Windows API + OCR | 41（README 工具表；源码约 38 个 `*Tool.cs`，部分为聚合工具） | 独立 OCR 工具、亮度/音量/分辨率工具、坐标 UI 查找工具。 |

工具数不是直接可比的胜负指标：本项目的 42 个工具中包含 `Wait`、`WaitFor`、多个 Human/Key 工具等细小 MCP 面；部分竞品使用少量“portmanteau”工具并在单个工具内通过 `action` 展开很多动作。

## 6. 缺口清单（按严重度排序）

表中把本轮已经从“原缺口”变成有实现的能力标为 ✅，避免把历史结论当成当前事实。

| # | 缺口/原缺口 | 严重度 | 能否用 PowerShell 兜底 | 当前依据 |
|---:|---|---|---|---|
| 1 | 无图像模板匹配、相似度定位、像素级锚点 | 高 | 否；需要图像算法/库或外接 CV 服务 | 仓库无模板匹配模块；截图 + OCR 只能做文字/区域辅助。 |
| 2 | 无工作队列、可靠任务领取、死信和跨步骤恢复 | 高 | 部分；可用 SQLite/HTTP/脚本临时模拟，但没有队列语义 | 没有 queue/worker 工具或持久队列模型。 |
| 3 | 无通用数据库连接器和 ETL；只有 SQLite | 高 | 部分；PowerShell/ODBC 可外部脚本化 | `Office` 只导入 stdlib `sqlite3`，无 SQLAlchemy/pyodbc/ODBC 连接器层。 |
| 4 | 无手机操作自动化 | 高（仅企业 RPA 坐标） | 否 | 无 Android/iOS、ADB 或移动端节点。 |
| 5 | 无通用计划任务创建/查询/运行 MCP 工具 | 中 | 是；`schtasks`/`Register-ScheduledTask` | 计划任务只用于项目自身安装/卸载；`SystemProcess` 只管 Windows 服务。 |
| 6 | 虚拟桌面只能读取，不能创建/切换/移动窗口 | 中 | 部分；通常要调用未公开 COM/Win32 | `IVirtualDesktopManager` 已存在，但 MCP 没有对应动作工具。 |
| 7 | 没有 IME 组合、候选词和拼音输入控制 | 中 | 部分；需额外 Win32/UI Automation 代码 | `SystemControl` 只有键盘布局枚举/切换。 |
| 8 | 旧格式 Office 空白：Excel 无 `.xls`/WPS，Word 无 `.doc` | 中 | 部分；有 Office 时可走 COM | `openpyxl` 只接受 `.xlsx/.xlsm`，`python-docx` 只接受 `.docx`。 |
| 9 | 邮件只支持用户名/密码，不支持 OAuth2 | 中 | 部分；需自行实现 token 流程 | `smtplib`/`imaplib` 路径使用密码登录，无 OAuth2 流程。 |
| 10 | `Act` 之外的工具并非统一“动作+校验+重试” | 中 | 部分；调用方自行重试 | 可靠性工具和 `Act` 存在，但没有强制包住所有 MCP 动作。 |
| 11 | Notepad3/Scintilla 等自绘编辑器不暴露可读 UIA 文本 | 中 | 部分；可剪贴板/快捷键兜底，但依赖焦点 | 仓库未找到针对这些编辑器的专用适配；此限制未能在当前代码中确认。 |
| 12 | 无原生图像/录音/视频理解，仅有 OCR 和截图 | 中 | 否 | 没有模型侧视觉理解封装；`Ocr` 只识别文字。 |
| 13 | OCR 依赖可能影响启动/首次调用和机器兼容性 | 中 | 部分；可改走 Tesseract | `rapidocr`/`onnxruntime` 是 optional extra；ONNX Runtime 可能受 CPU/DLL 环境影响。 |
| 14 | 结构化和可观测的流程审计不完整 | 中 | 部分；PowerShell 日志/文件可外部补充 | 没有统一 run ID、步骤账本、重放和凭证隔离层。 |
| 15 | 无 OCR（原缺口） | 已关闭 | 不适用 | `Ocr` 已注册；RapidOCR 为主，其他引擎按可选链降级。 |
| 16 | 网页无 JS/Cookie/网络监听（原缺口） | 已关闭 | 不适用 | `Web` 的 `eval`、`cookies`、`network` 模式已实现；见 `web/service.py`。 |
| 17 | 剪贴板只支持文本（原缺口） | 已关闭 | 不适用 | `ClipboardAdvanced` 支持文本、图片、CF_HDROP 文件、HTML、清空和格式列举。 |
| 18 | 无压缩解压（原缺口） | 已关闭 | 不适用 | `Archive` 支持 zip/tar/tar.gz。 |
| 19 | 无电源/音量/输入法/屏保（原缺口） | 部分关闭 | 部分 | 电源、音量、亮度、布局切换、屏保已实现；IME 输入法组合控制仍未实现。 |
| 20 | 无邮件/FTP/群通知（原缺口） | 已关闭 | 不适用 | `Net` 已实现 SMTP、IMAP、FTP/FTPS、HTTP 和企业 webhook。 |
| 21 | 无 Excel/Word/PDF/数据库封装（原缺口） | 部分关闭 | 部分 | Excel/Word/PDF/CSV/SQLite 已有；格式和高级特性受限。 |
| 22 | 进程只能 list/kill（原缺口） | 已关闭 | 不适用 | 核心 `Process` 仍为 list/kill，但 `SystemProcess` 已增加启动、优先级、挂起/恢复、服务管理。 |
| 23 | 无对话框交互（原缺口） | 已关闭 | 不适用 | `SystemDialog` 已实现，但仍是独立 Tk 子进程且会阻塞调用。 |
| 24 | 窗口缺 close/显隐/最大化（原缺口） | 已关闭 | 不适用 | `WindowControl` 已覆盖 list/close/show/hide/minimize/maximize/restore/topmost。 |
| 25 | 键盘缺单键按下抬起（原缺口） | 已关闭 | 不适用 | `KeyDown`、`KeyUp`、`Hold`、`ReleaseKeys` 等已实现。 |
| 26 | 缺拟人化输入（原缺口） | 已关闭 | 不适用 | `TypeHuman`、`MoveHuman`、`ClickHuman`、`DragHuman` 已实现。 |

## 7. 结论

### 通用 GUI 自动化

按本文矩阵的 21 个域计算：16 个形成完整路径，3 个是局部/只读/间接路径，2 个仍无稳定路径；把局部域按 0.5 计，约为 **83%**。这个数字的含义是“常见 Windows GUI 自动化入口覆盖”，不是“成功率”：

- 强项：UIA、坐标输入、多显示器、截图、窗口、剪贴板、注册表、OCR、CDP、文件名/数据处理。
- 主要短板：图像模板匹配、像素级定位、虚拟桌面控制、通用计划任务、IME 组合输入和复杂桌面应用的自绘控件。

### 对标影刀做企业级 RPA

- **单点工具可替代度**：按 20 个大类做等权近似，约 **55%–60%**。网页、鼠标键盘、等待、基础系统操作和文件/文档能力较强。
- **企业级完整链路覆盖度**：按流程设计、版本化、凭证、队列、调度、审计、连接器、移动端等完整能力算，约 **40%–50%**。工作队列、流程持久化、连接器市场、手机自动化和统一自愈机制是决定性缺口。
- 本项目不应被包装成“影刀同构替代品”：它更像面向 AI Agent 的本地系统/浏览器工具面，优势在 MCP 工具调用、可靠性原语和可插拔策略，而不是图形化 RPA 编排平台。