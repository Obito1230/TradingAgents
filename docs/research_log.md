# 科研日志 · TradingAgents 三层记忆链

> **一句话目标**：把多智能体交易框架里"只有 2–4 句自我反思"的单层决策日志，重做成
> **L1 事件记忆 / L2 情境检索 / L3 季度蒸馏**的三层记忆链，并**三层一起**投一篇论文
> （EI 会议起步，CCF-C 稳妥、CCF-B 争取），同批材料做计算机设计大赛 Demo。
>
> **本文件是唯一的进度账本。** 状态：进行中 · 最后更新 **2026-09-13**
> 配套：`memory_three_layer_design.md`（设计）· `evaluation_protocol_v2.md`（验证协议）·
> `positioning_and_related_work.md`（定位）· `experiment_plan.md`（实验计划）·
> `v1_implementation_guide.md`（实施）· `competition_implementation_roadmap.md`（比赛排期）·
> `killtest_protocol.md`（判定树）· `demo1_recording_script.md`（录屏脚本）
>
> 注：`killtest_protocol.md` §0.2 提到的 `experiment_log.md` 由**本文件**承担。

---

## 1. 进度总览

**总进度 ≈ 40%**（分母 = "论文投出 + 比赛交件"）

| 视角 | 百分比 | 含义 |
|---|---|---|
| **整体**（含实验 + 论文 + Demo） | **≈ 40%** | 论文能投出去才算 100% |
| **纯工程实现**（L1+L2+L3+脚本） | **≈ 68%** | L2 完全没动，是最大的空洞 |
| **L1 单层**（本文核心机制之一） | **≈ 90%** | 代码+测试+语料（6 条唯一事件）齐备；只差 P1 跑通，**且论文级证据仍需重选评估集** |
| **实验数据**（论文的命门） | **≈ 5%** | 语料已就绪（6 条已结算事件），A/B 消融仍零数据 |

### 1.1 分项进度表（权重是可审计的，改权重请改这张表）

| # | 工作项 | 权重 | 完成度 | 加权 | 判定依据 |
|---|---|---|---|---|---|
| 1 | 选题定位 / 相关工作 / 三层架构设计 | 8% | 85% | 6.8% | `memory_three_layer_design.md`、`positioning_and_related_work.md` 已成稿；§8 数据可得性仍待定 |
| 2 | 验证协议（C1–C3 claim 体系 + kill test 判定树） | 7% | 80% | 5.6% | `evaluation_protocol_v2.md` 已把主线从"更省 token"修正为"有界性/同等预算质量/遗忘保护"；判定树未执行。**注意 §4 #19：当前两臂设计与 C2 的"同等预算"口径不一致，待决策** |
| 3 | **L1 事件记忆**（复盘 agent / 事件库 / 遗忘保护 / 注入） | 20% | 90% | 18.0% | 代码+测试齐（§3.1、§3.4）；语料 **6 条唯一 EVENT**（2 亏损、1 受保护大亏 −14.7%）；三个测试场景经注入实测确认无未来日期（§4 #20）；**差 P1 运行** |
| 4 | **L3 季度蒸馏** | 12% | 5% | 0.6% | 只有 `distill_queue_path` 与 `get_rules_context()` 占位（返回 ""） |
| 5 | **L2 情境检索** | 15% | 0% | 0.0% | 未开始 |
| 6 | 实验基础设施（护栏 / 扫描 / 记账 / 结算 / 冻结） | 8% | 85% | 6.8% | 护栏含 LEAK/EMPTY/PENDING + 时间切片提示 + 冻结记忆 + 缓存优先取价；**LEAK 护栏已实测拦住"语料撞测试场景"**；缺 L2/L3 臂 condition→config 映射 |
| 7 | **实验数据**（P1–P5 消融） | 15% | 0% | 0.0% | 0 条 A/B 实验行（语料 6 条已结算，但不计入本行） |
| 8 | 论文撰写 + 投稿 | 10% | 8% | 0.8% | `experiment_plan.md` §10 有骨架，无正文 |
| 9 | 竞赛 Demo（视频 + 材料） | 5% | 30% | 1.5% | 录屏脚本 `demo1_recording_script.md` 已写，素材就绪，**未录** |
| | **合计** | **100%** | | **≈ 40%** | |

