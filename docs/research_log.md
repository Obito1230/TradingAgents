# 科研日志 · TradingAgents 三层记忆链

> **一句话目标**：把多智能体交易框架里"只有 2–4 句自我反思"的单层决策日志，重做成
> **L1 事件记忆 / L2 情境检索 / L3 季度蒸馏**的三层记忆链，并**三层一起**投一篇论文
> （EI 会议起步，CCF-C 稳妥、CCF-B 争取），同批材料做计算机设计大赛 Demo。
>
> **本文件是唯一的进度账本。** 状态：进行中 · 最后更新 **2026-09-12**
> 配套：`memory_three_layer_design.md`（设计）· `evaluation_protocol_v2.md`（验证协议）·
> `positioning_and_related_work.md`（定位）· `experiment_plan.md`（实验计划）·
> `v1_implementation_guide.md`（实施）· `competition_implementation_roadmap.md`（比赛排期）·
> `killtest_protocol.md`（判定树）· `demo1_recording_script.md`（录屏脚本）
>
> 注：`killtest_protocol.md` §0.2 提到的 `experiment_log.md` 由**本文件**承担。

---

## 1. 进度总览

**总进度 ≈ 36%**（分母 = "论文投出 + 比赛交件"）

| 视角 | 百分比 | 含义 |
|---|---|---|
| **整体**（含实验 + 论文 + Demo） | **≈ 36%** | 论文能投出去才算 100% |
| **纯工程实现**（L1+L2+L3+脚本） | **≈ 48%** | L2 完全没动，是最大的空洞 |
| **L1 单层**（本文核心机制之一） | **≈ 75%** | 代码+测试完备，缺真实语料与 A/B 证据 |
| **实验证据**（论文的命门） | **≈ 15%** | 只有工装，**零数据** |

### 1.1 分项进度表（权重是可审计的，改权重请改这张表）

| # | 工作项 | 权重 | 完成度 | 加权 | 判定依据 |
|---|---|---|---|---|---|
| 1 | 选题定位 / 相关工作 / 三层架构设计 | 8% | 85% | 6.8% | `memory_three_layer_design.md`、`positioning_and_related_work.md` 已成稿；§8 数据可得性仍待定 |
| 2 | 验证协议（C1–C3 claim 体系 + kill test 判定树） | 7% | 80% | 5.6% | `evaluation_protocol_v2.md` 已把主线从"更省 token"修正为"有界性/同等预算质量/遗忘保护"；判定树未执行 |
| 3 | **L1 事件记忆**（复盘 agent / 事件库 / 遗忘保护 / 注入） | 20% | 75% | 15.0% | 代码+测试齐（见 §3.1）；**语料只有 1 条 EVENT**，真实规模未验证 |
| 4 | **L3 季度蒸馏** | 12% | 5% | 0.6% | 只有 `distill_queue_path` 与 `get_rules_context()` 占位（返回 ""） |
| 5 | **L2 情境检索** | 15% | 0% | 0.0% | 未开始 |
| 6 | 实验基础设施（护栏 / 扫描 / 记账 / 结算 / 冻结） | 8% | 70% | 5.6% | `run_experiment.py` 等已就绪并 dry-run 验证；缺 L2/L3 臂的 condition→config 映射与并发隔离 |
| 7 | **实验数据**（P1–P5 消融） | 15% | 0% | 0.0% | 0 条实验行 |
| 8 | 论文撰写 + 投稿 | 10% | 8% | 0.8% | `experiment_plan.md` §10 有骨架，无正文 |
| 9 | 竞赛 Demo（视频 + 材料） | 5% | 30% | 1.5% | 录屏脚本 `demo1_recording_script.md` 已写，素材就绪，**未录** |
| | **合计** | **100%** | | **≈ 36%** | |

---

## 2. 现在站在哪一步

**站在 E1 的门口**（阶段名沿用 `experiment_plan.md` §8）：

```
E0 预演 ✅ ──> [E1 记忆构建] ← 你在这里 ──> E2 Pilot ──> E3 主实验 ──> E5/E6 消融 ──> E7 写作
                    ▲                                            ▲
                    └── D1 实现 L3 ──────────────────────────────┘
                    └── D2 实现 L2 ──────────────────────────────┘
```

- L1 代码**已完成并冻结**（623 passed / 1 skipped）；
- 实验工装**已完成**，`--dry-run` 全绿；
- 语料**没建**（这就是"剩下的语料我还没跑"）→ 卡在这里；
- L2/L3 **未实现** —— 按"三层一起发"的决定，它们必须在投稿前完成并各自有消融。

---

## 3. 已完成（带证据）

### 3.1 L1 事件记忆（代码层）

