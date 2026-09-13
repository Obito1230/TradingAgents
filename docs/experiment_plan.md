# 实验方案(L1 记忆层)· Experiment Plan

> 配套:`docs/memory_three_layer_design.md`(设计)· `docs/positioning_and_related_work.md`(定位/协议)· `docs/v1_implementation_guide.md`(实现)
>
> 工具:`scripts/run_experiment.py`(配对 A/B 执行器)· `scripts/run_pool.py` / `scripts/settle_now.py`(记忆构建)· `scripts/check_injection_audit.py`(注入审计)

---

## 1. 范围与目标(单篇论文,三层合一)

**论文目标(已定)**:一篇论文写**完整三层架构** —— L1 事件记忆 + L2 情景检索 + L3 蒸馏巩固,配 P1–P5 全套消融;**不拆成两篇,不提前投稿**。

下表只表示**实现进度**(决定"现在能跑哪些实验"),不改变论文目标:

| 原计划 | 实现状态 | 何时可评估 |
|---|---|---|
| **P1 消融-必要性**(L1 开/关) | ✅ 已实现 | **现在** |
| **P4 预算论据**(同等/更低 token) | ✅ 已实现 | **现在** |
| **P5 复盘质量**(人工评估) | ✅ 已实现 | **现在** |
| **P2 蒸馏补偿遗忘**(L3 开/关) | ⛔ 待 v2 实现 | v2 落地后 |
| **P3 检索阶梯**(L2 变体 A/B/C) | ⛔ 待 v3 实现 | v3 落地后 + 语料足够(≥30 条已结算事件) |

> **纪律**:claim 跟着实现走 —— **L2/L3 未实现、未消融之前不投稿**。唯一例外是外部硬截止(比赛/毕业节点)迫使提前产出:那时才把已完成部分单独成文,其余留 future work。**默认按三层合一推进。**

### 1.1 两个由机制决定的时序约束(不是选择,是物理约束)

1. **L3 的消融需要"淘汰队列里有内容"**:蒸馏的输入是被淘汰的条目,所以必须先积累记忆语料(E1/E3),P2 才有东西可消融;
2. **L2 的消融需要足够大的语料**:设计文档 §5.6 规定"已结算事件 < ~30 条时退化为粗桶匹配",所以 P3 必须在语料 ≥30 条之后才有意义。

→ 这正好支持"一步一步来":**先把 L1 跑起来 → 用廉价的 `settle_now` 累积语料 → 再做 L3、L2 → 最后一次性跑全套消融 → 一篇论文**。

---

## 2. 实验设计

### 2.1 两个实验臂

| 臂 | 配置 | 含义 |
|---|---|---|
| `on` | `event_memory_enabled=True` | L1 读写均开启(处理组) |
| `off` | `event_memory_enabled=False` | L1 **既不读也不写**(对照组 = 上游基线) |

**关键**:legacy 决策日志反思(`get_past_context`)**两臂都存在** → 差异纯粹来自 L1 层。这一点由 `tests/test_memory_ablation_switch.py::test_legacy_reflections_present_in_both_arms` 锁定。

### 2.2 记忆构建 vs 测试集(必须分开)

- **记忆构建集**:较早日期的场景,用来沉淀 EVENT(每个场景 = 一次分析 + 一次结算)。
- **测试集**:较晚日期的场景,用来跑 A/B。
- **⚠️ 泄漏规则(硬约束)**:测试场景 (T, D) 的记忆库里**不得存在同一 (T, D) 的 EVENT** —— 那等于把这次决策的**自己的未来结果**喂给它。执行器会自动检测并**跳过**该场景(除非 `--allow-leak`)。
  - 注:`get_lessons_context` 还会注入**跨标的**事件,这是架构设计的一部分(同标的 n_same + 跨标的 n_cross),不算泄漏。