---

## 2. 现在站在哪一步

**站在 E2 / P1 的门口**（阶段名沿用 `experiment_plan.md` §8）：

```
E0 预演 ✅ ──> E1 记忆构建 ✅ ──> [E2 Pilot P1] ← 你在这里 ──> E3 主实验 ──> E5/E6 消融 ──> E7 写作
                                      ▲                                        ▲
                                      └── D1 实现 L3 (v2) ─────────────────────┘
                                      └── D2 实现 L2 (v3) ─────────────────────┘
```

- L1 代码**已完成并冻结**（674 passed / 1 skipped）；
- 实验工装**已完成**，`--dry-run` 全绿，护栏实测有效；
- **语料已建成**：6 条唯一 EVENT（2 亏损 / 1 受保护大亏），0 悬挂 pending；
- **三个测试场景经注入实测确认无未来日期**（§4 #20）；
- L2/L3 **未实现** —— 按"三层一起发"的决定，它们必须在投稿前完成并各自有消融。
- **P1 前有两个待决策项**：臂设计口径（§4 #19）与是否修短窗结算（§4 #21）。见 §5 N2 前的门禁。

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
  **实验期内不得再换**（`experiment_plan.md` §7）；
- **思考模式分层开关**：`TRADINGAGENTS_DEEPSEEK_THINKING=false`（全管线关）+
  `TRADINGAGENTS_POSTMORTEM_THINKING=true`（复盘单独开）。实测真实图三档 extra_body：
  deep/quick = `{'thinking': {'type': 'disabled'}}`，postmortem = `None`（供应商默认＝开）。

### 3.3 实验工装

`scripts/`：`scan_demo_pool.py`（候选场景扫描）· `run_pool.py`（批量跑+结算）·
`cost_accounting.py`（token/费用/墙钟记账 + Heartbeat）· `settle_now.py`（廉价结算）·
`verify_l1.py`（4 项验收）· `show_injection.py` / `check_injection_audit.py`（注入证据）·
**`run_experiment.py`**（配对 A/B，含 LEAK / EMPTY / PENDING 三道护栏 + 时间切片提示 + dry-run 估算）。

### 3.4 时间一致性与幂等（2026-09-13 新增，实验有效性的前提）

| 能力 | 落点 | 证据 |
|---|---|---|
| **时间切片**：决策日之后（含当日）的记忆一律不注入 | `memory.py` `get_lessons_context(as_of=)` / `get_past_context(as_of=)`、`trading_graph._build_past_context(ticker, as_of=)` | `tests/test_memory_time_slice.py` |
| 两臂都切片（唯一差异仍是 L1 内容） | 同上 | 同上 |
| **entry_id 幂等**：同一 `E-<date>-<ticker>` 只写一次 | `store_event` 原始文本扫描（同 `store_decision` 的手法） | 同上 |
| **修复历史重复**：同一 entry_id 只保留首条，被移除者进蒸馏队列 | `_dedupe_events` | 同上 |
| **切片读取不得销毁数据**：`get_lessons_context` 会回写命中计数，回写时必须用**全量**条目而非切片子集 | `_rewrite_lessons(all_entries)` | 同上 |
| `as_of` 接受 `datetime`/`date`/字符串，统一截断到 `YYYY-MM-DD` | `memory._as_date` | 同上（含"同日不得注入"回归） |
| 审计工具同口径：`show_injection.py --date`（必填）、`verify_l1.py` 用 `as_of=事件日+1d` | `scripts/` | 端到端实测见 §4 #11 |
| **冻结模式下 GET 也不写盘**：`_rewrite_lessons` 是唯一写入口，统一加 `_readonly` 短路 | `memory._rewrite_lessons` | 实测：真实记忆库 sha256 前后一致，且返回 6705 字真实记忆；对照组（可写、落在副本上）确实改文件 |
| **`as_of` 是必填关键字参数**（不是可选默认 `None`） | `trading_graph._build_past_context(ticker, *, as_of)` | 省略即 `TypeError`；防止未来调用方静默退回"无过滤" |
| **L3 注入点也接受 `as_of`**（v1 返回空串，但签名先立好） | `memory.get_rules_context(as_of=None)` | 防止 L3 实现时把"未来蒸馏出的准则"注入到更早的决策（同一类 bug 上一层） |

