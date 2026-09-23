# 为什么 Jev / System One 这类快速浏览器 / GUI 决策能这么快？

> 调研快照：2026-09-23（Asia/Shanghai）。  
> 证据范围：`browser-use/jev-ultrafast` 本地完整副本、GitHub API 元数据、TypeSafe 官方 Markdown / `llms-full.txt`、Laya / Von / SemIf 等开源实现源码或 README、Hacker News / Dev.to / 掘金 / 独立中文博客的公开材料。  
> 本次没有运行任何模型、浏览器基准或 TypeSafe API，因此下文所有延迟都明确标注为“官方宣称”“仓库自报实测”或“第三方公开实测”，没有冒充本次实测。  
> 本报告只新增文档，不改动任何源码。

## 1. TL;DR

Jev 这类方案快，不是因为一个单点魔法，而是把“让大模型自由生成一段动作”拆成了一个**窄、结构化的决策问题**：

1. **模型层**：从“生成自然语言 / JSON / 坐标”改成“在有限选项上打分并返回概率分布”。TypeSafe 官方称 Jev 使用 parallel sampler，所有输出在一次查询里产生，不做逐 token 文本生成，也不需要从生成文本里解析动作；独立开源的 Laya、Von、SemIf 用源码证明这种实现可以做到一次前向返回多个问题的答案。
2. **动作层**：Jev Ultrafast 把页面变成带编号的元素表，一次 TypeSafe 请求同时问 `operation` 和每个 operation 的 `target` 候选；代码只消费与选中 operation 对应的 target head。这样把“先决定做什么、再看页面决定点哪里”的串行模型往返压成一次 API 往返。
3. **感知层**：默认不截图，直接读 DOM / ARIA 的名称、角色、值、状态、可见文本和可执行目标。模型看到的是小而有界的文本元素表，不再是截图、图像 token、视觉编码和坐标 grounding。
4. **执行层**：浏览器常驻、单个 CDP 会话、一轮一次 DOM snapshot、代码持有真实 DOM 节点身份，动作前重新取几何并做 enabled / visible / occlusion 检查，动作后做 bounded wait；本地文本模型只在 `TYPE_TEXT` 时调用。
5. **端到端层**：把长任务拆成很多个 100ms 级小决策后，单步很快，但整任务仍会被多次决策、文本生成、页面网络请求、结果加载和显式等待累积。Jev Ultrafast 的 Google Flights 任务为 **7.073s**，其中 17 次 Jev 请求合计 **3.720s**、两次文本生成合计 **0.927s**；这不是一次 7 秒的模型推理，而是一个完整多步浏览器任务。

一句话：**Jev 快在“把开放式生成改成有界的结构化选择，并把多个决策合并进一次并行请求”；Jev Ultrafast 快在“只把必要的信息给模型，把真实执行留在确定性的 CDP / DOM 代码里”。**

## 2. 证据分级与关键判断

### 2.1 证据等级

- **官方宣称**：TypeSafe 文档、官方发布博客、模型页。只代表厂商口径。
- **仓库自报实测**：`jev-ultrafast/docs/*measurement*.json`、Laya `BENCHMARKS.md`、MLX / CoreML benchmark、Von README 等。它们有可复现产物，但仍是项目维护者自己的测试。
- **第三方公开实测**：例如 SemIf 在同模型上比较 direct logits 与自回归 JSON；Hacker News 上的 CUA-S1 展示帖；中文博客 `jev.kuhung.me`。
- **待核实 / 不采信**：没有测量边界、没有样本量、没有可复现脚本的性能口号，尤其是跨方案但条件不一致的数字。

### 2.2 结论核验表

