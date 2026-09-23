# 缺口补齐记录

> 核对基准：当前工作树 `0.8.5`，核对日期为 2026-09-23。只以仓库源码、`pyproject.toml`、`uv.lock` 和可复现的本地检查为依据；“仓库内没有专项测试”不会被写成“已端到端验证”。

## 1. 补齐总览表

| 缺口 | 实现模块 | 工具名 | 新增依赖 | 验证方式 |
|---|---|---|---|---|
| 无本地 OCR 与文字坐标 | `src/windows_mcp/ocr/service.py`、`src/windows_mcp/tools/ocr.py` | `Ocr` | `rapidocr>=3.9.2`、`onnxruntime>=1.30.0`；Tesseract/WinRT 为可选降级 | 源码路径、extras、注册表静态核对；本机运行导入受 DLL 环境限制，详见 2.1。 |
| 网页自动化弱、无 CDP/校验 | `src/windows_mcp/web/service.py`、`src/windows_mcp/tools/web.py` | `Web` | `playwright>=1.63.0` | 源码核对全部 17 个 mode、settle/goto/snapshot/validation 路径；无新专项测试文件。 |
| 需要目标驱动、可插拔的浏览器 agent | `src/windows_mcp/agent/*`、`src/windows_mcp/tools/web_agent.py` | `WebAgent` | 复用 Playwright；非 dry-run 需要模型凭据 | 源码核对 `JevPolicy`/`OpenRouterPolicy`/`LayaPolicy`、auto 优先级和反消歧逻辑；无凭据时未做在线决策。 |
| 动作无重试、无校验 | `src/windows_mcp/reliability/service.py`、`src/windows_mcp/tools/act.py` | `Act` | 无新第三方依赖 | 源码核对 `retry_call`、`with_retry`、`resolve_target`、`element_occluded`、`wait_for_change` 和 `Act` 调用链。 |
| 键盘缺按下/抬起、缺拟人化输入 | `src/windows_mcp/inputx/service.py`、`src/windows_mcp/tools/inputx.py` | `KeyDown`、`KeyUp`、`Hold`、`Press`、`Hotkey`、`ReleaseKeys`、`TypeHuman`、`MoveHuman`、`ClickHuman`、`DragHuman` | 无新第三方依赖 | 源码核对 Win32 `SendInput`、键状态跟踪、Bézier 轨迹和过冲回拉；无新专项测试。 |
| 窗口/剪贴板/压缩/电源/进程/对话框能力不足 | `src/windows_mcp/systemx/service.py`、`src/windows_mcp/tools/systemx.py` | `WindowControl`、`ClipboardAdvanced`、`Archive`、`SystemControl`、`SystemProcess`、`SystemDialog` | 无新第三方依赖（使用 pywin32/psutil/Pillow/Tk/标准库） | 源码核对服务函数、mode 分支和 PowerShell/WMI/COM 调用；无新专项测试。 |
| 无邮件/FTP/群通知 | `src/windows_mcp/net/service.py`、`src/windows_mcp/tools/net.py` | `Net` | `requests`（项目基础依赖）；邮件/FTP 为 stdlib | 源码核对 SMTP/IMAP/FTP/webhook 分支和钉钉加签；未进行真实账号连通测试。 |
| 无 Excel/Word/PDF/数据库封装 | `src/windows_mcp/office/service.py`、`src/windows_mcp/tools/office.py` | `Office` | `openpyxl>=3.1.5`、`python-docx>=1.2.0`、`pypdf>=6.19.0`；SQLite/CSV 为 stdlib | 源码和锁文件核对格式约束、读写/合并/事务路径；无新专项测试。 |
| 可选能力依赖失败拖垮服务器 | `src/windows_mcp/tools/__init__.py` | 无新增工具 | 无 | 源码核对 `_load()` 和 `register_all()`；注意 OCR/Office 是延迟导入，缺包时不一定会跳过注册。 |
| Python 版本钉死、doctor 缺失 | `src/windows_mcp/interpreter.py`、`src/windows_mcp/__main__.py` | CLI：`windows-mcp doctor` | `uv`（运行时工具） | 源码核对解释器发现、版本约束、交互授权和 `--check-only`；当前 `.python-version` 为 `3.14`。 |
| 模型请求连接复用与决策重试/消歧不足 | `src/windows_mcp/agent/openrouter.py`、`src/windows_mcp/agent/service.py`、`src/windows_mcp/web/service.py` | 无新增 MCP 工具名 | `requests`（基础依赖） | 源码核对线程本地 Session、`decision_attempts=3`、JSON 解析、候选 role 排序、页面 settle 和指纹。 |