### 3.5 测试基线

```
674 passed, 1 skipped, 0 failed        （排除慢速 tests/test_structured_agents.py）
```

---

## 4. 当前卡点与已解决的坑

| # | 状态 | 事项 | 处理 |
|---|---|---|---|
| 1 | ✅ 已完成 | 语料：4 条**唯一** EVENT（1 条亏损：`601318.SS Sell −7.3%`）+ 1 条重复已定位 | 下一步见 §5 N2（重挑测试场景） |
| 2 | ✅ 已修 | **A/B 自泄漏**：`propagate()` 写的 pending 会被下一次同 ticker 的 run 结算 → 为**被测场景自己**写 EVENT → `on` 臂 rep≥2 读到自己结果；启动时护栏抓不到 | 新增 `memory_readonly`；所有写路径 no-op + 跳过结算 + **每个场景开跑前重查 LEAK** |
| 3 | ✅ 已修 | **耗时估算低估 30%**：`_print_estimate` 把 258s 的结算行平均进了 analysis 行 | 只统计 analysis 行；新增 `--est-per-run` |
| 4 | ✅ 已修 | **测试覆盖真 key 事故**：`--basetemp` 落在仓库内时 `find_dotenv(usecwd=True)` 上溯命中真 `.env`，测试把 `DEEPSEEK_API_KEY` 写成假值 | 已重填 key；fixture 禁用 `find_dotenv`；**教训：pytest 临时目录永远不要放进仓库** |
| 5 | ⚠️ 待决策 | **kill test 是 120 runs**（20 配对 × 3 轮 × 2 臂）；按 24 min/run = **48 小时墙钟** | 见 §5 N3 |
| 6 | ✅ 已修 | **反复出现的 HTTP 400**：真因不是"思考模式与本框架不兼容"，而是 `deepseek-flash` 不在能力表里 → 落到 `_DEFAULT`（`supports_tool_choice=True`）→ 每次结构化调用都送 `tool_choice` → API 回 `400 Thinking mode does not support this tool_choice` | ① 把 `deepseek-flash` 登记为 `_DEEPSEEK_THINKING`（**这才是根治**：思考开着也不再发 `tool_choice`）；② 新增分层开关：管线关思考（省 token），复盘保留思考。**实测**：用真实 258KB 辩论痕迹跑真实复盘 agent，thinking=True 与 False **都不报 400**；且开思考那版明显更锐利（点名 EPS 25.01/+33%、200 日线 318.97、Beta 0.43、319/324.5/327.8 三档仓位，自评还点出"把 FY2025 硬着陆风险弱化为需观察"） |
| 7 | 🔴 **卡点** | **语料与测试场景时间不兼容**：修好时间切片后，`601318.SS@2026-06-15` 因自身结果在库里被 SKIP-LEAK；`600519.SS@2026-01-22` 的同标的 EVENT 在 01-28（比决策晚 6 天），既不能用也不该用；`000001.SZ@2026-07-17` 无同标的记忆 | **重挑测试场景**：全部晚于语料最后一条 EVENT（见 §5 N2） |
| 8 | ✅ 已修 | **重复 EVENT（互相矛盾的评级）**：`E-2026-03-04-300750.SZ` 出现两次，一次 `Buy`（GLM 时代）、一次 `Hold`（DeepSeek 重跑）。根因：`store_decision` 只在**仍有 pending** 时幂等，已结算的 (ticker, date) 重跑会再写一条 pending 并再次结算 | `store_event` 按 entry_id 幂等；`_dedupe_events` 修复历史重复（保留首条、被移除者进蒸馏队列）。已在**副本**上验证 5→4 |
| 9 | ✅ 已修 | **成本列全空**：`PRICE_USD_PER_1M` 只有 `deepseek-v4-flash`，账户实际型号 `deepseek-flash` 匹配不到 → 6 条 DeepSeek run 的 `cost_usd` 为空 | 补价目（沿用同型号价：in 0.20 / out 0.80 每 1M）；**论文成本表前需对官网复核该价格** |
| 10 | ⚠️ 待确认 | A 股情绪/新闻源为空（`disabled_sources=social,macro,prediction_markets`），美股数据被 yfinance 封 | 作为**数据可得性限制**声明，不静默换数据 |
| 11 | 🔴 **已修 + 已恢复** | **时间切片的第一版实现会销毁数据**：`get_lessons_context` 为了回写命中计数会重写整个文件，而它被传入了**切片后的子集** → 凡日期 ≥ `as_of` 的 EVENT 会被**删除**。第一次端到端验证时（`as_of='2026-05-01'`）删掉了 `601318.SS@2026-06-15 Sell −7.3%` ——**语料里唯一的亏损方向事件**。根因是我只在**单测**里验证了"未来事件不进 prompt"，没验证"读取不改记忆库" | ① `_rewrite_lessons(all_entries)` 改为回写全量；② 补 4 项回归测试（切片读取后条目数/entry_id 集合不变、其他标的存活、命中计数仍工作）；③ 被删事件**已用同一套结算路径重建**（真实 `full_states_log_2026-06-15.json` 250KB + 记录在案的 raw/alpha + 复盘 agent），恢复后 5 条 EVENT、亏损方向 1 条不变；④ 端到端断言"切片读取前后条目集合一致"通过 |
| 12 | ⚠️ 遗留 | 该事件**无法逐字还原**：摘要文本由复盘 agent 重新生成，措辞与原来不完全相同（数字/评级/alpha/指针一致） | 若要完全一致需重跑原始结算；对后续消融无影响（两臂读同一份），但**不要**把它当作"原始产物"引用 |
| 12b | ✅ 已修 | **取价被限流时评分与结算一起静默失效**：`_fetch_returns` 直连 `yf.Ticker().history()`，**既不读也不写本地 OHLCV 缓存** → yfinance 一封 IP（`YFRateLimitError`，本会话实测单条请求 0.8s 内即被拒）**打分拿不到 alpha、`settle_now` 也结算不了**。而同一台机器上 `scan_demo_pool.py` 走缓存路径照样能出数 | 新增 `_window_closes()`：**缓存优先**（`load_ohlcv`），裸请求仅作兜底。实测 **4/4 复现已结算 raw 收益**（硬禁用实时路径，数字只可能来自缓存）；新增 `tests/test_fetch_returns_cache.py` 8 项。**准确的收益口径**：历史请求命中缓存 → 同一符号**当天**重复取价零网络调用；但缓存文件按"当天"命名（5 年窗口 → 今天）**每天滚动**，次日每个符号会重新抓一次。**所以它不是永久档案** —— 论文数字产出时须把 `data_cache_dir` 复制成冻结快照，否则复权调整会让历史窗口在另一天重算结果不同 |
| 13 | ✅ 已修（审计轮） | **冻结模式并没有真的冻结**：`_rewrite_lessons` 没有 `_readonly` 守卫，而 `get_lessons_context`（读取路径）会走它回写命中计数 → A/B 每次读取都在**改写真实记忆库**。因为 `_recency_key` 的排序主键是 `last_accessed`/`hits`，这些读取还会**改变未来淘汰谁** | `_rewrite_lessons` 加统一短路（唯一写入口）；实测真实库 sha256 前后一致、对照组确认守卫非空转 |
| 14 | ✅ 已修（审计轮） | **`as_of` 是可选的**：未来任何调用方忘了传，就静默退回"无过滤"，正是刚修掉的那个泄漏的复发路径；且 `get_rules_context()` 根本没有日期参数，L3 一旦实现就会把"未来蒸馏的准则"注入更早的决策 | `_build_past_context(ticker, *, as_of)` 改为**必填关键字**（省略即 `TypeError`）；`get_rules_context(as_of=None)` 签名先立好 + 2 项回归 |
| 15 | ⚠️ **未修（需决策/知悉）** | ①**并发写**：两个进程同时写同一记忆库会互相覆盖（无文件锁）→ 语料构建必须串行；实验期靠冻结模式规避。②**淘汰优先级是"访问制"**：`_recency_key` 主键是 `last_accessed`/`hits`，`trade_date` 只排第三 → 常被读到的条目几乎免疫淘汰，而"重要但没被读到"的条目排在琐碎的新条目之后。③`HITS` 字段语义是"**被注入过**"而不是"**被采用过**"，论文里不能当成"记忆被使用"的证据。④`_evict_to_cap` 是软上限：保护集很多时最多超出 `2×floor` 条（200/3 的配置下无害） | ①②建议在 L2/L3 之前定：是否给淘汰键改成"`trade_date` 优先 + 访问度做次级"，是否加文件锁/单写者约定 |
| 16 | 🔴 **未修（数据层，新发现）** | **`get_fundamentals` 有回溯泄漏且无法用免费数据修**：它走 yfinance 的 `ticker_obj.info`（或是 Alpha Vantage 同名接口），两者**都忽略 `curr_date`**，返回的是**"现在"的快照**——PE/PEG/PB/市值/52 周高低/50·200 日均线/Beta 全是运行当天的值。yfinance 还会在表头写 `# Data retrieved on: <今天>`。对 2026-03-04 的决策，这等于把 9 月的估值喂进去 | ①`get_balance_sheet` / `get_cashflow` / `get_income_statement` **已经做了**日期过滤（`filter_financials_by_date`）✓，`get_news`/`get_global_news`/`get_indicators`/`get_stock_data` 也都有界 ✓；②**但 `.info` 没有历史版本 → 免费数据源下过滤不出来**，只能：**(a) 声明为限制**，或 **(b) 把 `get_fundamentals` 从基本面分析师的工具表里摘掉**（保留三个已过滤的报表工具），或 (c) 买 point-in-time 基本面数据；③**性质**：这是**两臂共享**的混淆项，不会像记忆泄漏那样只偏向 `on` 臂，因此**不推翻 A/B**，但会影响"绝对命中率"的外部效度 |
| 17 | ⚠️ 未修（数据层，同类） | `get_insider_transactions` 在 `news_data_tools.py` 里**根本没传日期**（`route_to_vendor("get_insider_transactions", ticker)`），是否返回未来交易未验证 | 与 #16 同性质（共享混淆）；若要收紧，需先看 payload 是否带日期可过滤 |
| 18 | ⚠️ **无法判定** | `full_states_log` **不记录工具调用与原始工具输出**（只有各 agent 的报告与辩论历史），所以**无法从存档判断** `get_fundamentals` 是否真的被调用过；报告正文里没有 yfinance 的 `Data retrieved on` 表头（0 次出现），既不能证明调用过、也不能证明没调用 | 若要可审计，需在 full_states_log 里记录工具调用名（小改动，但对论文的"可复现/可审计"章节很有用） |
| 19 | 🔴 **未修（方法论，P1 前须决策）** | **两臂上下文量不对等，与 C2 的"同等预算"口径冲突**。`_build_past_context` = rules + **L1（仅 on 臂）** + legacy（两臂都有），所以 `on` 臂是**加法**而非**等预算替换**。实测：`off` 8.3k/13.3k/8.6k 字符 → `on` 15.4k/20.4k/15.1k 字符，**平均多 69%（最高 +86%）**；且 **6 条 L1 事件全部与 legacy 决策日志的 (ticker, date) 重复**。**后果**：`on` 臂若胜出，无法区分是"记忆选得更好"还是"上下文更多"，而 C2 正是论文主攻 claim。而 `evaluation_protocol_v2.md` §0.2 C2 明写的是"固定注入 token 预算下，L1+L3 **vs** 基线平权注入"——即**替换** | 三个选项：**(A)** 保持加法，但必须报告 token 数并补一条"等预算安慰剂臂"（把 legacy 补齐到同 token 预算）才能排除体量效应；**(B)** 改为替换（`event_memory_enabled=True` 时**去掉 legacy 段**），直接支持 C2，代价是失去"legacy 两臂一致"的简洁性；**(C)** 先跑 P1 当链路冒烟测试，再定臂设计。**我建议 (B)**（贴合协议原文），但需要你拍 |
| 20 | ✅ 已验证 | **P1 前注入实测**：对三个测试场景逐一跑 `show_injection.py --ticker <T> --date <D>`，检查实际注入内容 —— **所有注入日期都严格早于场景日**（三例最早场景 07-03，注入内容最大日期均为 06-29），无未来信息；L1 段与 legacy 段均非空 | 这是"时间切片真的生效"的端到端证据（比单测更强：它检查的是组装后的真实 prompt 内容） |
| 21 | ⚠️ **未修（潜在数据完整性 bug，有实证）** | **结算可能在持有窗未走完时触发，把短窗收益当成 5 日结果记录**。`_resolve_pending_entries` 只要求"价格数据可用"，**没有任何最短时间检查**；`_fetch_returns` 会把 `actual_days` 夹到 `min(holding_days, len-1)`。实证：legacy 日志里有 `[2026-02-11 \| 600519.SS \| Hold \| -1.3% \| -0.1% \| **2d**]` —— 确实发生过 2 日结算。本例 alpha 仅 −0.1% 所以没写事件，但**若短窗内恰好大幅波动，就会写出一条"用 2 日涨跌算出的 5 日 alpha"的 EVENT** | 修法：结算时要求 `days == holding_days`，否则跳过（沿用现有的"数据不足，下轮再试"语义）。约 3 行。**不影响 P1**（冻结模式完全跳过结算），但**影响后续为 L2/L3 扩建语料**，建议在那之前修 |

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

