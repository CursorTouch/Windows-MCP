# Windows-MCP Web 自动化调研报告

## 1. 结论先行

做 Windows-MCP 的 web 能力，默认路线应当是 Playwright + CDP 的确定性浏览器控制：它是无 LLM 往返的网页控制最快路径，Jev/Laya 则放在可选的 agent 决策层。

三者的职责不同：

- Playwright/CDP 是浏览器控制层，直接操作真实浏览器、DOM、Cookie 和网络事件，不依赖 LLM 往返。
- Jev/browser-use/jev-ultrafast 是 agent 决策循环，把页面状态压缩成索引化动作空间，用一次模型/API 往返输出 operation 和 target；价值在决策，不在替代 Playwright 的点击、输入和 DOM 解析。
- Laya 是本地决策模型/推理后端，可以替代托管的 Jev 决策 API，但同样不是浏览器控制层。

推荐架构：

~~~text
调用方/agent
  -> Web 工具（确定性动作）
    -> Playwright/CDP
      -> 用户真实、已登录的 Chrome

可选增强：
  页面状态 -> 本地 Laya 或托管 Jev -> (operation, target)
    -> 同一个 Web 工具执行
~~~

日常脚本、规则流程、测试和固定业务操作不需要决策模型。只有需要根据页面状态自主选择下一步时，才把决策模型作为可选策略层。

## 2. Playwright/CDP、Jev-ultrafast、Laya 的取舍

| 方案 | 角色 | 延迟/性能 | 成本 | 可靠性边界 |
| --- | --- | --- | --- | --- |
| Playwright 1.63.0 + CDP | 浏览器控制层 | 本地控制动作通常为毫秒到数十毫秒；页面加载、网络和站点等待另计 | Python 包依赖仅 pyee、greenlet；浏览器二进制另行安装 | 有成熟的 auto-wait/actionability；确定性高；页面改版后需更新 selector 或重新 snapshot |
| browser-use/jev-ultrafast + 托管 Jev | agent 决策层加浏览器实现 | 每轮决策一次网络往返；Google Flights Zürich 到 London 多步整任务实测 7.1 秒 | 托管 API 成本、网络延迟、账号/配额和外部可用性 | 结构化状态、固定动作空间、freshness/遮挡校验有价值；仍是 LLM/网络系统，存在模型错误和状态过期 |
| Laya（PyPI laya 0.3.6） | 本地决策层 | 上游口径单次前向约 33ms（T4），批量约 7.2ms | torch>=2.0.0、transformers>=4.48.0、safetensors、huggingface_hub、numpy；PyTorch 约 2.5GB | 本地、多语言、ModernBERT/Router 有价值；Python/PyTorch 体积对轻量 MCP 不划算 |
| @receptron/laya（Node/ONNX） | 本地决策层 | 3 个问题批量约 140ms（Apple CPU，暖机后，上游口径） | ONNX Runtime 不需要 torch/Python，Node 20+；ONNX fp32 权重 1.7GB，加载约 2GB RAM | 与 TypeSafe Jev 的 system_one API 形状一致；不需要 Python 栈，但权重仍不适合作为默认依赖 |

### 2.1 Playwright/CDP

Playwright 的优势是把浏览器控制、等待和定位统一在一个成熟 API 中：

- 通过 connect_over_cdp 连接用户真实 Chrome，保留登录态和用户环境。
- locator、wait_for、actionability 检查和自动滚动减少大量手写轮询。
- DOM、页面事件、网络请求、Cookie、截图和 JavaScript 求值都在同一控制面内完成。
- 没有模型往返，控制动作本身不消耗推理成本。

它的代价是 selector 和页面结构仍然可能变化；因此不能把“能点”理解成“永远可靠”。需要重新 snapshot、过期引用失效、可见性/遮挡校验和有限重试。

### 2.2 Jev-ultrafast

Jev 是 TypeSafe AI 的 System One 决策模型（非自回归，输出 operation 和 target 两个决策头），托管 API 是其服务形态。browser-use/jev-ultrafast（MIT，约 1.8 万星）值得抄的是动作循环，而不是把 Jev API 设为必需依赖：

- 索引化元素表，例如 [1] button "Change ticket type"。
- 一轮决策同时输出 operation 和 target。
- 默认消费结构化状态，不把截图作为默认输入。
- 操作集固定为 CLICK、TYPE_TEXT、SELECT、SCROLL_UP、SCROLL_DOWN、WAIT、DONE、BLOCKED。
- 只有 TYPE_TEXT 才调用小 LLM 生成文本。
- 动作后校验页面 freshness 和点击遮挡。
- 有界等待：输入建议不超过 200ms，其他动作以两帧或约 50ms 为界。
- 隐藏标签页保持渲染，避免后台节流。