> 计数口径说明：`src/windows_mcp/tools/__init__.py` 的 `_OPTIONAL_MODULES` 实际列出 **8 个模块名**，而不是对话摘要里的“7 个工具包”。其中 `web_agent` 复用 `web` 的 Playwright 能力；本表按实现模块和系统增强分组展示，避免把模块数和工具数混为一谈。

## 2. 逐个说明

### 2.1 OCR：`Ocr`

**原缺口**：只能依赖 UIA/文本树，canvas、自绘控件、扫描图和图片中的文字无法定位。

**实现方案**：新增 `windows_mcp.ocr` 领域包和 `tools/ocr.py`。`recognize()` 支持 `screen`、`region`、`file`、`element` 四个模式；`auto` 引擎链为 `rapidocr → tesseract → windows`。结果返回行/词的文本、bbox、中心点、polygon 和置信度。

**资源取舍与理由**：

- 选择 PyPI 包 `rapidocr>=3.9.2` + `onnxruntime>=1.30.0`，工具描述称默认使用 PP-OCRv6；模型路径为本地 CPU 推理，不要求云 API。
- 未选 `rapidocr-onnxruntime`：2026-09-23 查询该包 1.4.4 元数据为 `requires-python <3.13,>=3.6`，与项目 `requires-python >=3.14` 冲突；当前代码直接导入的是 `rapidocr.RapidOCR`，不是 `rapidocr_onnxruntime`。
- 排除 PaddleOCR/EasyOCR/docTR 作为默认方案：它们会带来 Paddle/PyTorch/训练和推理生态，不符合本项目的轻量 optional extra 目标；Paddle/EasyOCR 还必须处理额外框架和模型安装问题。
- `pytesseract` 需要系统 Tesseract；`Windows.Media.Ocr` 需要额外 WinRT 包。两者保留为显式/自动降级路径，不设为基础必装。
- **和摘要的差异**：“本机 Win10 19044 实测 Windows.Media.Ocr 类型不可用”没有在仓库代码/注释中记录；代码只能证明它被接入为可选 backend，不能证明所有 Win10 构建都不可用。

**依赖**：`rapidocr>=3.9.2`、`onnxruntime>=1.30.0`；`pytesseract`、WinRT 包不是自动安装依赖。

**已验证的行为**：`_normalize_engine()`、`_run_rapidocr()`、`_run_tesseract()`、`_run_windows_media_ocr()` 和 `_recognize_local()` 的调用链存在；`Ocr` 在 `_OPTIONAL_MODULES` 中注册；当前 `.venv` 有 `rapidocr 3.9.2` 和 `onnxruntime 1.30.0` 的 dist-info，但本次工作树的 `onnxruntime` 导入以 DLL 访问失败结束，因此不写成“本机 OCR 已完成端到端验证”。

**已知限制**：RapidOCR 默认模型主要针对中文+英文，其他语言要换引擎；首次加载模型可能较慢；CPU 推理对模型、图片大小和机器性能敏感；OCR 只给文字，不做图像模板匹配。

### 2.2 网页控制：`Web`