### N1 · E1 建语料 ✅ **已完成**（6 条唯一 EVENT / 2 亏损 / 1 受保护大亏）

实际执行（2026-09-13）：`600519.SS@2026-01-26`（alpha +9.16%）与 `000001.SZ@2026-06-12`（alpha **−14.67%**）各跑一次分析 + `settle_now` 结算；并清掉 `601318.SS@2026-06-29` 的悬挂 pending（alpha +3.89% < 5%，**不写事件**，只标记 resolved）。

语料现状（`trading_lessons.md`，0 悬挂 pending）：

| 事件 | alpha | 保护 | 备注 |
|---|---|---|---|
| 600519.SS @ 2026-01-28 | +16.4% | ✅ | |
| 600519.SS @ 2026-01-26 | +9.2% | — | 本次新增 |
| 300750.SZ @ 2026-03-04 | +13.1% | ✅ | 论文的定性展示案例（复盘抓到算术/口径不一致） |
| 300750.SZ @ 2026-04-08 | +6.9% | — | |
| 000001.SZ @ 2026-06-12 | **−14.7%** | ✅ | 本次新增，**唯一受保护的大亏** → C3 claim 有实据 |
| 601318.SS @ 2026-06-15 | −7.3% | — | |

**关键收获**：`000001.SZ` 那条 |alpha| ≥ 10% 进了保护集，补上了"大亏也要记住 / 熊市记忆不能被长牛市挤掉"这个诉求的实证基础 —— 此前 4 条里没有任何受保护的亏损。