Google Flights Zürich 到 London 多步任务 7.1 秒是其整任务实测，包含文本生成和加载等待，不是单个 CLICK 的执行耗时。这个数字适合和“截图加多轮大模型往返”的 agent 比较，不应拿来和本地 Playwright 单击延迟直接比较。

### 2.3 Laya

Laya 有两条可选路线：

1. PyPI laya：本地、开源、Jev 兼容的 Python 路线；模型能力和语言覆盖好，但会把 PyTorch、Transformers 和模型缓存带进 MCP。
2. @receptron/laya（receptron/laya，约 295 星）：Node/TypeScript + ONNX Runtime，不需要 torch/Python，Node 20+，本机 Node v20.18.1 已满足；API 形状与 Jev system_one 对齐。代价是 1.7GB fp32 权重和约 2GB 内存。

结论是：如果以后确实需要本地决策器，优先做 ONNX 可选后端，而不是把 Python PyTorch Laya 放进基础安装。即使使用 ONNX，也建议单独安装，不进入 Windows-MCP 的默认依赖。

### 2.4 browser-use/windows-harness

browser-use/windows-harness（约 73 星）是同一组织面向 Windows 的薄封装，原语包括 see、key、type、click、paste、ax、script（上游描述为 6 个原语）。它和 Windows-MCP 的 Desktop 服务思路一致：少量稳定原语加结构化状态。它适合作为操作集设计参考，但本任务的 Web 工具还应提供 CDP 连接、索引化 DOM ref、稳定 locator 缓存和网络/Cookie 能力。

## 3. 低成本可实现案例：Playwright + Jev 式索引化动作空间

### 3.1 不接任何 LLM 的毫秒级路径

本项目的 Web 服务采用以下顺序：

1. snapshot 在当前页面执行一次 JavaScript，收集可见交互元素。
2. 为每个元素分配临时 data-windows-mcp-ref 标记，并用 Playwright locator 缓存引用。
3. 返回紧凑状态：

~~~text
[1] textbox    "Where from?"  value="San Francisco"
[2] combobox   "Where to?"    value=""
[3] button     "Search"
~~~

4. 调用方或确定性规则直接给出动作，例如 ("CLICK", "3")。
5. 工具把 ref 映射回真实 DOM 节点，先检查可见、启用和遮挡，再执行动作。
6. 动作后检查 URL、元素是否仍 attached/visible，并返回验证信息。

这个路径没有 LLM 请求、没有截图编码、没有多轮 tool-call 协商。控制动作的成本主要是 CDP/Playwright 通信和页面自身的事件处理；页面导航和站点网络等待仍然是任务耗时的大头。

### 3.2 如果要接决策模型

建议定义可选决策适配器，而不是把模型写进 Web 工具：

~~~text
Policy.decide(snapshot_text, task, history)
  -> (operation, target, optional_text)
~~~

- operation 只能来自固定集合：CLICK、TYPE_TEXT、SELECT、SCROLL_UP、SCROLL_DOWN、WAIT、DONE、BLOCKED。
- target 只能引用快照中的 ref，禁止模型输出任意 DOM 操作。
- TYPE_TEXT 的文本由调用方或小模型提供；固定表单完全不需要生成模型。
- 每次动作后重新 snapshot，避免旧 ref 被复用。
- 本地 Laya/ONNX 或托管 Jev 都只作为 Policy 实现，Playwright 控制层保持稳定。

## 4. 本项目落地选择

### 4.1 默认连接用户浏览器

Web 工具的默认模式是 connect，默认 CDP endpoint 为：

~~~text
http://127.0.0.1:9222
~~~

典型启动方式：

~~~powershell
chrome.exe --remote-debugging-port=9222 --user-data-dir=<专用用户数据目录>
~~~

这样可以直接使用用户真实、已登录的浏览器。launch 模式用于测试、隔离流程或无头运行；headless 可选。

### 4.2 索引化快照和引用缓存

快照中的 ref 不是模型凭文字猜测出来的 selector，而是执行时写入 DOM 的临时标记。服务端同时保存：

- ref 编号；
- 临时 token；
- Playwright locator；
- role、name、tag 等描述。

页面重新渲染后旧 ref 可能失效，后续动作会返回清晰的 stale ref/attached 错误，要求重新 snapshot。这比让模型长期缓存 CSS selector 更可靠。

### 4.3 动作后校验

每次交互不只看“调用 API 没抛异常”，还做轻量验证：

- 元素是否可见、是否 enabled；
- 点击前检查中心点是否被其他元素遮挡；
- 动作后检查 URL 是否变化、元素是否 detached/visible；
- 输入后回读 value；
- select 后回读 selectedOptions。

## 5. Web 工具接口

工具名：Web

参数签名：

