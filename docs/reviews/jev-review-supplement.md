# Jev 快速交互研究补充与事实核查

> 本文件是本地独立验收 / 复核记录，随 PR 一起归档以便追溯。所有个人机器绝对路径已脱敏为 `<workspace>/`（任务工作目录）与 `<repo>/`（本仓库检出目录）。

> 核查日期：2026-09-23（Asia/Shanghai）  
> 核查对象：`<workspace>/upstream-git/docs/why-jev-is-fast.md`（只读）  
> 本地证据根目录：
> - `<workspace>/jev-ultrafast-main/`
> - `<workspace>/Windows-MCP/`
>
> 本次没有联网、没有调用 TypeSafe、没有运行模型或浏览器基准；外部项目的 benchmark 只能按“原始项目自报 / 官方宣称”标注。本文没有修改 `upstream-git` 下任何文件。核查时 `why-jev-is-fast.md` 的 SHA-256 为：
>
> `FF9F9A53CEA02B3E9A03EF3AEFE6AB365DA69706AC37C48C71B8C0978BD79523`

## A. 把 Jev Ultrafast 的“怎么操控网页”逐条落到源码

以下行号均以本地副本 `jev-ultrafast-main` 为根目录。为避免混淆，先区分两个编号层：

- `snapshot.js` 的 `id: "e1"`、`"e2"` 是**动作候选编号**；同一个 DOM 节点如果同时支持 click 和 fill，会产生多个动作候选。
- `model.py:action_space()` 的 `[1]`、`[2]` 才是**模型看到的元素编号**；同一个真实 DOM 节点只占一个元素编号。

### A1. Observation 如何生成，元素表如何编号

1. 页面侧先建立“代码拥有的节点身份”缓存：`WeakMap` 保存 node → id，`Map` 保存 id → node；新节点逐个递增，断开的节点被清除。见 `jev_ultrafast/snapshot.js:3-8`。
2. 只扫描常见可交互选择器：`input/textarea/select/button/a[href]/summary/contenteditable/[role=...]`；过滤 password/file/hidden、不可见、disabled、越出 viewport 的节点。见 `jev_ultrafast/snapshot.js:9-11, 24-27, 55-60`。
3. 每个动作候选先保存真实 `node`、role、名称、当前值、几何位置和状态；原生 `<select>` 的每个未选 option 会生成独立的 select target。见 `jev_ultrafast/snapshot.js:61-80`。
4. 页面正文用 `TreeWalker` 只取视口内可见文本，最多 6,000 字符；动作候选最多保留 250 个。见 `jev_ultrafast/snapshot.js:82-92, 99-101`。
5. 页面返回 `marker`、`page_key`、`guards`、`fingerprint` 所需信息；marker 包含 document/URL/viewport/title/text/semantics 和表单状态。见 `jev_ultrafast/snapshot.js:44-53, 93-98`。
6. Python 侧 `action_space()` 以 `node` 为去重键做第二层编号：第一次遇到某个真实 node 时分配 `str(len(elements)+1)`；同一 node 的 click/fill/select 复用同一个 `index`。见 `jev_ultrafast/model.py:48-78`。
7. 每个 operation 有独立 target map：`CLICK`、`TYPE_TEXT`、`SELECT` 互不混用；原生 select 的 target 编码为 `元素编号:选项编号`。见 `jev_ultrafast/model.py:51-77`。
8. 最终 action 数组在页面侧被重新编号为 `e1...` 供执行器定位；这与模型元素表编号不是同一层编号。见 `jev_ultrafast/snapshot.js:96-101`。

### A2. 一次请求如何同时问 operation 和 target