- **⚠️ 时间一致切分(同等硬约束,2026-09-13 补齐)**:注入的记忆必须是**严格早于决策日 D** 的。只查"(T, D) 完全相等"是不够的 —— 例如语料里有 `600519.SS@2026-01-28` 的事件(结果实现于 01-28→02-04),而测试场景是 `600519.SS@2026-01-22` 时,那条事件对 01-22 的决策就是**未来信息**;跨标的注入同理(6 月的事件不能进入 1 月的 prompt)。
  - 实现:`get_lessons_context(ticker, as_of=D)` / `get_past_context(ticker, as_of=D)`,由 `_build_past_context(ticker, as_of=trade_date)` 传入;**两臂用同一套过滤**,唯一差异仍是 L1 内容。
  - 执行器对"同标的 EVENT 日期 ≥ D"会打印 `[NOTE] … time-sliced out`,并在**该标的没有任何可用历史事件**时打印 `[WARN] no usable same-ticker EVENT`。
  - 推论:**测试场景必须整体晚于语料**。场景日期早于或夹在语料中间时,该场景的记忆臂基本为空,跑出来只是浪费墙钟。
- **⚠️ 语料日期上界(硬约束,实操最容易踩)**:新增语料场景的日期必须**严格早于最早的测试场景日**。当前最早测试日是 `2026-07-03`,所以语料一律取 `< 2026-07-03`。
  - 为什么必须写死:按"最近涨跌幅"扫描出来的候选,**排名最高的那几个往往正是测试场景本身**(实测:`600519.SS@2026-07-13` / `300750.SZ@2026-07-03` / `601318.SS@2026-07-13` 既是扫描前三名,也是测试集)。拿它们当语料 → `(T, D)` 完全命中 → 执行器 `[SKIP-LEAK]` 全部跳过 → **P1 场景数归零**。
  - 且日期 ≥ 测试日的语料事件会被**时间切片切掉**,对测试 prompt 毫无贡献(只是白跑一次分析)。
  - 安全取数区间示例:`scan_demo_pool.py --start 2025-10-01 --end 2026-06-25`。
- **⚠️ 记忆库必须在 P1 之前建完并冻结(硬约束)**:`run_experiment.py` 的每次 run 在**开始时**各读一次记忆库。若 pilot 运行期间新增或结算了事件,先跑的 run 与后跑的 run 读到的记忆不同 → 两臂不再可比。**pilot 运行期间不要执行 `settle_now.py` / `run_pool.py`**;跑完再动。
- **⚠️ 语料构建必须串行**:记忆库没有文件锁,两个进程同时写会互相覆盖(丢条目或写坏)。一次只跑一个 `run_pool` / `settle_now`。

### 2.2.1 评估集必须按协议盲选(硬约束,写死)

**测试场景不得依据其结果来挑选。** 这条比上面几条更根本 —— 违反它,整个数字作废,而前面所有工程护栏都拦不住。

- **现状声明**:当前 `exp_scenarios.json` 的三个场景是用 `scan_demo_pool.py --min-abs 0.04` 按**已实现涨跌**筛出来的,即在**因变量上选样本**(selection on the dependent variable)。
  - 允许用途:**P1 pilot 探路**(验证链路、看 token 差、看评级是否被记忆改变)。
  - **禁止用途**:作为论文报告的命中率数字。
- **论文评估集的选择规则**:先定规则、写进实验日志,**再取数**。例如"在区间 `[S, E]` 内,对固定标的池按固定步长取每个标的的第 k 个交易日",规则与结果无关。
- **取数后不得因为结果不好而替换场景**;确需替换必须留痕(换了哪个、为什么),否则构成 p-hacking。
- 同一原则已写在 `demo_pool_ashare.json` 的 readme 里:"论文评估集必须按协议盲选,与结果无关"。
- **低价数据快照**:`data_cache_dir` 里的 OHLCV 缓存文件按"**当天**"命名(5 年窗口 → 今天),**每天滚动**,所以它不是永久档案。论文数字产出时**把整个 `data_cache_dir` 复制成冻结产物**(如 `experiments/price_cache_snapshot/`),否则同一历史窗口在另一天重算可能因复权调整而不同 → 不可复现。

### 2.3 记忆"冻结"设计(推荐主实验)

对每个测试场景,两臂使用**同一份冻结记忆库**(仅由构建集产生),这样差异归因于"注入"而非"记忆累积动力学"。累积式设计(边跑边学)可作为补充演示,但噪声大、成本翻倍。

### 2.4 两臂是**等预算替换**,不是加法(2026-09-13 定稿)

`_build_past_context` 的两臂定义:

| 臂 | 注入内容 |
|---|---|
| `event_memory_enabled=False`(基线) | legacy 决策日志反思(n_same=5 + n_cross=3 平权)——**上游框架行为原样** |
| `event_memory_enabled=True`(处理) | L3 准则 preamble + L1 事件摘要,**替换** legacy 段 |