**原缺口**：原来只有 `Scrape`/DOM 只读能力，缺少可复用浏览器会话、元素 ref、动作校验和细粒度网络控制。

**实现方案**：新增 `windows_mcp.web` 和 `tools/web.py`。`Web` 的 `mode` 为 `connect`、`launch`、`close`、`status`、`snapshot`、`click`、`type`、`select`、`scroll`、`hover`、`press`、`eval`、`cookies`、`network`、`wait`、`screenshot`、`extract`。默认 CDP endpoint 是 `http://127.0.0.1:9222`。

关键实现：

- `_JS_SNAPSHOT` 给可见交互元素分配 `ref` 和临时 `data-windows-mcp-ref` token；后续动作优先使用 ref，避免把选择器泄漏给模型。
- `_ensure_visible_sync()` 检查可见和启用状态；`_ensure_not_obscured_sync()` 用 `document.elementFromPoint()` 检查遮挡。
- `_validation_sync()` 在点击/输入后检查 URL 变化、元素是否仍可见或已脱离。
- `_SETTLE_TIMEOUT_MS = 10_000`；`_settle_page()` 等待交互元素出现，出现后额外等 0.35 秒并再读一次，避免 SPA/同意页面的部分快照。
- `goto()` 使用 `page.goto(..., wait_until="load")`，清空 refs，再 `_settle_page(page, timeout_ms=min(timeout, 10_000))`。
- `snapshot_data()` 返回 URL、标题、最多 12,000 字符页面文本、scrollY、元素数组和 SHA-256 fingerprint。
- `cookies` 支持 `get/set/clear`；`eval` 执行当前页面 JS；`network` 捕获 XHR/fetch 的 request/response、状态和 header 摘要。

**资源取舍与理由**：依赖声明选择 `playwright>=1.63.0`，不引入 `browser-harness`；代码中没有 browser-harness 依赖或适配层。使用 Playwright 的自带 CDP 连接和 Locator，减少自研浏览器协议客户端的心智和维护成本。

**已验证的行为**：源码静态核对 17 个 mode 分支、连接/启动/关闭、ref 解析、可见/遮挡检查、动作后校验、Cookie/JS/network 路径。仓库当前测试列表没有 `test_web_*` 专项文件。

**已知限制**：只捕获 XHR/fetch，不捕获 WebSocket、HAR 或响应体；不提供请求改写/路由拦截；`eval` 是强权限入口；CDP 连接依赖 Chrome 已用正确端口启动；Playwright 浏览器需额外安装 Chromium。

### 2.3 浏览器 Agent：`WebAgent`

**原缺口**：有确定性浏览器动作，但没有“观察 → 决策 → 执行 → 再观察”的目标驱动闭环。

**实现方案**：新增 `windows_mcp.agent` 和 `tools/web_agent.py`。`WebAgentService` 默认 `max_steps=25`、`headless=False`、`timeout_ms=30000`、`max_elements=150`、`dry_run=False`。每一步从结构化快照构建有限动作空间，动作执行后比较 fingerprint，连续 3 个非 WAIT 动作无页面变化则标记 blocked。

策略事实：

- `JevPolicy`：双后端。TypeSafe key 优先走 `https://api.typesafe.ai/v1/systemone`；否则可用 OpenRouter key 走 `https://openrouter.ai/api/alpha/decisions`。
- `OpenRouterPolicy`：使用 OpenAI-compatible chat completions + strict JSON schema。
- `LayaPolicy`：仅保留本地 ONNX/Node 入口；`available` 可能为真，但 `decide()` 和 `text()` 仍会抛 `NotImplementedError`，不是可用后端。
- `get_policy("auto")` 的优先级是 `jev → openrouter → laya`，取第一个 `available` 的策略。
- `_disambiguate()`：当操作只有一个目标时直接选择该目标；多目标时 `TYPE_TEXT` 的 role 排序为 `combobox=0、searchbox=1、textbox=2、其他=9`，`SELECT` 为 `combobox=0、listbox=1、其他=9`，同 rank 按目标索引升序；只有最佳 rank 不是 9 时才改写决定并设置 `resolved_deterministically=True`。

