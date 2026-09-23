# PR 421 独立验收报告：能力包 + Jev 策略 + 可插拔 agent

> 本文件是本地独立验收 / 复核记录，随 PR 一起归档以便追溯。所有个人机器绝对路径已脱敏为 `<workspace>/`（任务工作目录）与 `<repo>/`（本仓库检出目录）。

- 目标仓库：`<workspace>\upstream-git`
- 目标提交：`3651293`（HEAD）
- 基线提交：`787385e`（origin/main）
- 验收方式：只读 Git/GitHub 本地副本/静态源码核对；未修改、未提交 `upstream-git`
- 验收日期：2026-09-23（Asia/Shanghai）

## 总体结论

**不可交付（按本次给定的严格验收标准）。**

核心功能代码并未发现会阻止合并的实现缺口；工具数、可选依赖容错、Jev 测量数字、以及抽查的功能实现均在静态源码层面成立。但新增文档 `docs/why-jev-is-fast.md` 明确写入了 7 处作者本机绝对路径，按本次标准属于交付卫生问题；在清理或泛化这些路径之前，不应视为可交付。若把工作树中已有的 PostHog `phc_...` 客户端 key 也按“任何真实 API key”严格处理，则还需由维护者确认其公开客户端 key 属性并决定是否需要豁免或轮换。

## 必查项汇总

| 项 | 结论 | 简短原因 |
|---|---|---|
| 1) 变更规模 | **PASS** | `46 files changed, 14732 insertions(+), 35 deletions(-)`，与声明一致。 |
| 2) 工具数量 | **PASS** | AST 静态统计为 42 个唯一工具名：核心 20 + 可选 22。 |
| 3) 可选依赖容错 | **PASS** | `_load(..., optional=True)` 捕获异常、告警并返回 `None`；核心导入仍抛错。动态故障注入验证通过。 |
| 4) 机密泄露 | **PASS（按 PR diff；严格工作树有历史 PHC key 告警）** | `sk-or-v1-*`、`sk-*`、长 `Bearer`、密码字面量扫描无命中；`config.toml` 已忽略。工作树存在历史 PostHog `phc_...` 客户端 key，但不在本 PR diff，且它不是 PostHog 的项目 secret。 |
| 5) 机器特定绝对路径 | **FAIL** | 新增文档 `docs/why-jev-is-fast.md` 含 8 处作者本机绝对路径（本次归档时已全部改为仓库相对路径）。 |
| 6) 文档真实性 | **PASS（本地可核部分）** | Jev Ultrafast 的 7,073/17/178/3,720/927/90,558 等数字与本地 measurement JSON 完全一致；Laya/Von 外部数字本地无来源，列为未验证。 |
| 7) 内部一致性 | **PASS（静态抽查）** | OCR bbox、Web ref、KeyDown/KeyUp、电源、doctor、NAVIGATE_*、决策重试、thread-local Session 均有对应代码。未做端到端运行。 |

## 1. 变更规模

执行：

```powershell
git -C upstream-git diff --stat 787385e 3651293
```

输出结论：

```text
46 files changed, 14732 insertions(+), 35 deletions(-)
```

补充：

```powershell
git -C upstream-git status --short --branch
```

结果：工作树干净，HEAD 为 `3651293`，分支为 `feat/capability-packs-and-jev-policy`。本提交包含 46 个文件（其中新增 40 个文件、修改 6 个文件），规模与声明一致。

**结论：PASS。**

## 2. 工具数量

### 统计方法

1. 读取 `src/windows_mcp/tools/__init__.py:12-39`，确认 `_CORE_MODULES` 为 12 个模块，`_OPTIONAL_MODULES` 为 8 个模块。
2. 扫描 `src/windows_mcp/tools/*.py` 中每个 `register()` 内的 `@mcp.tool(...)` 装饰器；每个装饰器对应一个工具注册点。
3. 使用 Python AST 解析装饰器关键字 `name=`，没有名字时使用函数名，并统计重复性。

### 统计结果

```text
TOTAL 42 UNIQUE 42
DUPLICATES []
CORE 20
OPTIONAL 22
```

各模块计数：

```text
app: 1
display: 1
shell: 1
filesystem: 1
snapshot: 2 -> Snapshot, Screenshot
input: 7 -> Click, Type, Scroll, Move, Shortcut, Wait, WaitFor
scrape: 1
multi: 2
clipboard: 1
process: 1
notification: 1
registry: 1

ocr: 1
web: 1
web_agent: 1
act: 1
inputx: 10
systemx: 6
net: 1
office: 1
```