- **为什么必须替换**:加法(L1 叠在 legacy 上)会让 `on` 臂上下文多出 **+54%~+86%(平均 +69%)**,且 6 条 L1 事件全部与 legacy 日志的 (ticker, date) 重复 → `on` 臂胜出无法区分"选得更好"还是"给得更多",而 C2 正是主攻 claim。
- **唯一例外**:L1 与 L3 都为空时(该决策之前没有任何事件)用 legacy 兜底,避免某臂完全无记忆。这只会**稀释**效应,不会放大。
- **✅ 预算口径已定稿(方案 a,2026-09-13 先于 P1 注册)**:替换之后 `on` 预算反而是 `off` 的 **69%**(少 31%),因为 L1 是压缩摘要、legacy 是完整决策+反思。**已在 P1 运行之前**把 C2 改称"**注入预算不高于基线**"(用更少上下文达到不劣,是更难满足的主张),注册说明写在 `evaluation_protocol_v2.md` §0.2,并同步 §0.3 问答口径、kill test 判定树、`killtest_protocol.md` §5/§8。
  - **已排除的做法**:调高 `on` 臂 `n_same/n_cross` 去把 token 对齐 —— 那是看着数据调参。
  - **报告要求**:每次 run 的真实 `past_context` 都存档在 `full_states_log_<date>.json`,论文须报告**两臂实测注入字符/token 及比值**,不能只声称"不更高"。

---

## 3. 指标与统计

| 指标 | 定义 |
|---|---|
| **方向命中率 hit** | 评级 ∈ {Buy, Overweight} → 期望 alpha > 0;∈ {Sell, Underweight} → 期望 alpha < 0;Hold 不计入分子分母(另报 `hold%`) |
| **已实现 alpha** | 决策日之后 5 个交易日收益 − 区域基准(`.SS→000001.SS`,`.SZ→399001.SZ`,由框架内置) |
| **token/成本** | `tokens_total`、`cost_usd`(P4 的预算论据) |
| **复盘质量** | 人工评分:是否抓住真正原因 / 是否可迁移(1–5 分),仅在 `on` 臂有产出 |

统计:**配对**比较(同一 `场景 × 重复` 下 on vs off),用符号检验 / Wilcoxon;报告 `identical rating across arms` 比例作为"记忆是否真的改变了决策"的第一个证据。

> ⚠️ 措辞纪律:注入审计只能证明"记忆进了 prompt";**"记忆改变了决策/提升了结果"必须由配对结果支撑**,不能混为一谈。

---

## 4. 协议合规(对照 ESWA 综述的审计清单)

| 项 | 本方案如何满足 |
|---|---|
| 时间一致切分 | 记忆构建集日期 **早于**测试集日期;参数不调优(阈值/上限等在研究前固定并记录) |
| 交易成本 | 明确声明:**无成本模型**(只报方向与 alpha),不假装精确 |
| 幸存者处理 | universe 由人工选定的 A 股标的构成,声明"按 2026 年可交易标的挑选,未做退市回溯" |
| 执行语义 | 决策按当日收盘信息、按 5 日持有窗口结算(与 `_fetch_returns` 一致) |
| 可复现 | 固定 provider / 模型 / 温度 / 阈值;实验配置随结果一起存档 |
| 报告 | 报告 N、每次 run 的 token/成本/耗时、失败 run 的 error |

---

## 5. 操作流程

```powershell
cd D:\project_create\TradingAgents

# 0) 固定 provider(实验期间不得更换;见 §7)
#    验证:python -c "from tradingagents.default_config import DEFAULT_CONFIG as c; print(c['llm_provider'], c['deep_think_llm'], c['quick_think_llm'])"

# 1) 预演:护栏 + 场景打分 + 成本/耗时估算(不调用 LLM)
& 'D:\jiqixuexi\envs\tradingagents\python.exe' scripts/run_experiment.py `
    --scenarios exp_scenarios.json --reps 3 --dry-run

# 2) 构建记忆(对构建集场景:一次分析 + 一次结算)
& '...\python.exe' scripts/cost_accounting.py --ticker 300750.SZ --date 2026-03-04 --analysts market,social,fundamentals --progress
& '...\python.exe' scripts/settle_now.py  --ticker 300750.SZ

# 3) 跑配对 A/B
& '...\python.exe' scripts/run_experiment.py `
    --scenarios exp_scenarios.json --reps 3 `
    --analysts market,social,fundamentals --progress --heartbeat 60