1. `questions["operation"]` 的候选是所有可用 operation，加上 `DONE`、`BLOCKED`；`DONE/BLOCKED` 是 operation 选项，不是独立的 done/guardrail question。见 `jev_ultrafast/model.py:88-93`。
2. 对每一种 target-producing operation，构造一个 `<operation>_target` question；候选 map 只包含该 operation 兼容的真实元素。见 `jev_ultrafast/model.py:94-106`。
3. `body` 只包含一次 `model + state + questions`，随后执行一次 `POST https://api.typesafe.ai/v1/systemone`。见 `jev_ultrafast/model.py:107-120`。
4. 返回值先校验 operation；只有选中的 operation 对应的 target head 会被校验和消费。未选中的 target 结果不会执行。见 `jev_ultrafast/model.py:120-133`。
5. 设计文档把这一点明确写成 `Two decisions, one network round trip`；target question 的 premise 显式写明它假设的 operation，因为各 question 不能读取彼此答案。见 `docs/design.md:5-7`。

### A3. 如何执行动作：CDP、Runtime.evaluate 和真实 node identity

1. `Browser.__init__` 常驻 Browser Harness daemon，创建一个后台 tab，`Target.attachToTarget(..., flatten=True)` 得到一个 CDP session；没有每步新建 subprocess。见 `jev_ultrafast/browser.py:1, 20-28`。
2. `Browser.evaluate()` 走 `Runtime.evaluate(..., returnByValue=True)`；这是页面观察、freshness 检查和节点解析共用的通道。见 `jev_ultrafast/browser.py:35-42`。
3. `browser_operation({"operation": "act", ...})` 对 click/fill/select 先在页面内执行 `window.__jevFast.nodes.get(action.node)`，确认节点仍连接、未 disabled、可见、未 readOnly，并重新取 bounding rect。见 `jev_ultrafast/browser.py:135-160`。
4. 点击使用 `Input.dispatchMouseEvent(mousePressed/mouseReleased)`；填充先点击，再发 select-all key event，最后用 `Input.insertText` 输入；滚动使用 `Input.dispatchMouseEvent(mouseWheel)`。见 `jev_ultrafast/browser.py:138-186`。
5. 原生 select 在页面内验证 option 后执行 `e.value=...` 并派发 `input/change`；这部分不是 Playwright selector，而是基于真实 node identity 的 JS 执行。见 `jev_ultrafast/browser.py:152-159`。
6. 默认 observation 是 `Runtime.evaluate(READ_STATE)`；只有 `screenshot=True` 才额外调用 `Page.captureScreenshot`。见 `jev_ultrafast/browser.py:188-193`。
7. 真实 node identity 不在模型输出里；模型只输出 element/target index，代码再用 `nodes` map 解析。见 `jev_ultrafast/snapshot.js:3-8` 和 `jev_ultrafast/browser.py:143-151`。

### A4. 如何等待与判断 freshness

1. 普通动作后首次观察最多等两个 `requestAnimationFrame` 或 50ms；editable ARIA combobox 额外等待可见 option，最多 200ms。见 `jev_ultrafast/browser.py:44-74`。
2. `observe()` 对 `StalePage` 最多重试 10 次，每次间隔 20ms；这不是重放动作，只是重新读页面。见 `jev_ultrafast/browser.py:77-86`。
3. `fresh(page)` 对 click/select 比较 `page_key` 和该节点的 `guard`；其他动作比较完整 `marker`。guard 包含 URL/viewport、安全表单值、节点身份、role/name/value/状态以及附近 form/dialog/row 上下文。见 `jev_ultrafast/browser.py:88-98`、`snapshot.js:44-54, 93-98`。
4. `act()` 在真正输入前再次调用 `fresh()`；`WAIT` 只 sleep 100ms；动作完成后把 `after_input` 留给下一次观察做有界等待。见 `jev_ultrafast/browser.py:100-107`。
5. 主循环在 predict 前检查 freshness，在 `act` 前用 page fingerprint 再检查，fill 在调用 text helper 前再次检查。见 `jev_ultrafast/agent.py:65-77, 86-117`。
6. 页面变化导致的决定不会被执行；`DONE/BLOCKED` 也要 freshness 通过才结束。见 `jev_ultrafast/agent.py:88-100`。
7. 执行后重新 observe，比较前后 fingerprint；连续三次非 WAIT 动作无可见变化才判 blocked。见 `jev_ultrafast/agent.py:120-158`。