**资源取舍与理由**：Policy 可插拔，模型只选择快照中提供的索引，不接收 selector/坐标/JS；TypeSafe 提供 Jev 的校准概率，OpenRouter 提供无需额外 SDK 的严格 JSON 替代，Laya 保留本地模型入口但未加载权重。

**依赖**：Playwright；HTTP 通过基础依赖 `requests`；在线决策还需要 TypeSafe 或 OpenRouter 凭据，`TYPE_TEXT` 的文本生成还需要 text model 凭据。

**已验证的行为**：源码核对策略注册、auto 顺序、动作空间校验、dry-run skeleton、`decision_attempts=3`、页面变化检测和 `_disambiguate()`。未配置凭据，未做在线 model 调用。

**已知限制**：Laya 未实现；`auto` 无可用策略时抛 `PolicyUnavailableError`；`TYPE_TEXT` 无文本模型时拒绝猜测值；agent 的 blocked 判定基于页面 fingerprint 变化，React/Canvas 应用可能出现假阴性。
### 2.4 动作可靠性：`Act`

**原缺口**：普通 `Click`/`Type` 是单次动作；失败、元素消失或被遮挡时，调用方要自己重新 Snapshot、重新定位和重试。

**实现方案**：新增 `windows_mcp.reliability` 和 `tools/act.py`。可靠性层提供：

- `retry_call()`：同步有界指数退避；`(ok, detail)` 中 `ok=False` 也作为可重试信号。
- `with_retry()`：同步/异步装饰器版本。
- `resolve_target()`：接受整数标签、`label:12`、`[x,y]`、`x,y`、`{label:...}`、`{x,y}`、`{point:...}`。
- `element_alive()`、`element_visible()`、`element_enabled()`、`element_occluded()`、`point_occluded()`。
- `wait_for_change()`：轮询探针直到变化或超时。

`Act` 支持 `CLICK`、`DOUBLE_CLICK`、`RIGHT_CLICK`、`HOVER`、`TYPE_TEXT`、`SELECT`、`SCROLL_UP`、`SCROLL_DOWN`、`PRESS`、`FOCUS`、`WAIT`、`DONE`；`retries` 限制 0–5，`timeout` 必须大于 0 且不超过 30 秒。`WAIT`/`DONE` 不执行额外重试。

**资源取舍与理由**：没有引入通用重试库。这里的核心问题不是简单函数重试，而是 UIA 树刷新、元素可见/启用/遮挡和坐标变化；自研层只依赖 stdlib 和项目已有 UIA，能保持行为可控。

**已验证的行为**：源码核对函数导出和 `Act` 的实际调用链：`retry_call` 驱动 `_run_attempt`，`_parse_verify` 生成校验条件，返回包含 `ok`、`verified`、`attempts` 和 `resolved_target`。仓库当前没有 `test_act*`/`test_reliability*` 专项文件。

**已知限制**：只对 `Act` 生效，不自动包裹所有 MCP 工具；验证条件依赖当前 UIA/状态探针，不能证明外部系统事务成功；`retries` 和 `timeout` 有硬上限；不可代替幂等性设计。

### 2.5 键盘与拟人化输入：`inputx`

**原缺口**：普通工具主要提供 Click/Type/Shortcut，缺少单键按下保持、防卡键释放，以及可配置的人手鼠标轨迹和键入节奏。

**实现方案**：新增 `windows_mcp.inputx`，注册 10 个工具，全部通过 `ctypes` 调用 Win32 `SendInput`：