代码依据：

- `src/windows_mcp/tools/__init__.py:12-25`：12 个核心模块。
- `src/windows_mcp/tools/__init__.py:27-39`：8 个可选模块。
- `src/windows_mcp/tools/__init__.py:53-67`：逐个调用 `mod.register(...)`。
- `docs/feature-coverage.md:21-23` 声明核心 20、可选 22、总计 42，与统计一致。

**结论：PASS；正确数字为 42。**

## 3. 可选依赖容错

关键源码 `src/windows_mcp/tools/__init__.py:42-50`：

```python
def _load(name: str, *, optional: bool):
    try:
        return importlib.import_module(f"windows_mcp.tools.{name}")
    except Exception as exc:
        if not optional:
            raise
        logger.warning("Tool pack %r unavailable, skipping: %s", name, exc)
        return None
```

`src/windows_mcp/tools/__init__.py:60-65` 在核心模块加载后，过滤掉可选模块返回的 `None`，因此单个可选模块导入失败不会阻止其他模块注册。

故障注入验证：

```text
OPTIONAL_RESULT None
CORE_RAISED True
WARNING Tool pack 'missing_optional' unavailable, skipping: simulated missing optional dependency
```

说明：OCR/Office 的部分第三方依赖是函数内延迟导入，因此缺包时工具模块可能仍成功注册、调用时才失败；`docs/feature-coverage.md:24` 已明确说明这一点。这不是崩溃容错失败，而是工具注册与依赖解析的语义区别。

**结论：PASS。**

## 4. 机密泄露

### 扫描范围

- 提交 diff：`git diff 787385e 3651293`
- 当前工作树：`upstream-git` 全量文本文件
- 模式：`sk-or-v1-*`、`sk-*`、长 `Bearer`、`api_key/token/secret/password` 字面量、常见云/代码托管 token 前缀

### 结果

以下扫描均无命中：

```text
sk-or-v1-...
sk-...
Bearer <长串>
password = '...' / password = "..."
```

`src/windows_mcp/infrastructure/config.py:197` 和 `README.md:608` 的 `oauth_client_secret` 命中属于 TOML 序列化代码或 `"my-secret"` 占位符，不是实际凭据。

`.gitignore:175-180`：

```gitignore
# Local dev caches / machine-specific config (must never be committed)
.ruff_cache/
.uv-cache/
.venv-*/
config.toml
**/.windows-mcp/config.toml
```

`git check-ignore -v` 验证：

```text
.gitignore:179:config.toml    config.toml
.gitignore:180:**/.windows-mcp/config.toml    .windows-mcp/config.toml
```

仓库未跟踪 `config.toml`，当前工作树也没有该文件。

### 需要标注的历史 PHC key

全工作树扫描发现 `src/windows_mcp/infrastructure/analytics.py:45-48`：

```python
API_KEY = os.environ.get(
    "POSTHOG_API_KEY",
    "phc_uxdCItyVTjXNU0sMPr97dq3tcz39scQNt3qjTYw5vLV",
)
```

`git blame` 指向 `bf6801f`，不是本 PR 新增；`git diff 787385e 3651293 -- src/windows_mcp/infrastructure/analytics.py` 为空。该 `phc_` key 是 PostHog 客户端项目 API key（用于事件上报的公开 key），不属于 PostHog 管理/私有密钥，但严格按“任何真实 API key”字面标准，应将其列为安全负责人需要确认的历史告警。

**结论：PASS（本 PR 未新增机密）；严格工作树口径需保留上述 PHC key 告警。**

## 5. 机器特定绝对路径

提交中的新增文档 `docs/why-jev-is-fast.md` 含 7 处作者本机绝对路径：