### A5. 文本输入如何走 text helper

1. 只有 `action["kind"] == "fill"` 才进入文本 helper；click/select/wait/done 不调用生成式文本模型。见 `jev_ultrafast/agent.py:105-115`。
2. helper 输入由 goal、当前 field 的 label/role/value、当前页面前 6,000 字符、最近 6 个动作组成。见 `jev_ultrafast/model.py:151-157`。
3. `field_text()` 使用 OpenAI-compatible `/chat/completions`；prompt 要求返回且只返回 `{"text": "..."}`。见 `jev_ultrafast/model.py:160-186`、`jev_ultrafast/questions.py:21-24`。
4. 输出必须是恰好一个 key、非空字符串、长度不超过 2,000；否则抛错并且不输入。见 `jev_ultrafast/model.py:187-193`。
5. stale retry 只有在整个 helper context 完全相同时才复用 `pending_text`，成功 mutation 后清空；也就是说并非模型层缓存，而是代码层对同一输入的幂等复用。见 `jev_ultrafast/agent.py:109-118`。


---

## B. 8~12 条最硬的机制性证据

分级说明：

- **代码可证**：本地源码直接可见。
- **测量文件可证**：本地 JSON/报告可复现地给出边界和数据。
- **官方/项目自报**：只能证明作者声称，不能证明内部实现或通用性能。

### B1. 类型化选择会阻止无效 JSON/越界动作

`validate_choice()` 强制 choice 位于候选集合、概率 key 完全匹配、概率和为 1、choice 为最大概率，否则在动作前抛错。这能证明“类型和动作空间边界”有效，但不能证明语义选择正确。证据：`model.py:30-45, 120-133`。**代码可证。**

### B2. operation 和 target 是一次请求内的 fan-out

`questions` 同时包含 operation 和每个 operation 的 target head；只有一个 POST；executor 只消费选中的 operation 对应的 target。证据：`model.py:81-133`、`docs/design.md:5-7`。**代码可证。**

### B3. 模型不能输出 selector、坐标或 JavaScript

模型只能选择 `element index` 或 `元素编号:选项编号`；真实节点、坐标和动作执行仍留在代码里。证据：`model.py:48-78, 120-133`、`snapshot.js:55-80, 101`、`browser.py:143-186`。**代码可证。**

### B4. 一轮 observation 只做一次页面侧状态读取

默认 observation 调用一次 `Runtime.evaluate(READ_STATE)`，返回整张元素表和文本；截图额外调用才发生。证据：`browser.py:12-14, 44-86, 188-193`、`snapshot.js:1-107`。**代码可证。**

### B5. 浏览器/协议热路径常驻

Browser Harness daemon、后台 tab、flattened CDP session 在 Agent 生命周期内复用，而不是每步启动进程或重新建 tab。证据：`browser.py:1, 20-28`、`agent.py:12-24, 167-174`。**代码可证。**

### B6. 输入前做实时 geometry、enabled、visibility 和 occlusion 检查

执行前重新解析真实 node，检查 connected/disabled/inert/readOnly/visibility、重新取中心点和 `elementFromPoint()`，覆盖或失效时拒绝动作。证据：`browser.py:135-165`。**代码可证。**

### B7. freshness 是语义比较，不是 DOM mutation 计数

freshness 比较 marker/page key/guard 以及附近上下文；动画导致的无关 DOM 变更不会天然让决定失效，但相关表单/节点/对话变化会使决定 stale。证据：`snapshot.js:44-54, 93-98`、`browser.py:88-98`、`docs/design.md:17`。**代码可证。**

### B8. 等待是有界的状态等待