| 工具 | 行为 |
|---|---|
| `KeyDown` / `KeyUp` | 显式按下和释放，服务跟踪已按下的键。 |
| `Press` / `Hold` | 按下后自动释放，或保持指定时长。 |
| `Hotkey` | 顺序按下组合键，逆序释放。 |
| `ReleaseKeys` | 释放服务追踪的全部键。 |
| `TypeHuman` | 字符输入加随机节奏扰动；Shift 临时处理。 |
| `MoveHuman` | 线性/三次 Bézier 轨迹、`ease_in_out_cubic`、过冲回拉。 |
| `ClickHuman` / `DragHuman` | 拟人化移动后执行点击/拖拽。 |

**开源候选取舍**（star/许可证为 2026-09-23 GitHub API 数据；不是依赖声明）：

| 候选 | 当前可核对元数据 | 放弃原因 |
|---|---:|---|
| `pyclick` | 193★，MIT | 引入 numpy/路径依赖，仍需接项目自己的输入和坐标层。 |
| `HumanCursor` | 528★，MIT | 带轨迹/控制抽象，依赖和全局鼠标语义不满足轻量、可预测目标。 |
| `pyautogui` | 12,706★，BSD-3-Clause | 跨平台全套依赖较重，且会带入额外鼠标/键盘后端。 |
| `pynput` | 2,170★，LGPL-3.0 | LGPL/全局监听与输入 hook 语义不适合作为基础依赖。 |
| `AsfhtgkDavid/windmouse` | 65★，GPL-3.0 | GPL-3.0 不适合直接作为 MIT 项目的默认依赖。 |
| `human_mouse` | 未能确认题目所述 104★/许可不明的对应仓库 | 不把未确认仓库引入依赖；不写成已核实事实。 |

最终选择零新增依赖：`math` + `ctypes` + `SendInput` + 自研 Bézier/easing/jitter。

**已验证的行为**：源码核对 `SendInput` 参数结构、键状态集合、组合键逆序释放、Bézier 控制点、easing 和过冲修正分支；没有针对真实鼠标轨迹的统计验证或反检测承诺。

**已知限制**：这是“拟人化启发式”，不保证绕过反自动化系统；UAC 安全桌面等受保护桌面通常不可注入；某些游戏/远程桌面环境会忽略注入；不会隐藏快捷键或全局输入行为。

### 2.6 系统增强：`SystemX`

**原缺口**：窗口/剪贴板/压缩/电源/进程/对话框能力分散或不完整。

**实现方案**：

- `WindowControl`：`PostMessage(WM_CLOSE)`、`ShowWindow`、`SetWindowPos`，支持 list/close/show/hide/minimize/maximize/restore/topmost。
- `ClipboardAdvanced`：`win32clipboard` 处理 Unicode、DIB 图片、CF_HDROP 文件列表、HTML Format、clear 和 formats。
- `Archive`：stdlib `zipfile`/`tarfile`/`shutil`，支持 zip/tar/tar.gz，解压时拒绝路径穿越和链接。
- `SystemControl`：音量/静音用 PowerShell 内联 C# Core Audio；亮度用 WMI；锁屏/屏保/关机/重启/注销/睡眠/休眠通过 Win32/PowerShell；键盘布局枚举和切换。
- `SystemProcess`：`psutil` 启动/优先级/挂起/恢复和服务 list/start/stop；挂起使用 `OpenProcess` + suspend/resume。
- `SystemDialog`：独立 `python -c` Tk 子进程，避免 MCP server 无 UI 线程，模式含 message/input/choice/file/folder/date。

**资源取舍与理由**：不引入 `pycaw`，音量直接使用 Core Audio COM；不引入 Office/WPS 依赖，文档能力另放 `office`；对话框使用已可用的 Tk，而不是把 GUI 主循环放进 server 线程。

**已验证的行为**：源码核对所有 mode 的服务分支、参数转换和 Windows/PowerShell 调用；未在有管理员权限的真实环境完成服务启停/电源/对话框端到端测试。