### N2 · **P1 门禁：先做两个决策**（各约 5 分钟，但会改变 P1 测什么）

1. **臂设计口径（§4 #19）** —— 加法 vs 等预算替换。**建议 (B) 替换**：贴合 `evaluation_protocol_v2.md` §0.2 C2 原文（"固定注入 token 预算下…"）；否则 `on` 臂多 69% 上下文，胜出也无法归因。
2. **是否修短窗结算（§4 #21）** —— 不影响 P1（冻结模式跳过结算），但会影响后续为 L2/L3 扩建语料；建议在扩建前修。

决策 1 若选 (B)，改动仅 `_build_past_context` 去掉 legacy 段（`event_memory_enabled=True` 时），约 4 行 + 单测。

### N3 · 跑 P1（决策后，~63 min / ~$0.52）

```powershell
& $PY scripts/run_experiment.py --scenarios exp_scenarios.json --reps 1 --progress
```
通过标准：6 run 全部 `status=ok`，CSV 里 `hit` 非空，两臂 `tokens_total` 有差异（这就是 L1 生效的直接证据）。

> **P1 期间不要跑 `settle_now` / `run_pool`** —— 每次 run 开始时各读一次记忆库，中途变动会让两臂读到不同记忆。

### N1b · 测试场景重挑 ✅ **已完成**