普通动作最多 2 rAF/50ms，autocomplete 最多 200ms，显式 WAIT 100ms；没有固定长 sleep，也不会 fast-forward 真实网络加载。证据：`browser.py:44-71, 103-104`、`docs/design.md:21`。**代码可证。**

### B9. mutation 不做 transport retry，decision 一次消费

代码在 mutation 前先清空 `state["decision"]`，并避免对浏览器 mutation 做通用重试，降低双击/重复输入风险。证据：`agent.py:86-96, 116-118`、`docs/design.md:19`。**代码可证。**

### B10. 文本生成是决策热路径之外的低频分支

只有 `TYPE_TEXT` 才调用 text helper，且输出经过严格 JSON 校验；失败时不会猜值或执行输入。证据：`agent.py:105-117`、`model.py:160-197`、`questions.py:21-24`。**代码可证。**

### B11. Google Flights 录像测量支持“单步快、整任务仍多步”的结论

`flights-measurement.json` 记录任务 `7,073ms`、17 次 decision 请求、decision 中位数 `178ms`、decision 合计 `3,720ms`、11 次 browser action（其中 1 次 WAIT）、两次 text helper 分别为 `581ms/346ms`。证据：`docs/flights-measurement.json:2-6, 16-20, 47-49, 115-235`。**测量文件可证。**

### B12. 配对测量支持“减少协议调用不等于任务同比变快”

`full-speed-measurement.json` 的 3 对交替运行中，baseline 中位 `9,450ms / 1,092 CDP calls`，candidate 中位 `7,092ms / 101 CDP calls`；两者各自 3/3 通过。指标下降约 90.8%，任务时间只下降约 25%。证据：`docs/full-speed-measurement.json:4-16, 133-170, 871-894`、`docs/performance.md:7-24`。**测量文件可证。**

### B13. 小 fixture 的 1.1~1.3 秒不能外推成真实网页速度

本地 fixture 三次运行分别为 `1,086/1,242/1,311ms`、每次 6 次 decision 请求，并明确排除 Chrome startup 和 initial page load；这是作者自建页面，不是公开网站基准。证据：`docs/measurement.json:2-5, 18-71`。**测量文件可证，但适用边界很窄。**

### B14. 官方/外部数字与本地证据的边界

以下只能作为官方宣称或项目自报，不能从本地 jev 源码推出 Jev 的内部模型结构：

- TypeSafe 的 `parallel sampler`、`all outputs in a single query`、`no text generation`、`约 100ms`、`70–500ms`：原文档 `why-jev-is-fast.md:34, 112-138` 引用的 TypeSafe 文档/博客；本次没有本地官方 model card 或权重复核。
- Laya/Von/SemIf 的 encoder、logits 和 benchmark，以及 Laya-MLX/CoreML 的 5–40ms 数字：原文档 `why-jev-is-fast.md:50, 64-68, 140-159, 416-424` 引用的是外部 repo/README；本地副本没有这些 repo，无法逐行复核。
- `ego-jev` 的 1.0–1.5s/次、每步新进程 250–350ms 等：原文档 `why-jev-is-fast.md:35, 342, 350, 444`；属于外部项目自报或作者进一步推断。
- `screenpeek`、`UI-TARS`、`CUA-S1`、`jev-browser`、`playjev` 的数字同样是外部项目/HN 自报，应保留原有“自报/未控制”标签。见原文档 `why-jev-is-fast.md:38, 69-72, 425-431, 568`。


---

## C. 缺口检查与可疑断言

### C1. 原文档已经覆盖的同类实现

原文档的覆盖面其实不窄：Jev Ultrafast、browser-harness、Laya、Laya-MLX、Laya-CoreML、Von、SemIf、jev-browser、playjev、ego-jev、jev-panerelay、screenpeek、windows-harness、UI-TARS、CUA-S1、HN/Juejin/kuhung 都有单独位置，见 `why-jev-is-fast.md:415-431, 556-570`。它也没有伪造 Reddit/X/公众号正文，反而明确标出了 Reddit/DDG/X 的证据缺口，见 `why-jev-is-fast.md:498`。