| 流行说法 | 核验结果 | 证据 |
|---|---|---|
| Jev 是非自回归、一次前向输出结构化决策 | **API 行为和开源同构实现支持；Jev 内部确切架构未公开**。TypeSafe 官方称 parallel sampler、all outputs in a single query、no text generation；Laya / Von / SemIf 的源码可证明这类实现确实是一次前向打分。 | TypeSafe launch blog；[Laya common.py](https://github.com/NandhaKishorM/laya/blob/main/laya/common.py#L89-L136)、[Laya agent.py](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py#L265-L368)；[Von option_marker.py](https://github.com/wfzyx/von/blob/master/src/von/models/option_marker.py#L37-L72) |
| TypeSafe 官方宣称 100ms 级 | **官方文档确有此说法**：“Most queries complete in about 100 ms”；发布博客给的是更宽的 **70–500ms**。 | [how-to-build-with-system-one](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md)；[launch blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev) |
| Jev Ultrafast 每轮约 100ms | **单任务实测中位数是 178ms/请求**，且大任务里的 Jev 调用可达到 1.0–1.5s。178ms 已包含托管 API 和网络，但不能拆出纯模型时间。 | [performance.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance.md)；[ego-jev README](https://github.com/jiangkoumo/ego-jev) |
| operation + target 一次请求 | **Jev Ultrafast 代码已验证**：一次 POST 的 `questions` 字典包含 operation 及 operation 对应的 target heads，代码只执行匹配 target。 | [model.py L81-L148](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L81-L148)；[design.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/design.md) |
| 默认不使用截图 | **已验证**：`Agent` 默认 `screenshots=False`；模型请求只发送 `url/title/text/elements`；截图只在 inspector、录像或显式开启时抓取。 | [agent.py L13-L23](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/agent.py#L13-L23)；[model.py L107-L115](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L107-L115)；[browser.py L188-L193](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L188-L193) |
| “不用截图”一定比视觉路线快 | **架构上很可能，但本次没有找到条件一致的端到端对照实验**。能证明的是截图路线多出 capture / encode / image tokens / VLM / grounding 几层；不能据此编造一个跨方案倍数。 | TypeSafe 说明 Jev 只接受文本、不支持图像；screenpeek 的“0.1s/步”和“7 vs 约1000 tokens”是项目自报；UI-TARS README 只证明它使用 VLM 和截图，没有给端到端延迟 |
| 浏览器协议调用 1092 → 101 是 Jev Ultrafast 官方测量 | **已验证为仓库自报实测**。它说明旧路径反复读 AX tree / resolve 大量 DOM 节点；新路径一轮一个直接 DOM snapshot。协议调用下降约 90.8%，但任务时间只降 25%，说明网络 / 页面加载 / 等待也占大头。 | [performance.md L7-L24](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance.md#L7-L24)；`full-speed-measurement.json` |
| “零幻觉” | **只能理解为输出类型不会越出给定选项，不等于判断永远正确**。Jev 可能在候选内选错；TypeSafe 自己也提醒 calibrated probability 不保证单个答案正确。 | [TypeSafe System One](https://docs.typesafe.ai/concepts/system-one.md)；[Confidence](https://docs.typesafe.ai/confidence.md) |
| “33ms Jev” | **这是开源 Laya 的数字，不是 TypeSafe 托管 Jev 的数字**。Laya README 同时给出 32.8–39.5ms 的 T4 测量和 7.2ms/question 的批量口径。 | [Laya README](https://github.com/NandhaKishorM/laya)；[Laya BENCHMARKS.md](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md) |
| “视觉路线每步 10–25s” | **未核实且不应作为通用事实**。`jev-panerelay` README 有此口号，但没有测量方法、样本量或可复现数据；视觉方案的延迟高度依赖模型、分辨率、网络和是否多轮。 | [jev-panerelay README](https://github.com/hdkiller/jev-panerelay) |
## 3. 延迟分解：单步、整任务、模型、网络、浏览器

### 3.1 先看分层结论

- TypeSafe 的 **70–500ms** 是托管 API 的端到端口径之一；官方文档最常见的一句是 **约 100ms**。两者都没有把“模型计算”和“网络”拆开。
- Jev Ultrafast 的 17 次 Jev 请求合计 **3,720ms**，中位数 **178ms/请求**，总任务 **7,073ms**。所以“ms 级单决策”和“秒级多步任务”可以同时成立。
- 在同一个 Jev Ultrafast 任务中，两次文本生成合计 **927ms**；如果任务大量填写文本字段，生成模型会重新成为主要瓶颈。
- 开源 Laya 的 T4 单问题 p50 是 **32.8ms**，但 10 问题批量是 **72.3ms**，50 问题批量为 **337.4ms**；这说明“并行评分”避免的是模型调用次数，不是输入 prefill 的免费。
- MLX / CoreML 的 5–14ms 是**本地短问题、已加载模型、已预热**的推理口径，不包含下载、加载和浏览器控制。

### 3.2 主延迟分账表

| 场景 / 来源 | 模型或推理 | 网络 / API | 浏览器 / 页面 / 等待 | 端到端 | 证据类型与边界 |
|---|---:|---:|---:|---:|---|
| TypeSafe 官方典型查询 | 与网络合并，未拆分 | 与模型合并，未拆分 | 不适用 | **约 100ms** | 官方宣称；没有说明 state 大小、区域、模型版本和是否包含冷启动 |
| TypeSafe 官方发布页 | 与网络合并，未拆分 | 与网络合并，未拆分 | 不适用 | **70–500ms** | 官方宣称；同一页还给出“最多 193.6× 更快 / 444.6× 更便宜”，但明确说这是偏高端的工作流口径，不是受控基准 |
| Jev Ultrafast 真实 Google Flights 录像 | Jev 请求 17 次，合计 **3,720ms**，中位 **178ms/请求** | 包含在 3,720ms 内，无法拆分 | 文本生成 2 次共 **927ms**；其余 **2,426ms** 包含浏览器动作、观察、加载、等待和未归因开销 | **7,073ms** | 仓库自报实测；计时从首次 page observation 之后开始，初始导航与最终独立验证不计入 |
| Jev Ultrafast 交替配对中的优化版第 3 次 | Jev 决策 **2,996ms**（16 请求，中位 182.5ms）；文本 helper **1,039ms** | 包含在 2,996ms 与 1,039ms 内 | CDP 方法耗时求和 **2,840.7ms**；剩余 **216.3ms** | **7,092ms** | 仓库 `full-speed-measurement.json`；CDP 求和不等于所有浏览器后台渲染时间 |
| Jev Ultrafast 历史 prepared 航班任务 | 17 次请求合计 **3,050ms**（23.7%） | 包含在 3,050ms 内 | 浏览器、截图、加载、等待等合计约 **9,834ms**（76.3%，由 12,884−3,050 推得） | **12,884ms** | 仓库历史测量；该版本使用 5 步准备式 goal，文本不来自 LLM |
| SemIf：同一 Qwen3.5-4B，21 个二分类 | **1.023s** direct logits / **0 output tokens** | 本地 GPU，无 API | 不适用 | **1.023s** | 第三方项目自报实测；同一模型、同一 state、同一 criteria，另一个 arm 是自回归 JSON |
| SemIf：自回归 JSON 基线 | **5.332s** / **111 output tokens** | 本地 GPU，无 API | 不适用 | **5.332s** | 同上；首 token 中位 0.489s，完成数组比 direct logits 慢 **5.21×** |
| Laya T4（官方 README + repo 内 benchmark） | 单问题 p50 **32.8ms**；英文 checkpoint **39.5ms**；10 问题 **72.3ms**；50 问题 **337.4ms** | 本地 GPU，无网络 | 不适用 | 同上 | 项目自报，测试为 Tesla T4；批量口径约为 **7.2ms/question** |
| `@receptron/laya` Node/ONNX | 3 个问题批量约 **140ms** | 本地 CPU，无网络 | 不适用 | **约 140ms** | 项目自报；Apple Silicon CPU，暖机后；1.7GB fp32 权重，首次加载另计 |
| Laya-MLX，短问题 | Laya 421M P50 **13.42ms** / P95 13.92ms；Multilingual 322M P50 **7.39ms** / P95 7.79ms | 本地 Apple Silicon，无网络 | 不适用 | 同上 | 仓库自报；模型加载排除，50 问题用 batch 64 |
| Laya-CoreML / ANE，短问题 | ANE FP16 P50 **4.98ms** / P95 5.31ms；1024-token 请求约 **91.7ms** | 本地 Apple Silicon，无网络 | 不适用 | 同上 | 仓库自报；短问题边界为 96 tokens，加载与 warmup 排除 |
| Von | README 表格给出约 **18ms GPU**；标题宣称 **sub-25ms**；仓库 description 当前宣称 **sub-15ms** | 本地 GPU，无网络 | 不适用 | 同上 | 项目自报且自身口径不一致，需以本地复测为准 |
| screenpeek：本地 OCR + 无障碍标签 | 约 **0.1s/step** | 无云 API | 包含在 0.1s 内 | **约 0.1s/step** | 项目自报；不是浏览器 DOM benchmark，不可直接和 Jev API 比较 |
| `Ying-Kai-Liao/jev-browser` | 每次 Jev 调用约 **300ms**；多数步骤 2–4 次调用 | 包含在 Jev 调用内 | Playwright 执行另计 | 5 步 checkout 约 **14s** | 项目自报；包含云端 Jev，不是纯模型 latency |
| `jiangkoumo/ego-jev` | Jev 约 **1.0–1.5s/次**；同任务大模型约 **1.6–4.5s/次** | 包含在调用时间内 | HN 任务 1.916s；表单 3.607s；维基搜索 3.634s | 见左 | 项目自报；说明大 state / 真实浏览器场景下，Jev 仍可能落到秒级 |
| UI-TARS Desktop | VLM / 多模态，未见端到端 latency 数字 | 未见拆分 | 官网 README 明示 Vision + Screenshot | **未核实** | 不能凭架构编造和 Jev 的倍数；只能确认它走视觉模型路线 |

### 3.3 Jev Ultrafast 的数字具体怎么读

录像任务的机器可读证据 `flights-measurement.json` 给出：

- `elapsed_ms: 7073`
- `decision_requests: 17`
- `decision_median_ms: 178`
- `decision_total_ms: 3720`
- `browser_actions: 11`，其中 `wait_actions: 1`
- 文本 helper 两次：Zurich **581ms**、London **346ms**
- 总输入 token **90,558**，总输出 token **6,325**
- 因此平均每个 Jev 请求约 **5,327 input tokens** 和 **372 output tokens**。这说明 Jev 省掉的是自由文本生成 / JSON 解析 / 重试循环，不是“模型看不见大输入”或“任何 output token 都为零”。
- 搜索在 **5,217ms** 执行，最终 DONE 在 **7,073ms**；最后约 **1,856ms** 主要花在 Google 结果加载、页面状态变化和最终完成判断上。

配对测量 `full-speed-measurement.json` 中的优化版第 3 次则提供更完整的分账：

- 任务总时间：**7,092ms**
- Jev 决策：**2,996ms**（42.2%）
- 文本 helper：**1,039ms**（14.7%）
- CDP 方法耗时求和：**2,840.7ms**（40.1%）
- 未归因残差：**216.3ms**（3.0%）

CDP 明细主要来自 `Runtime.evaluate` 71 次、2,483.5ms，以及鼠标/键盘/文本输入调用。它和旧版基线相比，旧版另有 `Accessibility.getFullAXTree` 30 次 1,382.7ms、`DOM.resolveNode` 828 次 1,792.6ms。这解释了为什么“协议调用数下降 90.8%”是真实的，但任务只快 25%：优化前的大量 DOM / AX 开销只是整条链路的一部分，页面加载和模型调用没有一起消失。

第三方中文 benchmark `jev.kuhung.me` 另给出一组：Jev provider **161ms** vs Gemini 2.5 Flash-Lite generation **992ms**；该页面同时明确区分 provider latency 与 page RTT（534ms vs 1352ms），因此不能把网页秒表当成模型 benchmark。

### 3.4 哪些数字不能相减

- **TypeSafe 的 100ms 不能拆成“50ms 模型 + 50ms 网络”**：仓库只记录一个总 `latency_ms`。
- **Laya 的 32.8ms 不能直接替代 Jev**：它是同构开源实现，不是 TypeSafe 的闭源服务；两者权重、机器、输入长度和网络条件不同。
- **7.073s 不是“Jev 单次推理 7 秒”**：它是 17 次决策、2 次文本生成、11 个浏览器动作、等待、加载和最终判断的总和。
- **7.073s 也不是冷启动总耗时**：计时点明确在首次 page observation 之后，初始导航、浏览器启动和最终独立验证都不在计时内。
## 4. 逐层机制

### 4.1 模型层：把“生成文本”改成“给有限选项打分”

#### TypeSafe 公开的是接口契约，不是 Jev 的完整架构

TypeSafe 把 System One 描述为：输入 `state + questions`，模型对每个问题独立评估，输出 `choice` / `score` / `noul` 以及概率和 confidence；一个请求可以混合多个问题，所有问题“并行”评估，增加问题通常只增加少量 token，不会线性增加响应时间。官方还明确说 Jev 当前只接受文本，不支持图像、音频或视频输入。

这解释了接口层为什么快：调用方不再让模型生成一段不可控文本，再靠代码解析、校验、重试；可行答案在请求里就已被限定，模型返回的是程序可以直接分派的类型化结果。

[TypeSafe Introduction](https://docs.typesafe.ai/introduction.md) 的流程图把链路画成：

```text
state + questions
        │ one request
        ▼
evaluate each question against state in parallel
        │ one response
        ▼
typed answers + probabilities + confidence
        │
        ▼
your code: branch / sort / route
```

#### “非自回归”在 Jev 上是行为级证据，在开源同构实现上是代码级证据

TypeSafe 官方发布博客的对照表写得很直接：

- 传统 LLM 的 Sampling：`Sequential. Generates one token at a time, each conditioned on the last.`
- System One + Jev 的 Sampling：`Parallel. Generates all outputs in a single query.`

但 TypeSafe 没有公开 Jev 参数量、tokenizer、encoder / decoder 结构或权重。因此可以说“**Jev 的公开行为和实现目标是非自回归式并行决策**”，不能说“**我已经看到 Jev 内部必然是某个 ModernBERT 分类头**”。后一层证据只能来自独立开源实现。

Laya 的源码给出了一个可工作的同构方案：

- `DecisionModel` 被注释为 `Bidirectional transformer encoder backbone + typed decision head`。
- 输入序列格式是 `[CLS] <question type> instructions [SEP] [MASK] opt0 [MASK] opt1 ... [SEP] state [SEP]`。
- encoder 对整段序列做双向编码；`marker_pos` 指向每个选项前的 `[MASK]`；`scorer` 在每个 marker hidden state 上输出一个 scalar logit；对同题所有选项做 softmax，得到概率分布。
- `act_head` 另外用 top-2 概率差、熵和选项数等特征输出动作概率。
- `Agent.system_one()` 把同一 state 下的多个问题组装成一个 batch，只调用一次 `self.model(...)`；随后在 Python 侧 argmax / 期望分数 / `p[1]` 转换，返回 `usage.output_tokens = 0`。

关键代码位置：[common.py L89-L136](https://github.com/NandhaKishorM/laya/blob/main/laya/common.py#L89-L136)、[agent.py L265-L368](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py#L265-L368)。

Von 走的是同一类路线：ModernBERT encoder + `OptionMarkerScorer`，把所有选项作为 marker 放进同一序列，一次 forward 返回每个选项的 logit；choice / noul / score 都复用这套 option-marker 打分，score 再做概率加权期望。见 [option_marker.py L37-L72](https://github.com/wfzyx/von/blob/master/src/von/models/option_marker.py#L37-L72) 和 [option_marker_backend.py L235-L390](https://github.com/wfzyx/von/blob/master/src/von/backends/option_marker_backend.py#L235-L390)。

SemIf 则代表另一条路线：不训练分类头，而是在冻结的 4B 模型上直接读取“答案选项 token”的 logits。它不是小型 System One encoder，但同样避免采样。它给出的同模型对照非常有价值：

| 同一 Qwen3.5-4B、同一 21 个 criteria | 时间 | output tokens |
|---|---:|---:|
| 直接读 typed option logits | **1.023s** | **0** |
| 自回归输出紧凑 JSON 数组 | **5.332s** | **111** |

第三个 arm 的首 token 中位是 0.489s，但完整数组耗时仍是 direct logits 的 5.21×。这不是“模型更聪明”的差异，而是“是否逐 token 解码”的差异。见 [SemIf README](https://github.com/TheoLeeCJ/SemIf)。

#### 自回归 chat 模型到底省掉了什么

| 阶段 | 普通自回归 chat 决策 | Jev / System One 式决策 |
|---|---|---|
| 输入 prefill | 需要 | 仍需要；大 state 仍要编码 |
| 输出生成 | 逐 token decode，依赖前一个 token；生成 JSON / reasoning / explanation | 直接对选项或 marker 打分，选一个类别 / 分数 / 概率 |
| KV cache / 解码调度 | 随输出长度增加 | 没有长文本输出解码链 |
| 结构化输出 | 可能生成坏 JSON、漏字段、越出 schema，需要 parse + validate + retry | 选项在请求时已限定；返回类型化答案 |
| 置信度 | 常需额外 prompt 或后处理，可能过度自信 | Choice / Score 直接返回概率分布和 confidence |
| 文本字段 | 同一个模型可能顺手生成并继续自回归 | 只在 `TYPE_TEXT` 时另调 text helper |
| 语义推理 | 可以多步思考、链式推理、调用工具 | 被限制为单次 snap judgment；复杂判断要在代码里拆成多个问题 |

SemIf 的 1.023s vs 5.332s 直接量化了“输出路径”的差距；但 TypeSafe 的 193.6× / 444.6× 是另一类口径：它在选定的 System One 工作流上比较 Jev 与用 System One LLM wrapper 的模型，官方自己明确标注“偏高端、不是受控基准”。两者不能混用。

#### 置信度和“零幻觉”的真实边界

TypeSafe 的 `confidence` 是概率分布的集中程度，不是正确率保证；官方文档也明确说校准是在预测群体上衡量，不保证某一个答案正确。所谓“零幻觉”更准确的表述是：**不会返回选项集合之外的值，不会产生 JSON 类型错误**；它仍然可能选错候选。Jev Ultrafast 的 `validate_choice()` 也做了 probability keys 完全匹配、和约为 1、所选值最大且 confidence 在 [0,1] 等检查，这只保证契约，不保证语义正确。

### 4.2 动作空间层：operation + target 一次请求，而不是先看页面再决定点哪里

#### 先把页面变成有限动作空间

Jev Ultrafast 的 `action_space()` 做四件事：

1. 每个 mode / DOM node 只分配一个编号，例如 `[1]`、`[2]`。一个同时可点、可输入的 combobox 仍只有一个元素编号。
2. 把 node 按可执行操作分组：
   - `click` → `CLICK`
   - `fill` → `TYPE_TEXT`
   - `select` → `SELECT`
   - `scroll` / `wait` 等无具体 target 的操作进入 controls。
3. 每个 operation 有独立的 candidate map，因此 `click_target` 只能看到可点击元素，`type_text_target` 只能看到可编辑元素；native select 还能编码成 `元素编号:选项编号`。
4. 只把目前真实观察到的候选返回给模型。模型不能生成 selector、坐标、JavaScript 或未观察到的节点。

对应代码：[model.py L48-L78](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L48-L78)。

#### 一次请求里同时问 operation 和每个 operation 的 target

`choose()` 构造的 TypeSafe body 里有两个层次：

- `questions["operation"]`：从当前可用 operation 加 `DONE` / `BLOCKED` 中选择下一步。
- 对每个 operation 生成一个 `questions["<operation>_target"]`：候选只包含与该 operation 兼容的元素。

这些问题的说明里明确写着 target 问题只负责为“假设要执行的 operation”选一个 target，另一个问题负责选 operation；target 不能读取 operation 的回答，所以设计上必须独立评估。模型返回后，代码先验证 operation，再只验证并消费匹配的 target head。

[design.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/design.md) 把这一点总结为：

> Two decisions, one network round trip. Each target head contains only compatible elements.

对应代码：[model.py L81-L148](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L81-L148)。

#### 为什么它比“先观察，再做 operation，再看页面决定 target”快

对比串行方案：

```text
observe page
  -> model call #1: what operation?
  -> maybe re-observe page
  -> model call #2: which target?
  -> generate selector / coordinate
  -> parse / validate
  -> act
```

Jev Ultrafast：

```text
observe page once
  -> one model request:
       operation question
       click_target question
       type_text_target question
       select_target question, if present
  -> validate two answers
  -> execute the matching target through code-owned DOM node identity
```

节省的部分有三块：

- **网络往返**：operation 和 target 不再拆成两次请求。
- **重复观察**：target head 和 operation head 使用同一份 snapshot，不会因为第一次模型调用导致页面状态变化而额外观察一遍。
- **输出搜索空间**：target 是有限编号，不需要生成坐标、CSS selector、XPath 或动作 JSON。

代价是**推测性 fan-out**：即使最终选择 `CLICK`，也会同时计算 `type_text_target`、`select_target` 等可能不用的 target。TypeSafe 官方认为这通常很划算，因为问题共享同一 state，增加问题“基本不改变响应时间”，只多付少量 question tokens；这比为了省一点计算再加一次网络往返更合适。

#### 文本输入是唯一的例外

Jev 不生成字段内容。Jev Ultrafast 只有在 operation 为 `TYPE_TEXT` 时才调用一个小型 OpenAI-compatible chat model。helper 的合同极其窄：

- 输入是 goal、当前 field、当前页面前 6000 字符、最近 6 个动作。
- 输出必须是恰好一个 key 的 JSON：`{"text": "..."}`。
- `null`、空串、多于一个 key、超过 2000 字符都视为失败。
- 失败时不会猜值，也不会执行输入。
- stale retry 只有在 helper 的全部输入完全相同时才复用已生成文本，成功 mutation 后立即丢弃。

对应代码：[model.py L151-L197](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L151-L197)、[questions.py L21-L24](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/questions.py#L21-L24)。

这也是为什么“Jev agent 快”不能被理解成“全程不出现自回归模型”：点击、选择、等待、完成判断可以完全不生成文本；填表时会重新引入一次文本生成，录像里两次填城市就花了 **581ms + 346ms**。
## 5. 感知层：为什么“不用截图”是提速关键，但不是万能药

### 5.1 默认路径读结构化页面，不读像素

Jev Ultrafast 的 `Agent.__init__` 默认 `screenshots=False`，并且初始观察和后续观察都以这个值传给 `Browser.observe()`。模型请求中的页面状态是：

```python
{
  "page": {"url": ..., "title": ..., "text": ...},
  "elements": [...],
  "recent_actions": [...]
}
```

它没有 `image` / `screenshot` 字段；即便 Inspector 或录像模式抓了截图，那张图也不会被加入 Jev 的 decisions request。对应 [agent.py L13-L23](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/agent.py#L13-L23)、[model.py L107-L115](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L107-L115)。

`snapshot.js` 在页面里做一次紧凑的 DOM / ARIA 抽取：

- 用 `WeakMap` 给真实 DOM node 分配稳定身份，用 `Map` 保存当前会话中的 live references；节点被替换后获得新身份，断开节点被清理。
- 读取常见 role：button、link、checkbox、radio、switch、tab、menuitem、option、gridcell、combobox、textbox、searchbox、spinbutton 等。
- 名称解析优先级包括 `aria-labelledby`、`aria-label`、`label`、按钮 value、`alt`、子文本、`title`、`placeholder`。
- 记录 `checked` / `selected` / `expanded` 等状态。
- 列出 native SELECT 的未选 option。
- 判断元素是否可见、是否有几何尺寸、是否在 viewport 内。
- 抽取视口内可见文本，最多 **6,000 字符**。
- action table 最多保留 **250 个可执行候选**，被截断的候选不能被模型选中。
- 返回 `fingerprint`、semantic guards、page key 和元素动作表。

对应 [snapshot.js L3-L106](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/snapshot.js#L3-L106)。

### 5.2 截图路线具体慢在哪里

截图路线最少多出这些阶段：

```text
capture pixels
  -> encode JPEG / PNG
  -> transport image bytes
  -> image tokenization / resize
  -> vision encoder
  -> multimodal model prefill
  -> autoregressive coordinate / action output
  -> optional grounding / verification
```

DOM / UIA 文本路线则常常已经直接拿到：

```text
role + accessible name + value + state + bounds
  -> 编号元素表
  -> finite target selection
```

因此“不用截图更快”不是玄学，而是少了一个高维输入通路和一层模型。Jev 本身又不支持图像输入，所以如果先用截图再喂给 Jev，必须额外加 OCR、视觉模型或图像转文本步骤；这已经不是同一个决策周期。

screenpeek 用同类思路做桌面自动化：本地 OCR + 应用自己的 accessibility labels，把屏幕变成 `7 Save @412,318` 这样的文本行，并宣称一步约 **0.1s**，而 `Save @412,318` 约 7 tokens、截图约 1,000 image tokens。这个数字是项目自报，但它清楚说明了文本路径的省 token 逻辑。见 [screenpeek README](https://github.com/I-No-oNe/screenpeek)。

### 5.3 这个结论不能外推成“截图永远慢”

以下场景仍然需要像素：

- canvas、游戏、地图、图片编辑、视频界面；
- 没有 UIA / DOM 语义的 custom control；
- 问题本身是“看起来怎样”“颜色/布局/图标是什么”；
- 需要视觉证据确认最终状态，而不是仅仅定位控件。

Jev Ultrafast 自己也把 shadow roots、frames、canvas、uploads、new tabs、nested scrolling 和任意键盘组件列入 limitations；录像 / Inspector 的 screenshot 仍然需要显式开启。正确的原则是：**结构化语义足够时不用截图；结构化语义不足时明确切到视觉路径，不要把二者混成一个不透明的大动作。**

## 6. 浏览器 / 协议层：把每一次往返和等待都压小

### 6.1 一个持久 browser daemon，一个 CDP session

`browser.py` 的模块注释就是 `one CDP session, no per-step subprocess`。初始化流程是：

- `ensure_daemon()`：确保 Browser Harness daemon 存在；
- `Target.createTarget(..., background=True)`：创建后台 tab；
- `Target.attachToTarget(..., flatten=True)`：拿到 session；
- `Emulation.setDeviceMetricsOverride`：固定 viewport；
- `Emulation.setFocusEmulationEnabled(enabled=True)`：让后台 tab 继续跑 rAF / 动画，而不抢用户前台；
- `Page.navigate` + 有界等待 `document.readyState == complete`，最多 15 秒。

对应 [browser.py L1-L42](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L1-L42)。

这样可以避免每步启动新进程、重新连接浏览器、丢 session、重复建 tab。`ego-jev` 的实测也支持这一点：每步新进程本身约 250–350ms，不是所有成本；最大收益来自减少语义 action channel 和重复浏览器往返。

### 6.2 一次 observation = 一次浏览器调用

`Browser.observe()` 不逐元素发 CDP 请求，而是调用 `browser_operation({"operation": "observe", ...})`；其中 `READ_STATE` 就是完整 `snapshot.js`，通过一次 `Runtime.evaluate(expression=READ_STATE, returnByValue=True)` 返回一个 JSON 对象。截图只在 request 的 `screenshot=True` 时额外调用 `Page.captureScreenshot`，并以 JPEG quality 72 返回。

对应 [browser.py L44-L107](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L44-L107)、[browser.py L188-L193](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L188-L193)。

旧版 Jev Ultrafast 的主要慢点正是这里：它反复读取完整 accessibility tree，并为大量 node 调 `DOM.resolveNode`。新路径把“读几百个节点”为“一次浏览器侧 JS 抽取”，导致协议调用从 1,092 降到 101。`ego-jev` 还把这条路径量化得更细：一次 `page.evaluate` 生成约 1.8k 字符元素表约 **2ms**，裸 CDP 动作 **13–16ms**；旧 `page.snapshot()` 为 **110–130ms**、`page.click(ref)` 为 **788–1005ms**。

### 6.3 有界等待，而不是 sleep

Jev Ultrafast 明确规定：

- 一般动作后最多等 **2 个 animation frame 或 50ms**；
- 编辑 ARIA combobox 后等待可见 suggestion，最多 **200ms**；
- 显式 `WAIT` 仍只等待 **100ms**；
- 真实网络加载不被 fast-forward；录像包含 Google 加载时间。

对应 [design.md L11-L22](https://github.com/browser-use/jev-ultrafast/blob/main/docs/design.md#L11-L22)、[browser.py L44-L86](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L44-L86)。

这不是“为了快跳过等待”，而是**根据页面是否出现需要的状态来结束等待**。对于 autocomplete，如果不等 suggestion 到达，模型就会在残缺选项上做一次错误决策，反而增加重试；相反，如果无条件等固定 1 秒，又会浪费大量时间。

### 6.4 代码持有 node identity，模型输出不能变成 selector / 坐标

`browser_operation()` 在真正输入前会重新执行：

1. `window.__jevFast.nodes.get(action.node)` 取出真实 node；
2. 检查 `isConnected`、disabled、aria-disabled、inert、readOnly、可见性；
3. 重新取 bounding rect，确认中心点在 viewport 内；
4. 用 `document.elementFromPoint(x, y)` 检查是否被遮挡；
5. 对 select 检查目标 option 是否存在且未禁用；
6. 点击通过 `Input.dispatchMouseEvent(mousePressed/mouseReleased)`，文本通过原生 select-all + `Input.insertText`。

对应 [browser.py L135-L186](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L135-L186)。

这个设计的性能和安全收益同时存在：

- 模型只输出编号，不需要负责坐标、selector 或 JavaScript；
- 即使页面在模型请求期间移动了元素，执行前还会再检查真正的 target，而不是盲用 stale coordinates；
- 被覆盖、disabled、不可见、断开连接的 target 会拒绝执行并触发重新观察，而不是点错位置。

### 6.5 freshness 不用“DOM mutation 计数器”，而用语义状态

新版 freshness 不把任何 DOM mutation 都当变化（动画会导致永久失效），而是比较：

- document / URL / viewport；
- safe form values / checked / selected；
- candidate node 的 identity、role、name、value、状态；
- 附近 form/dialog/row/article 的上下文；
- 对 text / typing / scroll / wait / completion 做更宽的全页 semantic 比较。

这仍然只是启发式，不是“任意页面变化都与目标无关”的证明；但对于 Google Flights 这种动画频繁的真实站点，它显著减少了无效重预测。对应 [snapshot.js L44-L54](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/snapshot.js#L44-L54)、[design.md L13-L21](https://github.com/browser-use/jev-ultrafast/blob/main/docs/design.md#L13-L21)。

### 6.6 主循环：predict → act → observe，决策一次消费

`agent.py` 的循环非常短：

1. `tick` 调 `predict`，再用当前 page fingerprint 调 `act`；
2. `predict` 如果发现页面已经不新鲜，会先重新 observe；然后调用 `choose()`，把 decision 和 fingerprint 存起来；
3. `act` 在 mutation 之前就把 `state["decision"] = None`，防止重试导致双击；
4. 对 `DONE` / `BLOCKED` 再做一次 freshness 检查；
5. 对 fill action，先检查 freshness，再调 text helper；
6. 执行 action，记录 history，然后重新 observe；
7. 连续 3 次非 wait action 没有改变 page fingerprint，则判 blocked。

对应 [agent.py L46-L165](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/agent.py#L46-L165)。

这些工程手段的共同点是：**它们不试图让模型变快，而是消除模型之外不必要的串行工作和重复工作。**
## 7. 同类实现对照表

> Star / language / license 均来自 GitHub API，快照时间 **2026-09-23 Asia/Shanghai**。Star 数会继续变化；这里的重点是辨析“它解决哪一层问题”和“延迟口径是什么”。

| 项目 | Stars / 语言 / 许可证 | 架构一句话 | 公开延迟数字 | 关键做法 | 是否可本地跑 |
|---|---:|---|---:|---|---|
| [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) | 18,417 / Python / MIT | 本地 CDP 执行 + 托管 TypeSafe Jev 决策，动态 indexed action space | Jev 请求中位 **178ms**；Google Flights 整任务 **7.073s** | 一轮 operation + target；一轮一次 DOM snapshot；默认无截图；freshness / occlusion；bounded wait | 浏览器 agent 本地跑；Jev 决策必须云 API |
| [browser-use/browser-harness](https://github.com/browser-use/browser-harness) | 18,036 / Python / MIT | Jev Ultrafast 的浏览器连接 / 自愈 harness | 无统一的模型延迟声明 | daemon + CDP；真实浏览器；工具化 | 是 |
| [NandhaKishorM/laya](https://github.com/NandhaKishorM/laya) | 17,833 / Python / Apache-2.0 | 开源、非自回归、Jev-compatible System 1 clone | README：单问题 **33ms**、batch **7.2ms/question**；BENCHMARKS：T4 单问题 32.8–39.5ms，10 问题 72.3ms | ModernBERT/mmBERT + option-marker scorer + calibrated probabilities + router | 是，需要 PyTorch / Transformers / 模型权重 |
| [mizorewww/laya-mlx](https://github.com/mizorewww/laya-mlx) | 5,634 / Python / Apache-2.0 | Laya 的 Apple Silicon MLX 原生移植 | 短问题 P50 **13.42ms**（Laya 421M）/ **7.39ms**（Multilingual 322M）；50 q 146.8 / 395 q/s | MLX 编译、batch、prefix reuse、bidirectional encoder + decision heads | 是，限 Apple Silicon |
| [TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf) | 3,922 / Python / MIT | 用开源 4B 模型直接读 option logits，不采样答案 token | 同模型 direct logits **1.023s** vs 自回归 JSON **5.332s** | runtime-defined criteria；native option logits；shared-state prefill；calibration | 是，3090 / MPS / llama.cpp 等 |
| [mizorewww/laya-coreml](https://github.com/mizorewww/laya-coreml) | 1,325 / Python / Apache-2.0 | Laya 的 Core ML + Apple Neural Engine 移植 | 短 ANE FP16 P50 **4.98ms**；1024-token 约 **91.7ms** | ANE FP16；shape bucket；部分 W8；加载 / warmup 排除 | 是，Apple Silicon |
| [wfzyx/von](https://github.com/wfzyx/von) | 510 / Python / Apache-2.0 | ModernBERT + option-marker 的本地 System One 决策模型 | README：**sub-25ms**，表格 **约18ms GPU**；GitHub description 当前写 **sub-15ms** | 395M ModernBERT；所有 option 放同一序列；single forward；noul / choice / score 共用打分 | 是，CUDA / ROCm / MPS / CPU |
| [receptron/laya](https://github.com/receptron/laya) | 305 / TypeScript / MIT | Node / ONNX Runtime 版 Laya，不需要 torch / Python runtime | 3 个问题批量约 **140ms**（Apple CPU 暖机后） | ONNX Runtime；1.7GB fp32；同 `systemOne` 接口 | 是，Node 20+ / ONNX |
| [Ying-Kai-Liao/jev-browser](https://github.com/Ying-Kai-Liao/jev-browser) | 76 / JavaScript / MIT | MCP / Playwright 上层，Jev 决定、LLM 规划、Playwright 执行 | 每次 Jev 调用约 **300ms**；每步 2–4 次；5 步 checkout 约 **14s** | one Jev request 问多个 guard / done / target / value 问题；页面读取给 Jev 而不是给生成模型 | agent 本地；Jev 云 |
| [filedcom/playjev](https://github.com/filedcom/playjev) | 7 / TypeScript / MIT | “Stagehand, but with Jev”：Playwright 执行 + Jev 选择 | bulk iframe form **0.91s** vs 5 个单独 action **7.41s**；8/8 本地公开 eval | sparse YAML state；numbered nodes；fixed operation vocabulary；bulk form one target-selection request | agent 本地；Jev 云 |
| [jiangkoumo/ego-jev](https://github.com/jiangkoumo/ego-jev) | 4 / JavaScript / MIT | 把每步大模型往返换成单个进程内的 Jev operation + target | HN 两步导航 **4,569ms → 1,916ms**；表单 3,607ms；Jev **1.0–1.5s/次** | viewport indexed element table；one request operation + target heads；raw CDP；single process | agent 本地；Jev 云 |
| [hdkiller/jev-panerelay](https://github.com/hdkiller/jev-panerelay) | 2 / Python / MIT | Panerelay 连接真实 Chrome/Edge + OpenRouter Jev | README 宣称 **350–500ms/step**、导航约 1–2s | atomic DOM snapshot；CDP native events；freshness marker；只 TYPE_TEXT 时调 helper | agent 本地；Jev / helper 云 |
| [I-No-oNe/screenpeek](https://github.com/I-No-oNe/screenpeek) | 2 / Rust / MIT | 不截图的桌面 GUI 控制：本地 OCR + accessibility labels → 文本行 | 项目自报 **约0.1s/step**；`Save @412,318` 约 7 tokens vs 截图约 1,000 | 控件按名字点击；屏幕变成短文本；无 API key；Linux / Windows | 是，纯本地 |
| [browser-use/windows-harness](https://github.com/browser-use/windows-harness) | 73 / Python / MIT | 一个持久 Python 进程直接暴露 Windows / browser / filesystem 原语 | 无 latency 数字 | thin harness；UIA / PrintWindow / SendInput / background option；Browser Harness | 是 |
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 39,093 / TypeScript / Apache-2.0 | VLM + screenshot 的 native GUI Agent stack | README 未给端到端 latency；本次不编造 | Vision / Screenshot；UI-TARS / Seed-VL；local 或 remote model | 客户端可本地；模型可本地或远程 |

### 7.1 按“快”的来源分成四类

1. **托管 System One + 本地浏览器执行**：Jev Ultrafast、jev-browser、playjev、ego-jev、jev-panerelay。模型调用可能是 100ms 或秒级，但本地浏览器和协议层仍可做很快。
2. **本地 encoder + typed head**：Laya、MLX、CoreML、Von。没有网络，短问题可以到 5–40ms；缺点是权重、内存、native runtime 和 CPU/GPU 兼容性。
3. **本地大模型 + direct logits**：SemIf。它不训练小分类头，所以仍承受 4B 模型 prefill 成本；优点是实现简单、模型通用，缺点是延迟明显高于 300M–400M encoder。
4. **截图 + VLM**：UI-TARS 等。优势是视觉通用性；代价是 capture、image tokens、vision encoder、grounding 和更长的模型输出。现有材料没有给出与 Jev 的条件一致对照，因此本报告不把它量化为固定“慢 N 倍”。

### 7.2 这些项目对 Jev Ultrafast 做法的差异

- **jev-browser**：更偏 MCP / Playwright 工具，Jev 负责 done / error / irreversible / tool / target / value 等多项判断；每步可能 2–4 次 Jev，性能不如 Jev Ultrafast 的单轮 fan-out。
- **playjev**：最接近“动作空间 + 批量 target selection”的思路，强调 Playwright 保留 auto-wait / locator / iframe / shadow DOM；没有 Jev Ultrafast 的 7 秒整任务数字，但有 0.91s vs 7.41s 的 bulk form 观察。
- **ego-jev**：把“一个进程、一次 evaluate 自建元素表、裸 CDP 动作”作为重点；实测中 Jev 调用本身仍 1.0–1.5s，说明模型外的浏览器重构不能消除大 state / 网络的模型延迟。
- **jev-panerelay**：功能描述和 Jev Ultrafast 最相似，但 star 少、README 的口径很营销，缺少 benchmark / 可复现数据；适合作为工程思路参考，不适合作为独立性能证据。
- **Laya / MLX / CoreML / Von**：更像是把 System One 决策后端本地化，不是浏览器 agent；它们解决“模型云依赖和推理延迟”，不解决 CDP / DOM / waiting。
- **screenpeek / windows-harness**：把结构化感知推广到桌面。screenpeek 走 OCR + accessibility text；windows-harness 是更薄的混合原语（UIA + screenshots + real browser），它把“模型写缺失逻辑”放在 Python 进程里，不是 Jev 那种严格有限 action space。`windows-harness` README 标题称 “Six primitives”，但同一代码块实际列出 `see/key/type/click/paste/ax/script` 七项，属于文档口径不一致，不影响其 thin-wrapper 定位。
## 8. 可迁移到 Windows-MCP 的做法

### 8.1 先把“模型决策”和“确定性执行”分开

现有 [web-automation-research.md](web-automation-research.md) 已经给出正确方向：Playwright / CDP 是确定性控制层，Jev / Laya 是可选决策层，不应把托管 Jev API 设为默认依赖。Jev Ultrafast 的证据进一步支持：

- 模型只输出**选择**：operation、target index、概率 / confidence。
- 代码拥有**权限和副作用**：真实 node、hit-test、click、type、wait、done / blocked 退出。
- 模型不能输出 selector、坐标、脚本或 shell 命令。

Windows-MCP 当前代码已经部分落在这个方向上：

- `web/service.py` 用一次性 `_JS_SNAPSHOT` 生成带 `data-windows-mcp-ref` 的编号元素表，并提供 `snapshot_data`、ref resolve、visibility / enabled / occlusion 检查和 action 后 validation。
- `agent/action_space.py` 已把 Jev Ultrafast 的 action-space / operation-target validation 思路移植进来。
- `agent/jev.py` 已实现“一次 POST + operation / target answers 的校验”，`agent/policy.py` 已将 Jev / OpenRouter / Laya 放在可插拔 policy 后面。
- `agent/laya.py` 目前仍是 reserved stub：有可用路径判断，但 `decide()` / `text()` 明确 `NotImplementedError`，所以“本地 Laya”不是已经完成的生产路径。

因此迁移的第一条不是“重写”，而是**保持这三层边界不回退**。

### 8.2 建议清单

| 优先级 | 做法 | 为什么快 / 为什么稳 | 在 Windows-MCP 的落点 |
|---|---|---|---|
| P0 | 每轮只做**一次有界结构快照** | 避免逐控件 UIA 调用和重复 tree walk；把模型输入限制在可处理规模 | 保留 `WebAutomationService.snapshot_data`；桌面 `Snapshot` 也输出 role / name / value / state / enabled / bounds / ref 的 bounded table |
| P0 | 默认用 UIA / DOM 文本，不默认截图 | 少 capture、image token、VLM 和 grounding；文本模型也能用 | Web 已结构化；桌面优先 UIA，截图工具保留为显式 fallback |
| P0 | 动作前重新解析 target，检查 visible / enabled / bounds / occlusion | 防止模型选择后页面移动、弹层遮挡、节点替换；避免点错 | Web 已有 `_ensure_not_obscured_sync` 与 `_validation_sync`；UIA 侧补 runtime id / property freshness |
| P0 | 使 decision 一次性消费，stale 必须重新 observe | 防止重试导致双击、重复输入或错误提交 | 借鉴 `agent.py` 的 fingerprint + `decision=None` before mutation |
| P1 | operation 与 target 在同一请求 fan-out | 省掉串行模型往返；target heads 可使用同一 state | `build_questions()` 已具备；未来扩展 Windows 动作时保持同构 |
| P1 | 有界 wait：一般 2 frames / 50ms，autocomplete 最多 200ms，显式 wait 100ms | 不被动画和固定 sleep 拖慢，也不在 suggestion 未到时做错决定 | Web 已有 50ms 左右 validation；将 200ms 规则推广到 Windows 组合框 / 建议列表 |
| P1 | 只有 `TYPE_TEXT` 才调生成模型，且严格 JSON / 空值拒绝 | 消除大多数点击任务的生成成本；避免未验证文本进入 UI | `JevPolicy.text()` 已单独走 TextHelper；保持 string-only 合约 |
| P1 | 记录 p50 / p95 和分阶段 timing：decision、network、snapshot、action、wait/load、retry | 否则只能看到“整任务慢”，无法判断优化对象 | Windows-MCP 已有 `WINDOWS_MCP_PROFILE_SNAPSHOT`，可扩展到完整 agent loop |
| P2 | 本地决策模型作为可选 backend，而不是默认依赖 | 可去云、隐私更好，但带来 1.7GB+ 权重和冷启动 | `LayaPolicy` 保留 stub；先要求 preload + ONNX / Node runtime + 版本锁定，再考虑产品化 |
| P2 | 对 confidence 做自己的阈值校准 | Jev 的概率是 calibrated group estimate，不保证单次正确 | 用 Windows-MCP 任务日志做 held-out calibration；低 confidence 时转 UI 验证或人工 |
| P2 | 显式处理 unsupported：shadow root / frame / canvas / nested scroll / new tab | 比“看起来执行了”更安全；unsupported 应返回 structured blocked | Web / Desktop 都保留结构化错误，不要用猜测坐标兜底 |

### 8.3 不建议照搬的做法

- **不要把 `windows-harness` 的 `script()` 任意代码能力直接混进受限决策 loop**。它很强，但也扩大了模型可执行副作用和信任边界；应把 raw script / filesystem / shell 放在独立、显式授权的工具面。
- **不要把 Jev / Laya 当成“必须经过”的层**。固定 selector 或确定性 Playwright / UIA 流程本来就更快；只有需要根据页面状态自主选择时才引入决策模型。
- **不要用单步 latency 宣传整任务性能**。一次 Jev 178ms 不代表一次 Flights 任务 178ms；一次本地 CDP click 13ms 也不代表表单提交完成 13ms。
- **不要只测热路径**。Laya README 记录了语言切换未 preload 时的模型 reload：7–10s / 次；MLX / CoreML 的 5–14ms 都明确排除了加载和 warmup。Windows-MCP 的本地模型路线必须把冷启动作为一等指标。
- **不要迷信“零幻觉”**。Jev 的类型安全是真的；语义正确性不是。

## 9. 未核实、易误读和反例

1. **Jev 的内部实现未公开**：`new model architecture`、`parallel sampler`、`all outputs in a single query` 都是官方表述，但没有官方 model card / weights / 参数量。把 Jev 说成某个已知 encoder + 某个 head，只能是推断。
2. **“Jev 70ms–500ms”是范围，不是稳定 p50**：Jev Ultrafast 中位 178ms/请求，ego-jev 的实测 Jev 是 1.0–1.5s/次。大 state、云路由、API 供应商和网络会显著改变结果。
3. **“不用截图”不是绝对定律**：视觉模型可以一次调用处理截图，也未必天然多轮；真正可量化的是截图 capture / encode / image token / vision prefill / grounding 这几个阶段，而不是一个未经控制的“慢 N 倍”口号。
4. **“零幻觉”不是“零错误”**：TypeSafe 的“不会类型错”是约束输出的结果；模型仍会在候选里选错。Juejin 文章引用的扑克实测还指出置信度可能和正确性倒挂，这类第三方反例提醒必须用真实任务验证。
5. **“Jev 比 LLM 快 193.6× / 便宜 444.6×”不是通用 benchmark**：官方发布页自己说这些数来自特定工作流，是偏高端估计，且 LLM 参考答案存在 OpenAI / Anthropic 偏置。应当把它当作“上限型宣传”，不是普遍性能事实。
6. **Reddit / X / V2EX / 微信公众号证据缺口**：本次 Reddit API 和 DuckDuckGo 请求超时，X / 公众号没有可读的一手正文；Bing 只给出部分搜索结果。为不污染证据链，没有把搜索摘要当成实测证据。可核实且已使用的社区材料是 Hacker News、Dev.to、掘金和 `jev.kuhung.me`。
7. **本报告的 star 数是快照**：新建项目 star 数和 README 声明可能快速变化；尤其 Laya / Von / Jev 系列都在 2026-09-16 到 2026-09-23 之间集中发布。
## 10. 来源清单

### 10.1 Jev Ultrafast 一手源码与测量

本报告按以下上游仓库逐文件核对（核对时使用完整本地副本）：

- [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)
- [README.md](https://github.com/browser-use/jev-ultrafast/blob/main/README.md)
- [jev_ultrafast/agent.py](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/agent.py)
- [jev_ultrafast/model.py](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py)
- [jev_ultrafast/questions.py](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/questions.py)
- [jev_ultrafast/browser.py](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py)
- [jev_ultrafast/snapshot.js](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/snapshot.js)
- [docs/design.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/design.md)
- [docs/performance.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance.md)
- [docs/performance-prepared.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance-prepared.md)
- [docs/flights-measurement.json](https://github.com/browser-use/jev-ultrafast/blob/main/docs/flights-measurement.json)
- [docs/full-speed-measurement.json](https://github.com/browser-use/jev-ultrafast/blob/main/docs/full-speed-measurement.json)
- [docs/measurement.json](https://github.com/browser-use/jev-ultrafast/blob/main/docs/measurement.json)
- [browser.py L135-L193](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/browser.py#L135-L193)
- [agent.py L55-L165](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/agent.py#L55-L165)
- [model.py L81-L197](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py#L81-L197)
- [snapshot.js L3-L106](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/snapshot.js#L3-L106)

### 10.2 TypeSafe 官方

- [Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [Introduction](https://docs.typesafe.ai/introduction.md)
- [System One](https://docs.typesafe.ai/concepts/system-one.md)
- [Primitives](https://docs.typesafe.ai/primitives.md)
- [Confidence](https://docs.typesafe.ai/confidence.md)
- [How to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md)
- [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out.md)
- [Parallel questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions.md)
- [Models](https://docs.typesafe.ai/models.md)
- [llms.txt](https://docs.typesafe.ai/llms.txt)
- [llms-full.txt](https://docs.typesafe.ai/llms-full.txt)

### 10.3 Laya / 本地决策模型

- [NandhaKishorM/laya](https://github.com/NandhaKishorM/laya)
- [Laya README](https://github.com/NandhaKishorM/laya/blob/main/README.md)
- [Laya BENCHMARKS.md](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md)
- [Laya common.py](https://github.com/NandhaKishorM/laya/blob/main/laya/common.py)
- [Laya agent.py](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py)
- [receptron/laya](https://github.com/receptron/laya)
- [mizorewww/laya-mlx](https://github.com/mizorewww/laya-mlx)
- [mizorewww/laya-coreml](https://github.com/mizorewww/laya-coreml)
- [wfzyx/von](https://github.com/wfzyx/von)
- [Von option_marker.py](https://github.com/wfzyx/von/blob/master/src/von/models/option_marker.py)
- [Von option_marker_backend.py](https://github.com/wfzyx/von/blob/master/src/von/backends/option_marker_backend.py)
- [TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf)
- [Laya 作者 Dev.to 文章](https://dev.to/nandakishor_m_6cc0adfde9f/i-built-non-autoregressive-decision-models-a-year-ago-then-a-frontier-lab-called-it-a-18me)

### 10.4 Jev 上层浏览器 / GUI 封装

- [Ying-Kai-Liao/jev-browser](https://github.com/Ying-Kai-Liao/jev-browser)
- [filedcom/playjev](https://github.com/filedcom/playjev)
- [jiangkoumo/ego-jev](https://github.com/jiangkoumo/ego-jev)
- [hdkiller/jev-panerelay](https://github.com/hdkiller/jev-panerelay)
- [I-No-oNe/screenpeek](https://github.com/I-No-oNe/screenpeek)
- [browser-use/windows-harness](https://github.com/browser-use/windows-harness)
- [browser-use/browser-harness](https://github.com/browser-use/browser-harness)
- [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop)

### 10.5 社区 / 第三方讨论

- [Hacker News: Jev Ultrafast](https://news.ycombinator.com/item?id=49735979) — 92 points、14 comments；本报告使用其中的初次观察计时质疑和生态反馈。
- [Hacker News: CUA-S1 for computer use](https://news.ycombinator.com/item?id=49767564) — 706k 参数 specialist，自报 form decision 本地 7–9ms vs 托管 Jev 260–280ms（包括网络）。
- [Hacker News Algolia 搜索 API](https://hn.algolia.com/api/v1/search?query=Jev%20TypeSafe&tags=story)
- [CUA-S1 / trycua/cua](https://github.com/trycua/cua)
- [掘金：发布 3 天登顶 HN：不生成一个字的模型 Jev](https://juejin.cn/post/7686669083098775562) — 中文第三方总结，明确复述官方 70–500ms、193.6× / 444.6× 并标注自报性质。
- [kuhung：深入解读 Jev 模型](https://jev.kuhung.me/) — 含 161ms Provider vs 992ms generation 的第三方 benchmark 页面。
- [知乎：万字长文解读 Jev 模型](https://zhuanlan.zhihu.com/p/2084624500726022179) — 搜索索引可见，但本次正文受反爬限制，未作为数据来源。
- [GitHub API 仓库元数据](https://api.github.com/repos/browser-use/jev-ultrafast) — Star / license / language 快照的接口形式；同类项目也通过 `/repos/<owner>/<repo>` 读取。

### 10.6 本仓库内的对照实现（仓库相对路径）

- `docs/web-automation-research.md`
- `src/windows_mcp/web/service.py`
- `src/windows_mcp/agent/action_space.py`
- `src/windows_mcp/agent/jev.py`
- `src/windows_mcp/agent/laya.py`
- `src/windows_mcp/agent/policy.py`

## 11. 最终结论

Jev / System One 的速度来自一组必须一起看的工程决策：

- **模型输出面变窄**：从自由文本 / JSON / 坐标变成有限 option 上的概率分布。
- **决策并发化**：同一 state 上 `operation`（候选里含 `DONE` / `BLOCKED`）与每个 operation 的 `<operation>_target` 在一次请求内 fan-out。注意 `DONE` / `BLOCKED` 只是 `operation` 问题的候选值，并不是独立的 done / guardrail question；fan-out 的实际问题集合是 `operation` 加每种 target-producing operation 的 `*_target`。
- **感知结构化**：默认 DOM / UIA / accessibility 文本，不把截图塞进决策模型。
- **执行确定化**：模型只给编号，代码持有 node identity，动作前重新校验，动作后 bounded observe。
- **热路径常驻**：浏览器 daemon / CDP session / 本地模型 preload，避免每步冷启动。
- **等待有界**：只在需要时等待可见状态，不用固定长 sleep。
- **文本单独处理**：只有真正需要写字段时才引入生成式模型。

最重要的反直觉结论是：**单步 100ms 级并不自动导致整任务秒级以内**。Jev Ultrafast 的 7.073s 说明一个真实浏览器任务仍然要支付多次模型调用、浏览器协议、页面加载、结果等待和文本生成；Jev 的真正优势是把每一次“模型决策”变得足够便宜、有效和可组合，而不是让整个互联网变成零延迟。
## 12. 代码级机制证据索引（可复核定位）

上面各节的机制结论，都能在 `browser-use/jev-ultrafast` 的源码里按行号复核。行号相对 `jev_ultrafast/` 目录；对应文件可直接用 10.1 的 GitHub 链接打开。

| 机制 | 代码定位 | 证据等级 |
|---|---|---|
| 页面侧 node → id 缓存：`WeakMap` / `Map` 保存真实节点，断开的节点被清理 | `snapshot.js:3-8` | 代码可证 |
| 只扫描可交互选择器，过滤 password / file / hidden、不可见、disabled、越界节点 | `snapshot.js:9-11, 24-27, 55-60` | 代码可证 |
| 每个动作候选保存真实 node、role / name / value / 几何 / 状态 | `snapshot.js:61-80` | 代码可证 |
| 可见文本最多 6,000 字符，动作候选最多 250 个，超出直接 `splice` 丢弃 | `snapshot.js:82-92, 99-101` | 代码可证 |
| freshness 用语义 marker / page key / guard 比较，而不是 DOM mutation 计数 | `snapshot.js:44-54, 93-98` | 代码可证 |
| 模型元素编号按 node 去重，一个真实节点只占一个编号，每个 operation 有独立 target map | `model.py:48-78` | 代码可证 |
| `operation` 与每个 `<operation>_target` 在同一个 body 里，一次 POST 完成 | `model.py:81-133`、`docs/design.md:5-7` | 代码可证 |
| 类型化校验：choice 必须命中候选集、概率 key 完全匹配、概率和为 1，否则在动作前抛错 | `model.py:30-45, 120-133` | 代码可证 |
| 浏览器侧常驻 daemon + 后台 tab + flattened CDP session，不在每步重建 | `browser.py:1, 20-28` | 代码可证 |
| 一轮 observation = 一次 `Runtime.evaluate(READ_STATE)` 拿整张元素表 | `browser.py:44-86, 188-193` | 代码可证 |
| 输入前重新解析真实节点并检查 connected / disabled / inert / readOnly / 可见性 / 视口 / `elementFromPoint()` 遮挡 | `browser.py:135-165` | 代码可证 |
| 有界等待：普通动作 2 rAF 且最多 50ms，autocomplete 最多 200ms，显式 WAIT 100ms | `browser.py:44-71, 103-104` | 代码可证 |
| mutation 前先清空 `state["decision"]`，且不对浏览器 mutation 做 transport retry | `agent.py:86-96, 116-118` | 代码可证 |
| 只有 `fill` 才调用文本 helper；输出必须恰好一个 key、非空、≤2000 字符，否则不输入 | `agent.py:105-117`、`model.py:160-197` | 代码可证 |
| 截图是显式额外路径，不在默认观察链上 | `browser.py:12-14, 188-193` | 代码可证 |
| Google Flights 整任务分账（7,073ms / 17 次决策 / 中位 178ms / 合计 3,720ms） | `docs/flights-measurement.json:2-6, 16-20, 47-49, 115-235` | 测量文件可证 |

> 注意：上表只覆盖“代码可证”和“测量文件可证”两类。本报告里所有外部项目的延迟数字（Laya、Von、MLX、CoreML、SemIf、jev-browser、ego-jev、screenpeek 等）都是**项目自报或官方宣称**，没有本地复现条件，不应与本次核对过的源码证据混为一谈。