时间切片生效后旧场景作废（`601318.SS@2026-06-15` 会 SKIP-LEAK；`600519.SS@2026-01-22` 无同标的记忆）。已换成**同标的、更晚日期**，让每个场景都有真实的同标的记忆，且全部严格晚于语料：

| 场景 | 扫描原始收益 | 同标的可用记忆 |
|---|---|---|
| `600519.SS @ 2026-07-13` | +9.62% | 01-26、01-28 |
| `300750.SZ @ 2026-07-03` | −8.22% | 03-04、04-08 |
| `601318.SS @ 2026-07-13` | +7.47% | 06-15 |

> ⚠️ 这三个是**按已实现涨跌筛出来的**，所以**只能用于 pilot 探路，不得作为论文数字** —— 见 `experiment_plan.md` §2.2.1（评估集必须按协议盲选）。

### N4 · 定 kill test 规模（**要跟老师确认的决策点**）

现在 `killtest_protocol.md` 写的是 **120 runs**。按实测 24 min/run = **48 h 墙钟**，加消融明显排不下。三条路：

| 方案 | 规模 | 墙钟（全配置） | 代价 |
|---|---|---|---|
| 降配档 | 120 runs | ~16 h | 辩论轨迹变短 → **复盘质量下降**（复盘正是卖点），必须声明 |
| 3–4 进程并发 | 120 runs | ~12–16 h | 需先确认冻结记忆生效 + 每进程独立记忆路径，否则互相覆盖 |
| 砍配对 | 60–72 runs | ~24–29 h | 统计功效下降 → 更可能只能报效应量+CI，拿不到显著性 |