**已知限制**：服务启停通常需要管理员；亮度不是所有显示器都支持 WMI；对话框会等待用户且可能被超时终止；音量依赖 Windows PowerShell 5.1/Core Audio；归档只覆盖 zip/tar 系，不含 7z/rar。

### 2.7 网络：`Net`

**原缺口**：只有网页抓取，缺少可编程 HTTP、邮件、FTP 和企业群通知。

**实现方案**：新增 `windows_mcp.net` 和 `tools/net.py`。`Net` 模式为 `http`、`download`、`smtp_send`、`imap`、`ftp_connect`、`ftp`、`ftp_disconnect`、`webhook`。

- HTTP 用已声明的 `requests`，支持 method/headers/params/json/form/raw body、重定向、TLS 校验和文本截断。
- 下载支持续传、expected size 和 overwrite。
- SMTP 使用 stdlib `smtplib`，支持 SSL/STARTTLS、Text/HTML、To/Cc/Bcc 和附件。
- IMAP 使用 stdlib `imaplib`，支持 list/search/fetch/mark read 和附件落盘。
- FTP 使用 stdlib `ftplib` 的 `FTP`/`FTP_TLS`，连接 id 由服务保存，显式 disconnect。
- Webhook 用 `requests.post`，适配企业微信、钉钉、飞书；钉钉可做 HMAC-SHA256 加签。

**资源取舍与理由**：邮件和 FTP 用 Python stdlib，企业 webhook 只是 HTTP POST，因此不引入厂商 SDK；HTTP 复用项目已有的 `requests`，避免另加 aiohttp/httpx。

**已验证的行为**：源码核对 8 个 mode 和 vendor error code 解析；未使用真实邮箱、FTP 或企业 webhook 凭据做在线测试。

**已知限制**：SMTP/IMAP 只有密码认证，没有 OAuth2/OIDC；凭据按调用参数传入，未接系统凭据库；FTP 会话在服务内存中保存；企业 webhook 受供应商频率和文本格式限制。

### 2.8 Office/数据库：`Office`

**原缺口**：没有不依赖 Microsoft Office 的 Excel、Word、PDF、CSV 和数据库操作。

**实现方案**：新增 `windows_mcp.office` 和 `tools/office.py`。Excel 用 `openpyxl`，Word 用 `python-docx`，PDF 用 `pypdf`（含加密 PDF 密码解密），CSV 用 stdlib `csv`，SQLite 用 stdlib `sqlite3`。SQL helpers 使用绑定参数、事务和 rollback；没有拼接用户值到 SQL。

**资源取舍与理由**：选择依赖较轻且可离线运行的包：`openpyxl` 只带来 `et-xmlfile`，`python-docx` 主要依赖 `lxml`，`pypdf` 以纯 Python 路径为主；`pyproject.toml` 没有引入 pandas。数据库默认用 stdlib SQLite，不引入 SQLAlchemy/pyodbc。

**已验证的行为**：锁文件/当前环境包含 `openpyxl 3.1.5`、`python-docx 1.2.0`、`pypdf 6.19.0`；源码核对扩展名校验、覆盖策略、公式、分页、合并和 SQLite 事务分支。仓库当前没有 `test_office*` 专项文件。

**已知限制**：Excel 只接受 `.xlsx/.xlsm`，创建输出只保证 `.xlsx`；Word 只接受/输出 `.docx`；PDF 提取依赖文本层，扫描件要转 OCR；不支持 Excel 宏执行、复杂样式、图表、透视表或 pandas DataFrame；数据库连接器只有 SQLite。
### 2.9 工具包容错注册

**原缺口**：一个 optional 包导入失败会让整个 MCP server 无法暴露剩余工具。

**实现方案**：`src/windows_mcp/tools/__init__.py` 的 `_load(name, optional=...)` 使用 `importlib.import_module`；optional 导入失败时调用 `logger.warning("Tool pack %r unavailable, skipping: %s", name, exc)` 并返回 `None`，`register_all()` 过滤 `None` 后继续注册其他模块。核心模块失败仍然 `raise`，这是有意设计。