| 能力 | 落点 | 证据 |
|---|---|---|
| 复盘 agent 读**全队辩论痕迹**（不是只看决策） | `agents/utils/postmortem_agent.py` | `tests/test_postmortem_agent.py` |
| 5 字段结构化复盘（决策摘要/依据/漏判/可迁移教训/自评） | `agents/schemas.py` `EventPostmortem` | 总长目标 ≤900 字符 |
| 事件库：元数据 + 摘要 + 指向 `full_states_log_<date>.json` 的指针 | `agents/utils/memory.py` `store_event` / `load_lessons` | `tests/test_lessons_store.py` |
| **保护集 + 方向下限**（大亏不会被常规淘汰误杀） | `_evict_to_cap` / `_select_direction_balanced` | `tests/test_maintenance.py`（含 `test_protected_loss_never_evicted`） |
| 淘汰 → **蒸馏队列**（不暴力丢，留给 L3 补偿） | `_append_distill_queue` | 同上 |
| 精确重复去重（**故意不做语义去重**，见 §6） | `_dedupe_events` | `test_distinct_events_not_over_merged` |
| 注入预算硬化（摘要硬上限 1600 字符） | `_cap_summary` | `tests/test_injection_budget.py` |
| 阈值触发（|alpha| ≥ 5% 才记忆，保护门槛 10%） | `default_config.py` | `tests/test_lessons_context.py` |
| 生成失败时写占位事件（**不丢极端事件**） | `_maybe_write_event` | GLM 1301 事故后的加固 |
| 消融开关 `event_memory_enabled` | `default_config.py` | `tests/test_memory_ablation_switch.py` |
| **冻结记忆** `memory_readonly`（A/B 专用，防自泄漏） | `memory.py` + `trading_graph.py` + `run_experiment.py` | `tests/test_memory_readonly.py` |

### 3.2 工程与数据适配（踩过的坑都在这）

- **数据源**：yfinance 在本网络对 A 股/美股 `yf.download` 返回空 → 改 `Ticker.history` 主路径 +
  `download` 兜底 + 365 天分块（`dataflows/stockstats_utils.py`）；
- **时区**：`Ticker.history` 返回 Asia/Shanghai 感知索引 → `_to_naive_dates()` **丢弃时区而不做 UTC 换算**
  （换算会把 A 股日期退一天）；已验证 `load_ohlcv('300750.SZ','2026-03-04')` close=332.4194 与扫描一致；
- **基准**：`alpha = 原始收益 − 区域基准`（`.SS→000001.SS`、`.SZ→399001.SZ`、美股→SPY），结算窗口 5 日；
- **内容审核**：GLM 400/1301 曾杀死 387s/904s 的 run → 审核感知重试 + 占位事件；
- **Provider 固定**：DeepSeek `deepseek-flash`（deep/quick 同档），`postmortem_llm=quick`；
  **实验期内不得再换**（`experiment_plan.md` §7）。

### 3.3 实验工装

`scripts/`：`scan_demo_pool.py`（候选场景扫描）· `run_pool.py`（批量跑+结算）·
`cost_accounting.py`（token/费用/墙钟记账 + Heartbeat）· `settle_now.py`（廉价结算）·
`verify_l1.py`（4 项验收）· `show_injection.py` / `check_injection_audit.py`（注入证据）·
**`run_experiment.py`**（配对 A/B，含 LEAK / EMPTY / PENDING 三道护栏 + dry-run 估算）。

### 3.4 测试基线

```
623 passed, 1 skipped, 0 failed        （排除慢速 tests/test_structured_agents.py）
其中本期新增/重写的记忆相关测试 62 项，分布在 9 个文件
```

---

## 4. 当前卡点与已解决的坑

| # | 状态 | 事项 | 处理 |
|---|---|---|---|
| 1 | 🔴 **卡点** | **语料只有 1 条 EVENT**，且是 GLM 时代产物；两条 pending 决策（`AAPL@2026-01-15`、`300750.SZ@2026-04-08`）待结算 | 见 §5 N1，零 LLM 成本可先捡 2 条 |
| 2 | ✅ 已修 | **A/B 自泄漏**：`propagate()` 写的 pending 会被下一次同 ticker 的 run 结算 → 为**被测场景自己**写 EVENT → `on` 臂 rep≥2 读到自己结果；启动时护栏抓不到 | 新增 `memory_readonly`；所有写路径 no-op + 跳过结算 + **每个场景开跑前重查 LEAK** |
| 3 | ✅ 已修 | **耗时估算低估 30%**：`_print_estimate` 把 258s 的结算行平均进了 analysis 行 | 只统计 analysis 行；新增 `--est-per-run` |
| 4 | ✅ 已修 | **测试覆盖真 key 事故**：`--basetemp` 落在仓库内时 `find_dotenv(usecwd=True)` 上溯命中真 `.env`，测试把 `DEEPSEEK_API_KEY` 写成假值 | 已重填 key；fixture 禁用 `find_dotenv`；**教训：pytest 临时目录永远不要放进仓库** |
| 5 | ⚠️ 待决策 | **kill test 是 120 runs**（20 配对 × 3 轮 × 2 臂）；按 24 min/run = **48 小时墙钟** | 见 §5 N3 |
| 6 | ⚠️ 待确认 | A 股情绪/新闻源为空（`disabled_sources=social,macro,prediction_markets`），美股数据被 yfinance 封 | 作为**数据可得性限制**声明，不静默换数据 |