**建议**：先用 pilot 的方差算出"要多少配对才有功效"，再倒推规模；把"降配档 + 并发"当默认，
全配置只用来跑最终论文数字。**这个问题适合直接问老师。**

### N5 · **v2 = D1 实现 L3 季度蒸馏**（P1 之后的第一件事）

- 蒸馏队列 → 自包含、可修订的准则库（`max_rule_entries=50`）→ preamble 注入；
- 闭环必须打通：**淘汰 → 蒸馏 → 降级**，否则"蒸馏补偿遗忘"这个核心耦合点无法论证；
- 验收：`get_rules_context()` 返回真实准则；构造数据证明被淘汰的事件其信息进入了准则库。

### N6 · **v3 = D2 实现 L2 情境检索**

> 顺序说明：按 `memory_three_layer_design.md` §10 里程碑，**v2 = L3、v3 = L2**。L3 的输入（淘汰队列）v1 已经产出，L2 的状态指纹是更大的设计，所以 L3 先做。

- 事件条目写入状态指纹 → 逐维标准化 → 余弦相似度 → 加权 softmax 检索 → 粗桶降级兜底；
- **必须解决"长牛市把熊市记忆挤出去"**：与 N1 的"至少 1 条亏损事件"、保护集、方向下限联动验证。

### N7 · E5/E6 消融（依赖 N1 攒够 ≥30 条已结算事件）