### C2. 我认为真正应补的两个对照案例

1. **`browser-use/browser-use`：`https://github.com/browser-use/browser-use`**  
   原文档只在 Jev Ultrafast 的 README 链接里带到它，见 `jev-ultrafast-main/README.md:142`，没有把它列入同类对照表。它补的是“普通 browser-use 主线 agent loop”这一基线：更适合说明 Jev 改变的不是浏览器控制层，而是把开放式模型决策替换成有限 option/typed decision。没有这个基线，读者容易把 Jev Ultrafast 的 CDP 优化误认为 Jev 模型本身的速度。

2. **`CursorTouch/Windows-MCP`：`https://github.com/CursorTouch/Windows-MCP`**  
   本地 `Windows-MCP/README.md:4` 给出该 URL，原文档虽然在 `why-jev-is-fast.md:448-463` 分析了 Windows-MCP，但没有把它列为“实现案例”。Windows-MCP 已经有 `agent/action_space.py`、`agent/jev.py`、`tree/service.py`、`tools/act.py`，它补的是桌面/UIA 侧的其实践：稳定 runtime 身份、UIA cache、单轮 Act、Playwright/CDP WebAgent，和 Jev 的浏览器路径可以形成同构对照。

补一个相关但不是 Jev 决策模型的本地案例即可：`https://pypi.org/project/windows-use/`（本地 `Windows-MCP/README.md:31`），它说明 Windows-MCP 已被上层 agent 消费；它不应被拿来和 Jev 单步 latency 直接比较。

### C3. 看起来无出处、被过度外推或条件不匹配的断言

1. **最明确的一处错误：把 done/guardrail 也算进一次 fan-out。**  
   原文档 `why-jev-is-fast.md:590` 写“operation、target、done、guardrail 等问题在一次请求内 fan-out”。本地 `model.py:91-106` 实际只有 `operation` 和 `<operation>_target`；`DONE`/`BLOCKED` 只是 operation choices，见 `model.py:88-93`，没有独立 done question，也没有 guardrail question。这个表述应改成“operation + operation-specific target heads；done/blocked 是 operation 选项；其他项目可能另有 guardrail”。这是原文档最需要修的一处。

2. **“本地文本模型”用词不准确。**  
   原文档 `why-jev-is-fast.md:15` 说“本地文本模型”。实际 `field_text()` 默认使用 OpenAI-compatible `/chat/completions`，可配置 DeepSeek/OpenRouter 等远程 endpoint，证据：`model.py:160-170`、`.env.example:3-7`；录像使用的是 `inception/mercury-2.5`，见 `flights-measurement.json:18-19`。准确说法应是“独立的、低频的文本 helper（通常在本地代码中调用，但模型 endpoint 可远程）”。

3. **`ego-jev` 的 2ms/13–16ms/110–130ms/788–1005ms 是外部项目自报，原报告没有给出对应本地的可复核 artifact。**  
   见 `why-jev-is-fast.md:350`。原文档把它和本地 Jev 源码放在同一段，读者可能误以为是本地测量；应改名称为“ego-jev README/项目自报”，或者明确缺测量脚本和样本量。

4. **“旧版主要慢点正是这里”是因果推断，不是单独消融实验。**  
   本地 measurement 能证明 baseline 有很多 `Accessibility.getFullAXTree` 和 `DOM.resolveNode` 调用，见 `full-speed-measurement.json:133-149`，但不能单凭总数证明它们是任务慢的唯一主要原因；这是与原文档 `why-jev-is-fast.md:350` 需要区分的。更稳妥的说法是“协议调用数和部分耗时显著一致地下降”。