- `docs/why-jev-is-fast.md:452`：`<workspace>/Windows-MCP-main/docs/web-automation-research.md`
- `docs/why-jev-is-fast.md:504`：`<workspace>/jev-ultrafast-main/`
- `docs/why-jev-is-fast.md:578`：`<workspace>/Windows-MCP-main/docs/web-automation-research.md`
- `docs/why-jev-is-fast.md:579`：`.../Windows-MCP-main/src/windows_mcp/web/service.py`
- `docs/why-jev-is-fast.md:580`：`.../Windows-MCP-main/src/windows_mcp/agent/action_space.py`
- `docs/why-jev-is-fast.md:581`：`.../Windows-MCP-main/src/windows_mcp/agent/jev.py`
- `docs/why-jev-is-fast.md:582`：`.../Windows-MCP-main/src/windows_mcp/agent/laya.py`
- `docs/why-jev-is-fast.md:583`：`.../Windows-MCP-main/src/windows_mcp/agent/policy.py`

这些路径来自新增文件，不是既有文件；`git diff --unified=0` 的对应 `+` 行可见。它们把作者用户名、工作区名称和本地目录布局固化进了交付文档，其他机器无法解析，且泄露本机目录结构。

`README.md:210/222/234/426/442` 使用 `C:\Users\<user>\...` 通用占位符，不是个人绝对路径，因此不构成本项问题。

**结论：FAIL。**

## 6. 文档真实性：Jev 数字与免责声明

### 免责声明

`docs/why-jev-is-fast.md:5` 明确声明：

```text
本次没有运行任何模型、浏览器基准或 TypeSafe API，因此下文所有延迟都明确标注为
“官方宣称”“仓库自报实测”或“第三方公开实测”，没有冒充本次实测。
```

与提交内容一致：该提交没有新增 measurement JSON 或基准产物；`git diff --name-only 787385e 3651293 | rg -i '\.json$|measurement|benchmark'` 无输出。文档引用的是本地只读的 `jev-ultrafast-main` 测量副本和外部来源，而不是本次运行结果。

### 本地 measurement JSON 核对

来源文件：

```text
<workspace>/jev-ultrafast-main/docs/flights-measurement.json
<workspace>/jev-ultrafast-main/docs/full-speed-measurement.json
<workspace>/jev-ultrafast-main/docs/flights-prepared-measurement.json
```

核对的具体数字：

| 文档数字 | 文档位置 | 本地 JSON/权威来源 | 核对结果 |
|---|---:|---|---|
| 总任务 7,073 ms | `docs/why-jev-is-fast.md:16/48/59/597` | `flights-measurement.json:2` = `7073` | PASS |
| Jev 请求 17 次 | `docs/why-jev-is-fast.md:16/48/59` | `flights-measurement.json:3` = `17` | PASS |
| 中位 178 ms/请求 | `docs/why-jev-is-fast.md:48/59` | `flights-measurement.json:4` = `178` | PASS |
| 决策总耗时 3,720 ms | `docs/why-jev-is-fast.md:48/59` | `flights-measurement.json:5` = `3720` | PASS |
| 两次文本生成 927 ms | `docs/why-jev-is-fast.md:16/48/59` | `flights-measurement.json:19 + 48` = `581 + 346 = 927` | PASS |
| 其余 2,426 ms | `docs/why-jev-is-fast.md:59` | `7073 - 3720 - 927 = 2426` | PASS |
| 90,558 input tokens | `docs/why-jev-is-fast.md` 对应 token 表 | `flights-measurement.json:103` = `90558` | PASS |
| 6,325 output tokens | 同上 | `flights-measurement.json:104` = `6325` | PASS |
| 11 个浏览器动作 + 1 个 WAIT | `docs/why-jev-is-fast.md:59` | `flights-measurement.json:6-7` = `browser_actions=11`, `wait_actions=1` | PASS |
| 9,450 ms -> 7,092 ms（25% 下降） | `docs/why-jev-is-fast.md:60/64` | `full-speed-measurement.json:8/14`，基线/候选中位数 | PASS |
| 1,092 -> 101 CDP calls | `docs/why-jev-is-fast.md:39/59` | `full-speed-measurement.json:9/15` | PASS |
| 候选第 3 次 7,092 ms / 16 requests / 2,996 ms / 1,039 ms | `docs/why-jev-is-fast.md:60` | `full-speed-measurement.json:757-760`，文本 latency `540 + 499 = 1039` | PASS |
| CDP 求和 2,840.7 ms，剩余 216.3 ms | `docs/why-jev-is-fast.md:60` | 候选第 3 次 CDP 明细求和 = `2840.749`，`7092 - 2996 - 1039 - 2840.749 = 216.251` | PASS |
| 历史 prepared 任务 12,884 ms、17 请求、3,050 ms、23.7% / 76.3% | `docs/why-jev-is-fast.md:61` | `flights-prepared-measurement.json:5/8/9/10`，计算 3,050 / 12,884 = 23.7%，差额 9,834 ms = 76.3% | PASS |