- P2：L3 开/关；P3：A 纯相似度 / B 相似度×recency^γ / C 同标的最近 n 条平权。

### N8 · E7 汇总写作 · N9 · Demo 视频

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
| 2026-09-13 | 关闭 DeepSeek 思考模式（`TRADINGAGENTS_DEEPSEEK_THINKING=false`） | 思考 token 按输出计费且是本管线输出开销的大头。**但 400 的真因是能力表漏登记 `deepseek-flash`，两者都修了**（见 §4 #6） |
| 2026-09-13 | **复盘单独开思考**（`TRADINGAGENTS_POSTMORTEM_THINKING=true`，配置键 `postmortem_thinking`，默认 `None`=继承） | 复盘每次读完整辩论痕迹、一个语料只跑几次，是最值得花推理 token 的地方；分析师/辩论调用多而密，关掉省 token。复盘**不会**因此报 400（能力表已修）。注意复盘走 **quick 档**，所以不能靠"按档位分"，必须给它独立客户端 |
| 2026-09-13 | **记忆必须时间切片**：`as_of` 之前（严格早于决策日）的材料才可注入，两臂同样过滤 | 不切片就是**用未来信息做回测**，"记忆开"臂靠事后诸葛取胜，审稿人一眼击穿；这也正是 `evaluation_protocol_v2.md` 早就要求的"时间一致切分" |
| 2026-09-13 | `entry_id` 作幂等键，重复决策只留首条 | 同一 `(ticker, date)` 重跑会写第二条评级相反的 EVENT，记忆自相矛盾；首条通常是原始复盘（本项目的展示案例） |
| 2026-09-13 | **回退多信号准入**（`pnl`/`regime`/`risk`），L1 保持"只按 \|alpha\| 阈值准入" | 决定**不再对记忆侧做功能改动**。功能本身默认关闭、不影响行为，但被一并撤掉以减少代码面。`scripts/audit_triggers.py` 同步删除。**备份在 `.rollback_backup/`**（patch + 三个新文件），需要时可恢复。**保留**：时间切片、`as_of` 必填、`_readonly` 守卫、破坏性写入修复、思考模式修复——那些是安全修复不是功能 |
| 2026-09-13 | 取价**缓存优先**，裸 yfinance 仅兜底 | 不是记忆语义改动（阈值/准入/遗忘/注入/条目格式全不动，只换价格来源）。理由：①限流会让**评分和结算同时静默失效**；②历史窗口零网络调用 → 回测可离线复现。**评估集重选后必须重跑**，因为缓存里的数值不会随后续复权调整 |

---

## 7. 待问老师的问题

1. kill test 的规模与统计功效：120 runs 排不下时，优先"降配 + 并发"还是"砍配对"？两者都要在论文里声明，哪个更可接受？
2. 降配档（缩短辩论轮数）会削弱复盘输入，是否可以作为主实验配置？
3. v1 用 **alpha 符号**做市场状态代理，是否可接受？（L2 的完整状态向量在 v3，需要声明这个降级）
4. 是否需要引入 A 股新闻/情绪数据源（当前 A 股情绪源为空，是显著的数据可得性缺口）？
5. 比赛 Demo 与论文可否共用同一批实验数据（避免两套数字不一致）？
6. 复盘档位：现在是 **quick 档 + 思考开启**。要不要升到 **deep 档**（`postmortem_llm=deep`）？升档更贵但复盘质量可能更好，而"复盘抓到原管线缺陷"正是论文的定性亮点。
7. 淘汰优先级现在是"访问制"（`last_accessed`/`hits` 优先于 `trade_date`，见 §4 #15②）：这与"大亏/熊市记忆要留"的目标是一致还是相冲？要不要改成 `trade_date` 优先、访问度做次级？

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