5. **原文档的 Laya/Von/SemIf 1.0–5.3s、MLX/CoreML 5–14ms、Von sub-15/sub-25/18ms 都是外部 repo 自报。**  
   它们在原文档 `why-jev-is-fast.md:62-68, 152-159` 有链接，但当前本地只有 Jev 和 Windows-MCP 副本，无法离线验证精确行号、机器配置或脚本；这不是“编造”，但不应被写成本地可证事实。

6. **HN/掘金/kuhung 的社区材料仍需保留二手属性。**  
   原文档 `why-jev-is-fast.md:98, 496-498, 567-573` 已部分说明；建议在表中给每条增加 `official / repo self-report / third-party anecdote` 标签，避免读者将论坛延迟当成受控 benchmark。


---

## D. 可直接迁移到 Windows-MCP 的最小改动清单

以下改动均映射到当前 `Windows-MCP/src/windows_mcp/`，按收益/风险排序。它们是设计建议，不是本次已执行修改。

### D1. 把逐元素状态读取合并进 `_JS_SNAPSHOT`

- **落点**：`web/service.py:40-132`、`web/service.py:712-766`。
- **改法**：把 `checked/selected/expanded/contenteditable/input_type/options` 一次性放进页面侧 `_JS_SNAPSHOT`，删除 `_snapshot_data_sync()` 中每个 row 的 `locator.evaluate()`。
- **收益**：当前 150 个元素可能产生 150 次额外 locator evaluate；合并后最接近 Jev 的“一轮一次 page-side snapshot”，会直接减少协议往返和元素失联窗口。
- **风险**：大 `<select>`/异常 DOM 可能让序列化变慢或抛错；需要对 options 数量设上限、逐节点 try/catch，并保留 token→locator 的旧路径作为 fallback。

### D2. 增加 operation-specific target heads 的回归测试

- **落点**：`agent/action_space.py:277-334`、`agent/service.py:228-265`、`tests/test_agent_action_space.py`。
- **改法**：增加三类断言：同一 node 只占一个 element index；`CLICK/TYPE_TEXT/SELECT` 的候选互不串线；只能消费被选中 operation 的 target。
- **收益**：锁住 Jev 最重要的“一次请求 + 操作/目标兼容”不变量，避免未来加入 Windows 动作时回退成串行调用。
- **风险**：需要 mock provider 快照，不能依赖真实 TypeSafe；测试应离线运行。

### D3. 用分阶段 profiling 覆盖完整 agent loop，而不是只看 tree/snapshot

- **落点**：`agent/service.py:372-667`、`web/service.py:266-298, 712-791`；现有 `WINDOWS_MCP_PROFILE_SNAPSHOT` 见 `tree/service.py:27-28, 147-149`、`desktop/service.py:54-55, 113-164`。
- **改法**：在历史条目中增加 `decision_ms`、`fresh_snapshot_ms`、`text_helper_ms`、`action_ms`、`post_snapshot_ms`、`retry/stale` 计数；默认只聚合，不记录页面正文。
- **收益**：先分清慢在模型、CDP、UIA 快照、加载还是重复 snapshot；否则会误把“CDP call 数”当成任务耗时。
- **风险**：日志量和敏感信息；只存毫秒数、计数和哈希，不存 full page text/credential。

### D4. 以 semantic guard 补充 full fingerprint，降低误 stale

- **落点**：`web/service.py:40-132, 779-791`、`agent/service.py:460-461, 602-603`。
- **改法**：snapshot 额外返回 per-target `guard` 与 document/URL/viewport 的 marker；click/select 比较目标 guard，fill/wait/done 使用全 marker；保留现有 full fingerprint 作为动作后 change detector。
- **收益**：避免动画、无关 ticker、隐藏节点导致整轮 decision 重做；这是 Jev `fresh()` 的主要工程收益。
- **风险**：scoped guard 可能漏掉对目标有影响的远处变化；必须保留“full fingerprint 变化就重新观察”的保守 fallback，或在低置信度时强制 full check。

### D5. 把文本生成结果按完整 helper context 缓存