# 4) 查看结果
#    实验行:experiments/p1_runs.csv    共享成本账:cost_log.csv
```

### 执行器的三道护栏(避免"白跑一晚")

| 护栏 | 触发条件 | 行为 |
|---|---|---|
| **LEAK** | 测试场景自身已有 EVENT | 跳过该场景并打印原因 |
| **EMPTY** | 记忆库无任何事件 → `on` 臂等同 `off` | 直接 abort(除非 `--force`) |
| **PENDING** | 这些标的还有未结算条目 | 警告并列出(结算会给先跑的臂多加一次反思调用) |

---

## 6. 成本 / 时间现实(必须先看这个再开跑)

耗时口径:只统计 `cost_log.csv` 里的 **analysis 行**。结算行(`pass=settle`)只有 ~260s,
早期版本的 `_print_estimate` 把它平均了进去,得出 1055s/run —— **低估了近 30%**。
改为只统计 analysis 行后(2026-09-12 重算):

| 配置 | 单次 analysis run | 6 run pilot(3 场景 × 1 重复 × 2 臂) | 18 run 主实验(3 场景 × 3 重复 × 2 臂) |
|---|---|---|---|
| DeepSeek `deepseek-flash` + 全分析师(实测 1453s) | ~24 min / ~$0.13 | **≈ 145 min** | **≈ 7.3 h / ~$2.3** |
| 降配档(1 轮辩论 + 2 分析师,待实测) | ~8 min(估) | ≈ 48 min | ≈ 2.4 h |

`--est-per-run <秒>` 可用实测值直接覆盖估算。

**结论**:成本不是瓶颈,**墙钟时间是瓶颈**。四个可用杠杆(需在论文中声明):
1. 换更快的模型/provider(已完成:DeepSeek `deepseek-flash`,全实验期固定不变);
2. 降低 pipeline 规模:`TRADINGAGENTS_MAX_DEBATE_ROUNDS=1` / `MAX_RISK_ROUNDS=1` 或裁剪分析师
   (`--analysts`) —— 但会削弱辩论痕迹(而复盘依赖它),须作为声明变量,且**两臂必须同档**;
3. 减少 reps/场景数:先跑 pilot(3 场景 × 2 臂 × **1** 重复 = 6 run),用观察到的方差再定正式重复数;
4. 并发:记忆路径可由 env 覆盖(`TRADINGAGENTS_LESSONS_PATH` / `_MEMORY_LOG_PATH` /
   `_RESULTS_DIR`),因此多进程隔离并发是可行的 —— 但**必须先确认冻结记忆生效**,否则并发写
   同一个 `trading_memory.md` 会互相覆盖。

---

## 7. Provider 纪律(硬规则)

- **实验期间不得更换 provider/模型**;中途换 = 混淆变量,结论作废。
- 换 provider 必须在**跑正式实验之前**完成,并记录:provider、deep/quick 模型 ID、温度、是否使用 thinking 模式。
- 内容审核(如 GLM 1301)会确定性打断 run → 已有缓解:审核感知重试 + 复盘失败时仍写占位事件(不丢极端事件)+ `--analysts` 裁剪。**若某 provider 持续拦截,应作为限制声明,而不是静默换 provider。**
- 若更换 provider 后重录 Demo,应说明"演示与实验使用同一 provider"。

### 7.1 冻结配置清单(论文必须原样报告)

| 项 | 值 | 备注 |
|---|---|---|
| provider | `deepseek` | 账户 `/models` 实际提供 `deepseek-flash`、`deepseek-v4-pro` |
| deep / quick 模型 | `deepseek-flash` / `deepseek-flash` | 同档 |
| **管线思考模式** | **关闭**(`TRADINGAGENTS_DEEPSEEK_THINKING=false`) | 省输出 token;思考 token 按输出计费 |
| **复盘思考模式** | **开启**(`TRADINGAGENTS_POSTMORTEM_THINKING=true`) | 复盘读完整辩论痕迹、每语料只跑几次,最值得花推理 token |
| 复盘档位 | `postmortem_llm=quick` | 走 quick 档但单独开思考 → 必须给它独立客户端(已实现) |
| 温度 | 见 `TRADINGAGENTS_TEMPERATURE` | 未设则各 provider 默认 |
| 关闭的数据源 | `social,macro,prediction_markets` | **两臂同档** |
| 输出语言 | Chinese | 影响 token 统计,须与基线一起报告 |

- **思考开关也是实验变量**:两臂必须同档。`postmortem_thinking` 默认 `None`(=继承 `deepseek_thinking`),显式设值才分层。
- 深度模型的能力差异:**思考开启的 DeepSeek 拒绝 `tool_choice`**;客户端会为思考型号自动抑制该参数(见 `llm_clients/capabilities.py`)。模型 ID 若不在能力表里会退回默认(发送 `tool_choice`)并触发 `400 Thinking mode does not support this tool_choice` —— 换模型前先确认能力表已登记。

---

## 8. 阶段计划(三层合一)

| 阶段 | 内容 | 产出 |
|---|---|---|
| **E0 预演** | `--dry-run`;确认护栏、场景打分、估算 | 计划数(场景/重复/成本/时长) |
| **E1 记忆构建** | 对构建集跑分析 + 结算,得到 ≥2 条 EVENT(建议含 1 盈利 + 1 亏损) | `trading_lessons.md` 内容 + `check_injection_audit` 证据 |
| **E2 Pilot(P1)** | 1 场景 × 2 臂 × 2 重复 | 首个配对结果 + 全流程打通 |
| **E3 主实验 P1** | 3–5 场景 × 2 臂 × 3–5 重复(冻结记忆) | `experiments/p1_runs.csv` + 配对统计 |
| **E4 P4 / P5** | token 预算对比 + 复盘摘要人工评分 | 预算表 + 质量分 |
| **D1 实现 L3(v2)** | 蒸馏队列 → 准则库(自包含、可修订)→ preamble 注入;淘汰→蒸馏→降级闭环 | 代码 + 单测 |
| **E5 消融 P2** | L3 开/关(依赖 E1/E3 积累出淘汰队列内容) | P2 结果 |
| **D2 实现 L2(v3)** | 状态指纹写入条目 + 逐维标准化余弦 + 两段式检索 + 粗桶降级 | 代码 + 单测 |
| **E6 消融 P3** | A 纯相似度 / B 相似度×recency^γ / C 同标的最近 n 条平权(需 ≥30 条已结算事件) | P3 结果 |
| **E7 汇总写作** | 三层架构 + P1–P5 + 协议章节 + 泛化小节 | 论文初稿 |

**并行建议**:D1/D2 的开发期可以同时用 `settle_now.py` 廉价累积语料(每次约 $0.005–0.02),这样等 E5/E6 开始时语料就绪,不必干等。

---

## 9. 风险清单

| 风险 | 缓解 |
|---|---|
| 一次 run 30+ 分钟 → 实验排期失控 | 换快 provider;先 pilot;必要时裁剪 pipeline(声明) |
| provider 内容审核中断 run | 重试 + 占位事件 + 裁剪 analysts;持续拦截则声明为限制 |
| yfinance 限流导致打分失败 | 打分带重试(`run_experiment.resolve_realized`);必要时错峰重跑 |
| 记忆库为空导致两臂等同 | EMPTY 护栏 abort(见 §5) |
| 自己喂了自己结果(泄漏) | LEAK 护栏跳过(见 §2.2) |
| 样本太小、结论不显著 | 报告效应量与置信区间;不显著也如实写(P4 预算收益仍成立) |

---

## 10. 论文结构(三层合一的目标形态)

1. **Intro**:多智能体交易框架的记忆缺口(现状只有 2–4 句自我反思、且看不到辩论过程);
2. **Related work**:FinMem / TradingGPT / Generative Agents / MemoryBank / Reflexion + regime 检测文献(见 `positioning_and_related_work.md` §4);
3. **架构**:三层 + 两个核心耦合点 —— ① 复盘读**全队辩论痕迹**;② **蒸馏补偿遗忘**的巩固闭环(附 L2 两段式检索设计);
4. **实现与预算约束**:摘要上限、保护集、方向下限、蒸馏队列、检索门控;
5. **实验**:P1–P5 消融 + 协议合规(时间一致切分 / 成本声明 / 幸存者 / 可复现)+ 多市场泛化(可选);
6. **结果与讨论**:含"复盘抓到原管线缺陷"的定性证据(跨节点逻辑矛盾、算术不一致);
7. **局限**:数据可得性(`positioning_and_related_work.md` §8)、provider 内容审核、样本量。