本地测量源文件的行号证据：

- `<workspace>/jev-ultrafast-main/docs/flights-measurement.json:2-7,103-104`
- `<workspace>/jev-ultrafast-main/docs/performance.md:3,28,32`
- `<workspace>/jev-ultrafast-main/docs/full-speed-measurement.json:8-15,177-178,313-314,759-760`
- `<workspace>/jev-ultrafast-main/docs/flights-prepared-measurement.json:5,8-16`

### 未能本地核实的数字

- Laya T4 单问题 P50 `32.8ms`、10 问题 `72.3ms`、50 问题 `337.4ms`：文档引用 Laya 外部 README/BENCHMARKS，但本地工作区没有 Laya 完整副本；`rg` 只找到 `docs/why-jev-is-fast.md` 自身和无关 lock 文件。
- Von `约18ms GPU` / `sub-25ms` / `sub-15ms`：文档引用 Von 外部 README 和仓库 description，但本地工作区没有 Von 完整副本；同样只能验证“文档明确标注了项目自报/口径不一致”，不能独立验证数字。
- `docs/why-jev-is-fast.md:6` 的“本报告只新建本文件，不修改源码；`docs/web-automation-research.md` SHA-256 保持不变”无法从 Git 历史独立验证；后者在本提交中是 `A`（新增），所以该句至少不能由提交历史直接证明。

**结论：PASS（本地 Jev 数字真实、未夸张、免责声明与本地证据一致）；Laya/Von 外部数字和该文件的“只新建本文件”声明列为未验证项。**

## 7. 文档承诺与代码实现一致性抽查

抽查了 8 条，均能在静态源码层找到对应实现。

| 文档承诺 | 文档位置 | 代码证据 | 结论 |
|---|---|---|---|
| OCR 返回 bounding box、中心点和 polygon | `docs/gap-remediation.md:29`；`docs/feature-coverage.md:50,121,204` | `src/windows_mcp/ocr/service.py:146-154` 构造 `bbox/center/polygon`；`ocr/service.py:600-610` 返回 lines/words；`tools/ocr.py:21-22,48` 暴露结果 | PASS |
| Web 支持 act-by-ref（click/type/select 等优先使用 snapshot ref） | `docs/gap-remediation.md:53`；`docs/feature-coverage.md:51` | `tools/web.py:66-67,83` 声明 ref；`tools/web.py:124-163` 把 ref 传到动作；`web/service.py:794-815` 解析 ref，`web/service.py:897-907` 执行 click | PASS |
| KeyDown / KeyUp | `docs/feature-coverage.md:214`；`docs/gap-remediation.md:13` | `tools/inputx.py:120-161` 注册工具；`inputx/service.py:573-591` 发送按下/抬起事件并维护按键状态 | PASS |
| power control（shutdown/restart/logoff/suspend/hibernate/abort） | `docs/feature-coverage.md:93,208`；`docs/gap-remediation.md:14` | `tools/systemx.py:242-258` 定义 power modes；`tools/systemx.py:316-331` 分发；`systemx/service.py:1088-1185` 调用关机/重启/挂起/取消 API | PASS |
| `windows-mcp doctor` | `docs/gap-remediation.md:18,207-219` | `src/windows_mcp/__main__.py:447-515` 定义 Click 命令、`--yes`、`--check-only`、发现/安装逻辑；`interpreter.py` 提供解释器发现和安装 | PASS |
| `NAVIGATE_*` | `docs/why-jev-is-fast.md:474` 附近的 agent 设计；`docs/gap-remediation.md:19` 的 agent 动作验证 | `agent/action_space.py:302-303` 从 goal 生成 `NAVIGATE_n`；`action_space.py:366-384` 纳入验证；`agent/service.py:323-330` 执行 URL 导航 | PASS |
| decision retry（OpenRouter 决策重试） | `docs/gap-remediation.md:19,229-230` | `agent/openrouter.py:282-309` 定义 `decision_attempts = 3`，对 `ValueError/RuntimeError` 做三次指数退避 | PASS |
| thread-local Session | `docs/gap-remediation.md:19,229` | `agent/openrouter.py:32-47` 使用 `threading.local()` 和每线程 `requests.Session` | PASS |