**需要特别注明**：摘要称“缺依赖时只跳过该工具包”，但源码事实更细：

- `web` 在 `web/service.py` 顶层导入 `playwright.sync_api`，缺 Playwright 时 `web` 会跳过；`web_agent` 导入路径依赖 `web`，也会受牵连。
- `ocr` 的 `RapidOCR`、`tesseract`、WinRT 都是函数内延迟导入；`office` 的 `openpyxl`/`python-docx`/`pypdf` 也延迟导入。因此缺 OCR/Office extra 时工具模块可能仍成功注册，调用后才返回依赖错误。
- `act`、`inputx`、`systemx`、`net` 主要使用项目基础依赖或 stdlib，通常能随基础环境注册。
- 因此“server 不会被单个 optional import 拖垮”成立，但“缺依赖时一定从工具面消失”不成立。

**已验证的行为**：源码静态核对注册循环和 warning 分支。

**已知限制**：工具列表能否在客户端出现取决于客户端首次注册时的导入结果；迟绑定依赖错误只能在调用时暴露；隐藏依赖错误会增加用户排障成本。

### 2.10 解释器发现与 `windows-mcp doctor`

**原问题**：`.python-version` 曾钉死不存在的补丁版本，`uv run` 与机器现有解释器不一致。当前工作树的真实事实是：

- `.python-version` = `3.14`（单行），不是 `3.14.7`。
- `pyproject.toml` 的 `requires-python` = `>=3.14`，classifier 和 Ruff target 也都是 3.14。
- 当前仓库文档中仍有一处旧记录提到 `3.14.7`，但该值不在当前 `.python-version` 中；本次只读核对没有改写该文档。

**实现方案**：`src/windows_mcp/interpreter.py` 提供 `parse_requires_python()`、`satisfies()`、`discover_interpreters()`、`format_interpreters()`、`ensure_interpreter()`。`src/windows_mcp/__main__.py` 的 `doctor` 命令展示 OS、当前 Python、uv、requires-python、兼容版本和解释器列表。

**授权规则**：没有兼容解释器时，`doctor` 默认询问 `y/yes`；`--yes` 可显式授权；`--check-only` 只报告不修改；非交互环境没有授权时不等待输入，直接报错并给出手工 `uv python install <spec>` 指引。唯一安装动作是 `uv python install <派生版本>`，不升级、替换、卸载或修改现有系统/conda/pyenv 解释器。

**已验证的行为**：源码核对 `interactive_enabled = sys.stdin.isatty()`、授权分支、安装命令和安装后重新发现逻辑。

**已知限制**：依赖 PATH 上的 `uv`；如果 `requires-python` 无法推导出安装版本，只给手工指引；不会替用户自动选择现有解释器以外的版本策略。

### 2.11 模型连接池、决策重试和自动消歧

**原缺口**：每次决策重新建立 TLS 连接，模型偶发空 JSON 会中断多步任务，单输入框仍可能被模型选错。

**实现方案**：

- `agent/openrouter.py` 使用 `threading.local()` 保存每个线程的 `requests.Session`；源码注释记录实测：未复用 Session 的决策中位数约 1,898 ms，复用后约 1,412 ms，后续调用约 1.0 s，主要节省 TCP+TLS 握手。
- `_post_json()` 只发一次请求；`OpenRouterPolicy.decision_attempts = 3`，只对 `ValueError`/`RuntimeError` 做 3 次尝试，间隔为 `0.4 * 2**attempt`。最后一轮重新抛出错误，错误信息明确“no action executed”。
- `_parse_json_object()` 接受裸 JSON 对象、Markdown fence、JSON 字符串、单元素列表和混在推理文本中的平衡 `{...}` 块；找不到对象即拒绝执行。
- `WebAgentService._disambiguate()` 对单候选目标直接确定化；多候选时按下表排序后选择。