---

## 5. 下一步（按顺序，可复制）

### N0 · 今天收尾（~15 min）

```powershell
cd D:\project_create\TradingAgents
# 1) 确认 key 已恢复（联网测试通过即证明 key 可用）
& 'D:\jiqixuexi\envs\tradingagents\python.exe' -m pytest tests/test_deepseek_reasoning.py -q
# 2) 全量回归
& 'D:\jiqixuexi\envs\tradingagents\python.exe' -m pytest tests -q --ignore=tests/test_structured_agents.py
# 3) 提交本期改动（冻结记忆 / 估算修正 / 摘掉 env override / 文档）
git add -A; git commit -m "feat(memory): freeze-memory mode for A/B, fix run estimate, drop memory_readonly env override"
```

**同时要做的决定**：实验跑**全配置**（24 min/run）还是**降配档**（`MAX_DEBATE_ROUNDS=1` +
`MAX_RISK_ROUNDS=1` + `--analysts market,news`，估 ~8 min/run）？这个决定直接决定 §5 N3 能否排得下。
**两臂必须同档，且必须写进论文的 experiment profile。**

### N1 · E1 建语料（**当前这一步**，1.5–2.4 h 全配置 / ~0.5 h 降配）

```powershell
# 1) 备份 GLM 时代的 1 条 EVENT
Copy-Item C:\Users\dc213\.tradingagents\memory\trading_lessons.md `
          C:\Users\dc213\.tradingagents\memory\trading_lessons.md.glm.bak

# 2) 白捡 2 条：这两条 pending 本来就有 full_states_log，不用重跑图
& 'D:\jiqixuexi\envs\tradingagents\python.exe' scripts/settle_now.py --ticker 300750.SZ  # 2026-04-08 Overweight
& 'D:\jiqixuexi\envs\tradingagents\python.exe' scripts/settle_now.py --ticker AAPL       # 2026-01-15 Buy

# 3) 跑剩余语料（必须是**非测试场景**，即不在 exp_scenarios.json 里的 ticker/date）
& 'D:\jiqixuexi\envs\tradingagents\python.exe' scripts/run_pool.py `
    --pool demo_pool_ashare.json --settle --progress
```

**验收标准**（全过才进 N2）：
- `trading_lessons.md` 里 EVENT 数 **≥ 6**，且**至少 1 条是亏损/看空方向**（否则 L2/L3 的方向平衡与保护集都没东西可证）；
- `settle_now.py --ticker <T> --dry-run` 显示 pending = 0（没有悬挂结算）；
- `check_injection_audit.py --ticker <T> --date <D>` 能在 prompt 里看到 L1 段落。

> 若看到 yfinance 报 `unable to open database file`：那是 DSH 沙箱挡了 sqlite 缓存，不是代码问题，在自己 shell 里跑即可。

### N2 · E0 预演 + E2 Pilot（~145 min 全配置 / ~48 min 降配）

```powershell
& 'D:\jiqixuexi\envs\tradingagents\python.exe' scripts/run_experiment.py `
    --scenarios exp_scenarios.json --reps 1 --progress