~~~python
def web_tool(
    mode: WebMode = "connect",
    endpoint: str = "http://127.0.0.1:9222",
    headless: bool | str = False,
    url: str | None = None,
    ref: int | str | None = None,
    selector: str | None = None,
    selectors: list[str] | str | None = None,
    text: str | None = None,
    value: Any = None,
    key: str | None = None,
    script: str | None = None,
    action: str = "get",
    payload: Any = None,
    wait_for: WaitMode | None = None,
    timeout_ms: int | str = 30000,
    amount: int | str = 600,
    direction: ScrollDirection = "down",
    scroll_to: ScrollDirection | None = None,
    full_page: bool | str = True,
    path: str | None = None,
    attributes: list[str] | str | None = None,
    fields: list[str] | str | None = None,
    clear: bool | str = True,
    append: bool | str = False,
    press_enter: bool | str = False,
    max_elements: int | str = 150,
    limit: int | str = 100,
    tab_index: int | str | None = None,
    ctx: Context = None,
) -> str
~~~

模式覆盖：

- 生命周期：connect、launch、close、status。
- 定位与动作：snapshot、click、type、select、scroll、hover、press。
- 数据与控制：eval、cookies、network、wait、screenshot、extract。

工具层会把被 MCP 客户端序列化成字符串的 list/dict 尽量还原为 Python 结构，包括 cookie payload、selector 列表、extract fields 和 select value。

## 6. 依赖和浏览器二进制

本机已核实的 Playwright Python 包版本为 1.63.0，Python 包运行依赖仅 pyee 和 greenlet。建议加入 pyproject.toml 的精确依赖行是：

~~~toml
"playwright>=1.63.0",
~~~

浏览器二进制必须单独安装：

~~~powershell
uv run --python 3.14.6 --with playwright playwright install chromium
~~~

如果希望完全锁定复现版本，也可以写成 "playwright==1.63.0"。本项目当前未修改 pyproject.toml，按任务文件范围只提供依赖行。

## 7. 本机实测

仓库 .python-version 当前为 3.14.7，而本机可用解释器是 3.14.6；因此测试统一使用 --python 3.14.6。

导入检查（等价于去掉 --python 后配合 $env:UV_PYTHON）：

~~~powershell
uv run --python 3.14.6 --with playwright python -c "import windows_mcp.tools.web; print('import ok')"
$env:UV_PYTHON='3.14.6'; uv run --with playwright python -c "import windows_mcp.tools.web; print('import ok')"
~~~

真实输出：

~~~text
import ok
~~~

ruff 检查：

~~~powershell
uv run --python 3.14.6 --with ruff ruff check src/windows_mcp/web/__init__.py src/windows_mcp/web/service.py src/windows_mcp/tools/web.py
~~~

真实输出：

~~~text
All checks passed!
~~~

端到端测试使用 data:text/html 临时页面，不依赖外网，覆盖 launch、snapshot、type、click、eval、close：

~~~text
LAUNCH: {... "connected": true, "mode": "launch" ...}
SNAPSHOT:
url=data:text/html;charset=utf-8,...
title=Windows-MCP Web Test
[1] textbox    "Name" value=""
[2] button     "Save"
TYPE: Typed into ref [1]; value="Ada"; ref [1]; validation: no navigation, element_visible=True
CLICK: Clicked ref [2]; ref [2]; validation: no navigation, element_visible=True
EVAL: Hello Ada
CLOSE: Browser closed.
~~~

这证明从浏览器启动、结构化元素索引、ref 回映、键盘输入、点击、JavaScript 提取到关闭的确定性链路已经实际跑通。

## 8. 已知限制

- 当前 snapshot 主要覆盖主 frame 的可见交互元素，复杂 Shadow DOM、跨 iframe、Canvas 和自定义组件需要后续扩展。
- 使用临时 DOM 标记缓存 locator；页面重渲染后必须重新 snapshot，旧 ref 会失效。
- connect 依赖用户先以 CDP 端口启动 Chrome；如果目标是普通已打开但没有调试端口的 Chrome，不能直接接管。
- CDP 对真实用户浏览器的 close 语义可能关闭该浏览器；生产中应明确区分“断开连接”和“关闭浏览器”。
- 网络模式捕获 XHR/fetch 的请求元数据和响应头/状态，不默认捕获大响应体，避免上下文和内存膨胀。
- 截图会写入本地路径；元素截图仍需目标可见。
- Playwright 浏览器二进制不包含在 Python 包依赖中，部署时必须执行 playwright install chromium。
- 本任务按文件范围没有修改 src/windows_mcp/tools/__init__.py；该文件当前是集中注册白名单。正式启用 Web 工具时，需要在允许修改注册文件的变更中加入 web 模块，或在外层显式调用 web.register(...)。
- Laya ONNX 路线虽然不需要 torch/Python，但 1.7GB 权重和约 2GB 内存不适合默认内置；应作为可选策略后端。