- **落点**：`agent/service.py:351-365, 574-600`。
- **改法**：以 `canonical_json(context)` 或 `(goal, field, page text, recent actions)` 为 key 缓存 `(text, helper_meta)`；只在页面 fingerprint 未变且同一 field 重试时复用，成功执行后清除。
- **收益**：当前 text helper 通常要 300~1,000ms；stale retry 若重新生成会重复支付整段延迟。
- **风险**：context 不完整会复用错误值；不要只按 field label 缓存，必须包含完整输入和页面状态版本，并在 mutation 成功后无条件清空。

### D6. 把固定 WAIT 500ms 换成有界条件等待

- **落点**：`agent/service.py:331-333`、`web/service.py:1130-1165`；桌面已有更好的 `tools/act.py:743-855` 的 `wait_for_change(probe, timeout, interval)`。
- **改法**：WebAgent 的 `WAIT` 默认 100ms 起步，仅在目标/结果仍未出现时按小步轮询到配置上限；优先等可见 suggestion/结果元素/URL 变化，不固定 sleep 500ms。
- **收益**：减少固定空等，同时避免在 autocomplete/结果尚未出现时过早决策。
- **风险**：过短会导致在加载页上多一次无效决策；必须使用显式 condition 和 hard cap，不得跳过真实网络加载。

### D7. 让 Laya 的 `available` 反映真正可执行，而不是只检查目录和 Node

- **落点**：`agent/laya.py:30-50`、`agent/policy.py:106-116`。
- **改法**：在 `decide()` 真正实现前，让 `available` 返回 False，或增加显式 `allow_stub`；不要让 `auto` 在目录+Node 存在时选中一个必然 `NotImplementedError` 的后端。
- **收益**：消除“自动选到不可用本地模型”的运行时失败；本地模型路线可作为显式实验开关保留。
- **风险**：暂时失去 `auto` 的 Laya 选择；这与真实可用性一致，待 ONNX/Node bridge 完成后重新打开。

### D8. 为 UIA/桌面 action 传递稳定 runtime identity 和 freshness version

- **落点**：`tree/service.py:843-865`（tree traversal/cache）、`tree/service.py:976-986`（focus event 已使用 runtime id）、`tools/act.py:707-813`（refresh → resolve → execute → verify）。
- **改法**：在 snapshot 的交互节点中记录 UIA RuntimeId（或等价的稳定 fingerprint），Act 执行前重新校验同一 runtime id/关键属性，再执行；坐标仅作为最后的 hit-test fallback。
- **收益**：减少 UIA 重排后按名称/坐标误点，缩小“模型看到的节点”和“实际执行节点”之间的窗口。
- **风险**：部分 UIA provider 的 RuntimeId 不稳定或缺失；需要 fingerprint fallback、provider 能力标记和失败时不执行。

---

## E. 核查结论

1. **A 部分可完全由本地源码复核。** Jev Ultrafast 的核心闭环确实是 `snapshot.js` 生成 indexed DOM state → `model.py` 一次请求返回 operation + operation-specific target → `browser.py` 通过真实 node identity + CDP/JS 执行 → marker/guard 判断 freshness → fill 分支才调用 text helper。
2. **B 部分最硬的机制是“有限动作空间 + 单次 fan-out + 代码持有真实节点 + 有界观察”。** 7.073s 的任务时间和 9.450s→7.092s 配对数据只能证明该实现的一组测量结果，不能证明 Jev 内部架构或普适性能。
3. **C 部分的主要缺口不是更多社媒项目名，而是两个对照：`browser-use/browser-use` 和 `CursorTouch/Windows-MCP`。** 外部 Laya/Von/SemIf 等数字不能在本地复核，必须继续标成 repo 自报。
4. **最需要修的一处**：原文档 `why-jev-is-fast.md:590` 的“done、guardrail 等问题一次 fan-out”。本地代码证明实际发送的是 `operation + operation-specific target`；`DONE/BLOCKED` 是 operation 的候选值，不是独立 question。