```
- dry-run 先确认：`planned runs = 6`、无 `[SKIP-LEAK]`、三个场景都能打分；
- 产出 `experiments/p1_runs.csv` + 配对表 → **用观察到的方差决定正式 reps**，不要拍脑袋定 3。

### N3 · 定 kill test 规模（**要跟老师确认的决策点**）

现在 `killtest_protocol.md` 写的是 **120 runs**。按实测 24 min/run = **48 h 墙钟**，加消融明显排不下。三条路：

| 方案 | 规模 | 墙钟（全配置） | 代价 |
|---|---|---|---|
| 降配档 | 120 runs | ~16 h | 辩论轨迹变短 → **复盘质量下降**（复盘正是卖点），必须声明 |
| 3–4 进程并发 | 120 runs | ~12–16 h | 需先确认冻结记忆生效 + 每进程独立记忆路径，否则互相覆盖 |
| 砍配对 | 60–72 runs | ~24–29 h | 统计功效下降 → 更可能只能报效应量+CI，拿不到显著性 |

**建议**：先用 pilot 的方差算出"要多少配对才有功效"，再倒推规模；把"降配档 + 并发"当默认，
全配置只用来跑最终论文数字。**这个问题适合直接问老师。**

### N4 · D1 实现 L3 季度蒸馏（可与 N1 语料累积并行）

- 蒸馏队列 → 自包含、可修订的准则库（`max_rule_entries=50`）→ preamble 注入；
- 闭环必须打通：**淘汰 → 蒸馏 → 降级**，否则"蒸馏补偿遗忘"这个核心耦合点无法论证；
- 验收：`get_rules_context()` 返回真实准则；构造数据证明被淘汰的事件其信息进入了准则库。

### N5 · D2 实现 L2 情境检索

- 事件条目写入状态指纹 → 逐维标准化 → 余弦相似度 → 加权 softmax 检索 → 粗桶降级兜底；
- **必须解决"长牛市把熊市记忆挤出去"**：与 N1 的"至少 1 条亏损事件"、保护集、方向下限联动验证。

### N6 · E5/E6 消融（依赖 N1 攒够 ≥30 条已结算事件）

- P2：L3 开/关；P3：A 纯相似度 / B 相似度×recency^γ / C 同标的最近 n 条平权。

### N7 · E7 汇总写作 · N8 · Demo 视频

- 写作骨架见 `experiment_plan.md` §10；
- Demo 素材已备好（`demo1_recording_script.md`），**建议在 L2/L3 实现前先录一版 v1**，防止后期返工无片可交。

---

## 6. 关键决策记录（别再重新讨论一遍）

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-09-0x | 记忆改成**三层**，且**三层一起**投稿，不把 L2/L3 降级为 future work | "一步一步来"≠分篇发；只有外部硬 deadline 才收窄主张 |
| 2026-09-1x | Provider 定为 DeepSeek `deepseek-flash`，实验期冻结 | 中途换 = 混淆变量，结论作废 |
| 2026-09-11 | 去重只做**精确重复**，语义去重推迟到 v3 | 曾按"同标的同评级 30 天内"合并，把 1 亏 5 赢塌成 1 条，**摧毁熊市记忆** |
| 2026-09-11 | 放弃"省 token"作为卖点 | ACON/SimpleMem 是真压缩且量纲不同，比不过；改为 C1 有界性 + C2 同等预算质量 + C3 遗忘保护 |
| 2026-09-12 | 加 `memory_readonly` 冻结记忆 | 不冻结则 A/B 自泄漏（见 §4 #2） |
| 2026-09-12 | **不**给 `memory_readonly` 开 env override | 冻结是**静默失败**：run 照样成功但不记任何东西。只能由 harness 在代码里设 |
| 2026-09-12 | 保留 legacy 决策日志注入在**两臂都有** | 保证两臂唯一差异就是 L1，比较才干净 |

---

## 7. 待问老师的问题

1. kill test 的规模与统计功效：120 runs 排不下时，优先"降配 + 并发"还是"砍配对"？两者都要在论文里声明，哪个更可接受？
2. 降配档（缩短辩论轮数）会削弱复盘输入，是否可以作为主实验配置？
3. v1 用 **alpha 符号**做市场状态代理，是否可接受？（L2 的完整状态向量在 v3，需要声明这个降级）
4. 是否需要引入 A 股新闻/情绪数据源（当前 A 股情绪源为空，是显著的数据可得性缺口）？
5. 比赛 Demo 与论文可否共用同一批实验数据（避免两套数字不一致）？

---

## 8. 风险与排期

| 风险 | 现状 | 缓解 |
|---|---|---|
| **墙钟时间是唯一真瓶颈** | 24 min/run；120-run kill test = 48 h | §5 N3 三选一；先 pilot 估方差 |
| L2/L3 未实现就投 | 27% 权重为 0 | 按 N4/N5 推进，且开发期用 `settle_now.py` 并行攒语料 |
| 语料方向失衡（全是盈利） | 现有 1 条是 +13.1% 盈利 | N1 验收标准强制"≥1 条亏损" |
| 样本太小、结论不显著 | 未知 | 报告效应量 + CI，不显著也如实写；C1/C3 不依赖 LLM 效果，仍成立 |
| provider 内容审核中断 run | 已有重试 + 占位事件 | 持续拦截则声明为限制，不静默换 provider |
| 自己喂自己结果 | ✅ 已修（冻结记忆） | 每场景开跑前重查 LEAK |
| 数据可得性（A 股情绪源空、美股被封） | 待定 | 并入 §7 问题 4；作为限制声明 |

---

## 9. 更新约定

- 每次推进后**只改三处**：§1 的完成度与加权、§3/§4 的证据与卡点、§5 的下一步勾选；
- **改了权重必须说明为什么**（权重是可审计的分母，不许悄悄调）；
- 事故与返工**必须留痕**（如 §4 #4 的 key 覆盖事故）——这是科研日志不是宣传稿；
- 阶段进展用 `experiment_plan.md` §8 的 E/D 编号，不要另起一套命名。