补充说明：

- `docs/feature-coverage.md:24` 对可选 OCR/Office 延迟导入的例外说明，与 `tools/__init__.py` 和 OCR/Office 服务代码一致。
- `docs/why-jev-is-fast.md:463` 明确承认 `LayaPolicy` 仍是 stub；`agent/laya.py:13-62` 确实在 `decide()` / `text()` 处抛出 `NotImplementedError`，没有把未实现能力伪装成已完成。
- 这些结论均为静态源码核对；没有凭据、Playwright 浏览器、可用 pytest/mcp 依赖，因此不能替代真实端到端执行。

**结论：PASS（静态一致性）。未进行运行时端到端验收。**

## 未验证项

1. **完整测试套件未运行**：可用的 `<workspace>\.venv` 没有安装 `pytest`；尝试运行测试得到 `No module named pytest`。因此无法独立复现仓库测试通过状态。
2. **完整工具注册未动态执行**：同一环境缺少 `mcp`/`starlette` 等运行依赖；动态注册探针在导入核心 `app` 模块时以 `ModuleNotFoundError: No module named 'mcp'` 失败。42 个工具数是 AST 静态统计和模块注册方式核对的结果，不是完整运行时握手结果。
3. **Laya/Von 外部数字未核实**：`32.8ms`、`72.3ms`、`337.4ms`、Von `18ms/sub-25ms/sub-15ms` 只出现在本仓库文档中；本地没有 Laya/Von 完整源码或 benchmark 产物，且本轮禁止联网。
4. **真实模型/浏览器/OCR/电源 E2E 未运行**：没有 TypeSafe/OpenRouter 凭据，没有 Playwright 浏览器/CDP 会话，没有 OCR 运行时 DLL 验证；电源和 doctor 安装路径也未实际操作，以免修改机器状态。
5. **`docs/why-jev-is-fast.md` 的“web-automation-research.md SHA 保持不变”无法从本提交独立证明**，且该文件在本提交中显示为新增。
6. **PostHog PHC key 的性质需安全负责人确认**：它是已存在的历史公开客户端项目 key，不在本 PR diff；本报告不把它当作新机密泄露，但严格口径下仍应留档说明。

## 最终裁决

- **核心实现真实性**：通过静态验收。42 个工具、可选包容错、OCR/Web/输入/电源/doctor/agent 的抽查实现均与文档承诺一致。
- **Jev 测量真实性**：本地可核的 Jev Ultrafast 数字全部对得上，没有发现夸大或编造。
- **交付阻断项**：`docs/why-jev-is-fast.md` 的 7 处作者本机绝对路径，以及严格工作树口径下的历史 PostHog PHC key 告警。
- **总体结论：不可交付，清除/泛化个人绝对路径后再复验；如安全负责人要求轮换或豁免 PHC key，还需处理该历史条目。**

---

## 复核后状态（归档时更新）

上面记录的是**初次验收时**的状态。归档进本 PR 时，阻断项已处理：

1. **本机绝对路径**：`docs/why-jev-is-fast.md` 中 8 处作者本机绝对路径已全部改为仓库相对路径；本目录下的两份复核报告也已脱敏。复扫仓库已无个人绝对路径。
2. **Jev fan-out 表述**：`docs/why-jev-is-fast.md` 结尾原来把 `done` / `guardrail` 也写成同一次请求里的独立问题，与源码不符——`model.py:88-93` 中 `DONE` / `BLOCKED` 只是 `operation` 问题的候选值，实际 fan-out 的是 `operation` 加每种 target-producing operation 的 `*_target`。该句已改正。

另外，上面"未验证项 1"记录完整测试套件未运行。归档前恢复依赖后已复跑：

- 命令：`uv run --frozen python -m pytest -q`
- 结果：**638 passed, 1 failed**
- 唯一失败：`tests/test_cli_legacy_flags.py::test_install_options_are_not_blocked_by_legacy_filter`，原因是测试宿主没有注册计划任务的权限（`Register-ScheduledTask` 的 `Action` 参数为空 / `HRESULT 0x80041003`），与本次新增代码无关。

其余未验证项（真实模型、Playwright/CDP、OCR 运行时、电源切换的端到端执行，以及 Laya / Von 等外部项目自报数字）依然成立。