| 操作 | role 优先级 |
|---|---|
| `TYPE_TEXT` | `combobox=0`、`searchbox=1`、`textbox=2`，其他为 9 |
| `SELECT` | `combobox=0`、`listbox=1`，其他为 9 |
| 排序规则 | 先 role rank，再目标索引 `int(item[0])` 升序 |
| 无法确定 | 最佳 rank 为 9 时保留模型原决定 |

**已验证的行为**：源码核对 Session 生命周期、重试次数/退避、`_parse_json_object()` 的输入形态和 `_disambiguate()` 的 rank 字典。注释中的 latency 是源码提供的测量值，本次未重新压测。

**已知限制**：连接池是线程本地，不跨进程/线程共享；只重试模型输出错误，不重试已经执行过的页面动作；role 名依赖快照归一化；自动消歧会把 `resolved_deterministically=True` 暴露给调用方，但仍可能因动态网页变化而选错。

## 3. 依赖策略

`pyproject.toml` 当前真实 extras：

| extra | 内容 |
|---|---|
| `ocr` | `rapidocr>=3.9.2`、`onnxruntime>=1.30.0` |
| `web` | `playwright>=1.63.0` |
| `office` | `openpyxl>=3.1.5`、`python-docx>=1.2.0`、`pypdf>=6.19.0` |
| `all` | 上述 OCR、Web、Office 全部依赖的并集 |
| `dev` | `ruff>=0.9.0`、`pytest>=8.0.0`、`pytest-asyncio>=0.24.0` |

基础依赖仍包含 `comtypes`、`dxcam`、`pillow`、`pywin32`、`requests`、`psutil` 等；`pandas`、`pycaw`、SQLAlchemy、pyodbc 不在当前依赖中。`uv.lock` 当前解析到的关键版本与 extras 约束一致：RapidOCR 3.9.2、ONNX Runtime 1.30.0、Playwright 1.63.0、openpyxl 3.1.5、python-docx 1.2.0、pypdf 6.19.0。

注册策略是“核心模块 fail-fast、可选模块 import-fail-soft”，但如 2.9 所述，OCR/Office 的依赖是延迟导入，不能把“模块已导入”等同于“功能已可用”。

## 4. 未补齐 / 仍缺的能力

- **图像模板匹配**：用户明确不作为本轮实现范围；当前没有 OpenCV/template matching 工具。
- **Laya 本地决策后端**：`LayaPolicy` 是保留入口，`decide()`/`text()` 未实现；没有加载约 1.7 GB 的 ONNX 权重，也没有 Node 运行时桥。
- **Excel 格式**：只支持 `.xlsx/.xlsm`，不支持 `.xls`、WPS 原生对象、宏执行或完整 Excel 进程自动化。
- **Word 格式**：只支持 `.docx`，不支持旧 `.doc`、复杂域/批注/修订的完整操作。
- **PDF**：只能做文本层提取、分页、合并/拆分和基本加密处理；扫描件必须走 OCR，不能保证布局/表格结构恢复。
- **邮件认证**：SMTP/IMAP 只支持用户名/密码，不支持 OAuth2；未接系统凭据库、邮件同步或增量状态。
- **Windows 服务**：启停通常需要管理员权限；不保证依赖服务、恢复策略和远程服务管理。
- **Web 网络观测**：只捕获 XHR/fetch，不覆盖 WebSocket、HAR、请求改写、响应体和跨页面事件持久化。
- **IME/编辑器适配**：只有键盘布局枚举/切换；没有 IME 组合与候选词控制。Notepad3/Scintilla 等自绘编辑器的 UIA 文本缺陷没有找到代码级适配，且该限制未能在本轮代码中确认。
- **企业级 RPA 基础层**：仍缺工作队列、流程版本化/调度、凭证保险库、连接器市场、统一审计/重放和移动端自动化。