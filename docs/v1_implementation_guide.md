# v1 实施指南:记忆主路径改动点地图

> **用途**:W1 精读成果固化 + v1 实现时的逐处参照。关联:`docs/memory_three_layer_design.md`(设计)、`docs/competition_implementation_roadmap.md`(进度)。
>
> **进度**:步骤 1(配置项)、2(lessons 存储骨架)、3(复盘 agent)、4(结算挂钩)、5(注入改造)已实现并配单测;步骤 6–7 待做。本文件是"改哪里、怎么改"的唯一地图。

---

## 1. 记忆主路径全景(现状)

```
propagate(ticker, date)                            # trading_graph.py L362
 ├─ _resolve_pending_entries(ticker)               # L296 【结算入口】
 │     └─ 对每条同 ticker 的 pending:
 │          _fetch_returns()  → raw, alpha, days   # L251
 │          Reflector.reflect_on_final_decision()  # 只输入"决策文本+数字"
 │          batch_update_with_outcomes()           # memory.py L164
 │
 └─ _run_graph(ticker, date)                       # L419 【分析+注入入口】
       ├─ _build_past_context(ticker) → past_context  # L419 【取用:三段合并】
       ├─ init_agent_state(past_context=…)         # propagation.py
       ├─ graph.invoke(...)
       ├─ _log_state() → full_states_log_<date>.json  # L484 【全量辩论落盘】
       └─ store_decision()                         # memory.py L30 【写 pending】
```

`past_context` 唯一消费者 = `portfolio_manager.py` 的 `lessons_line`(标签现为 "Team memory")。

---

## 2. 逐文件 v1 改动点

### `tradingagents/agents/utils/memory.py`

| 位置 | 现状 | v1 改动 | 状态 |
|---|---|---|---|
| `__init__` | 只读 `memory_log_path`/`memory_log_max_entries` | 增加 `lessons_path`、`max_event_entries`、`event_protect_threshold` | ✅ 已做 |
| `store_decision` | 写 pending 决策 | 不动(审计日志保留) | — |
| `load_entries`/`_parse_entry` | 解析决策条目 | 不动;lessons 用独立解析 | — |
| `get_past_context` | 只读决策日志、无访问记录 | 保留作兜底;新增 lessons 取用 + 访问追踪回写 | ✅ 步骤 5 |
| `batch_update_with_outcomes` | 结算改 tag+追加 REFLECTION | 不动;事件写入在它之后(见 trading_graph) | — |
| `_apply_rotation` | 决策日志丢最旧 resolved | 保留;lessons 另写 `maintain_memory` | ⏳ 步骤 6 |
| 新增 `store_event` / `load_lessons` / `_parse_lessons_entry` | 无 | L1 事件条目写入与解析(EVENT 前缀) | ✅ 已做 |
| 新增 `_render_event_entry` / `_rewrite_lessons` / `_format_event` / `get_lessons_context` / `get_rules_context` | 无 | 事件序列化/原子重写/注入取用(recency top-k + 访问追踪) | ✅ 步骤 5 |

### `tradingagents/graph/trading_graph.py`

| 位置 | v1 改动 | 状态 |
|---|---|---|
| `__init__` | 按 `postmortem_llm` 选 deep/quick 客户端,构造复盘 agent | ✅ 步骤 3 |
| `_resolve_pending_entries` | 结算拿到 alpha 后判阈值 → 读 `full_states_log_<date>.json` → 复盘 agent → `store_event`;循环后 `maintain_memory` | ✅ 步骤 4(maintain_memory 待步骤 6) |
| `_run_graph` | `past_context` 改为 `_build_past_context`(准则+事件+兜底三段合并) | ✅ 步骤 5 |
| `_fetch_returns` / `_log_state` | 不动(分别产出结算数字与复盘输入) | — |

### 新增/配套

| 文件 | 内容 | 状态 |
|---|---|---|
| `tradingagents/agents/utils/postmortem_agent.py` | 复盘 agent 工厂(读 full JSON + 结算数字 → `EventPostmortem`) | ✅ 步骤 3 |
| `tradingagents/agents/schemas.py` | 新增 `EventPostmortem` + `render_event_postmortem` | ✅ 步骤 3 |
| `tradingagents/default_config.py` | 新增配置键 + env 覆盖 | ✅ 已做 |
| `portfolio_manager.py` | 标签改为"Team memory"(注入内容在 `_run_graph` 拼装) | ✅ 步骤 5 |
| `tests/test_lessons_store.py` | lessons 解析/事件写入单测 | ✅ 已做 |
| `tests/test_postmortem_agent.py` | 复盘 schema/agent/结算挂钩单测 | ✅ 已做 |
| `tests/test_lessons_context.py` | 注入取用单测(往返/排序/hits/三段合并) | ✅ 步骤 5 |

---

## 3. 配置键命名(已定为平键,与现有 `DEFAULT_CONFIG` 扁平风格一致)

```
lessons_path              # ~/.tradingagents/memory/trading_lessons.md (os.getenv 直取)
event_alpha_threshold     # 0.05  (float)
event_protect_threshold   # 0.10  (float)
max_event_entries         # 200   (int)
max_rule_entries          # 50    (int, v2)
distill_interval_days     # 90    (int, v2)
postmortem_llm            # "deep" (str)
```
env 覆盖(带类型强转):`TRADINGAGENTS_EVENT_ALPHA_THRESHOLD`、`TRADINGAGENTS_EVENT_PROTECT_THRESHOLD`、`TRADINGAGENTS_MAX_EVENT_ENTRIES`、`TRADINGAGENTS_MAX_RULE_ENTRIES`、`TRADINGAGENTS_DISTILL_INTERVAL_DAYS`、`TRADINGAGENTS_POSTMORTEM_LLM`。

---

## 4. 实现顺序(每步可测、不破坏现有)

1. ✅ 配置项(default_config + env)
2. ✅ lessons 存储骨架(`store_event`/`load_lessons`/EVENT 解析 + 单测)
3. ✅ 复盘 schema + agent(`EventPostmortem` + 工厂)
4. ✅ 结算挂钩(`_resolve_pending_entries` 阈值→复盘→写事件)
5. ✅ 注入改造(`_run_graph` 三段合并 + lessons 取用 + 访问追踪)
6. ✅ 维护(上限/保护集/方向下限/蒸馏队列 + 完全重复去重)
7. ⏳ 集成:`run_pool.py` 跑真实 quick 档验证 + `cost_log.csv`

---

## 5. 进度清单

- [x] 环境:官方项目本地跑通一次
- [x] 演示标的池:`demo_pool.json`(scan_demo_pool.py 生成并人工定稿)
- [x] 成本记账:`cost_accounting.py` + `run_pool.py`(离线验证通过)
- [x] 精读主路径并固化改动点(本文件)
- [x] 步骤 1–2(配置项 + lessons 存储骨架 + 单测)
- [x] 步骤 3–4(复盘 agent + 结算挂钩 + 单测)
- [x] 步骤 5(注入改造 + 单测)
- [x] 步骤 6(维护:蒸馏队列 + 保护对称 + 方向下限 + 完全重复去重)
- [x] **git 功能分支**:`feature/memory-v1`
- [x] 新增单测 + 既有测试全绿

> **W1 已收尾,W2 进行中**:步骤 1–6 已完成;剩余步骤 7(集成)在 W2–W3 推进。

---

## 6. W2–W3:步骤 5–7 详细改动点

> 周表 / gate / 风险见 `docs/competition_implementation_roadmap.md` §7;本节是**逐处实现地图**(与 §2 同格式)。

### 6.1 步骤 5 —— 注入改造(记忆取用,让复盘结果喂回 PM)✅ 已实现

**`tradingagents/agents/utils/memory.py`**

| 位置 | 改动 |
|---|---|
| 已有 `_parse_lessons_entry` | 新增反向 `_render_event_entry(e)`:把解析出的 dict 序列化回 markdown 块(tag + SUMMARY + CONTEXT_POINTER + FINGERPRINT? + LAST_ACCESSED/HITS/PROTECTED) |
| 新增 `_rewrite_lessons(entries)` | 用 `_render_event_entry` 逐条渲染,`_SEPARATOR` 连接,tmp + `os.replace` 原子落盘(访问追踪与维护共用) |
| 新增 `_format_event(e)` | 注入用紧凑格式:`[{trade_date} \| {ticker} \| {rating} \| {alpha%}]\n{summary}` |
| 新增 `get_lessons_context(ticker, n_same=5, n_cross=3)` | 读 lessons → 同 ticker 最近 n_same 条 + 跨票最近 n_cross 条(文件序 reverse 取新)→ 命中条目 `hits+1`、`last_accessed=今天` → `_rewrite_lessons` → 返回紧凑文本 |
| 新增 `get_rules_context()` | v1 占位返回 `""`(v2 解析 RULE 条目作 preamble) |

**`tradingagents/graph/trading_graph.py`**

| 位置 | 改动 |
|---|---|
| `_run_graph` | `past_context = get_past_context(...)` 改为 `self._build_past_context(company_name)` |
| 新增 `_build_past_context(ticker)` | `rules + events + legacy` 三段合并(空段剔除),`"\n\n"` 连接;`legacy = get_past_context(...)` 作兜底 |

**`tradingagents/agents/managers/portfolio_manager.py`**

| 位置 | 改动 |
|---|---|
| `lessons_line` 标签 | 由 "Lessons from prior decisions..." 改为 "Team memory:"(注入内容本身在 `_run_graph` 拼装) |

**设计决策**:事件段默认 N=5(与现 `n_same=5` 同量级,预算可控);注入正文不含全量过程(摘要已在 `store_event` 精简);访问追踪 = 每次 run 一次小体积原子重写(≤ 几百条,成本可忽略)。

**测试点**:`_render_event_entry` 与 `_parse_lessons_entry` 往返一致;`get_lessons_context` 排序(最新在前)/ n_same·n_cross 截断 / hits 递增;`_build_past_context` 三段拼接(准则空 → 事件+旧反思;事件空 → 只旧反思)。

### 6.2 步骤 6 —— 维护(简化遗忘,考虑 L3)✅ 已实现

**`tradingagents/agents/utils/memory.py`**

| 位置 | 改动 |
|---|---|
| 新增 `_dedupe_events(entries)` | 只去**完全重复**(同 ticker+date+rating);语义去重(相似情境)推迟 v3(需状态指纹) |
| 新增 `_evict_to_cap(entries)` | 上限淘汰:非保护条目按 `_recency_key`(最近使用/最近日期/最多命中)保留到 ≤ `max_event_entries`,**保护集 + 方向下限永远豁免**(软上限) |
| 新增 `_append_distill_queue(entries)` | 被淘汰/去重的条目 append 进蒸馏队列文件,供 v2 的 L3 蒸馏消化,**不硬删** |
| 新增 `maintain_memory()` | 去重 → 淘汰 → 入队列 → 原子重写;未配置 lessons 时 no-op |

**`tradingagents/graph/trading_graph.py`**

| 位置 | 改动 |
|---|---|
| `_resolve_pending_entries` 尾部 | `if updates:` 批处理后调用 `self.memory_log.maintain_memory()` |

**最终设计决策(本轮敲定)**:
- **去重**:语义去重会链式过度合并(把 1 亏 5 赚并成 1 条),与"大亏也要记住/熊市记忆不丢"冲突 → v1 只做完全重复去重,语义去重推迟 v3;
- **方向下限** `min_events_per_direction`:淘汰与检索都保证 win/loss 各保留 N 条,长牛市不淹没熊市记忆;
- **软上限**:`max_event_entries` 只约束普通条目,保护集 + 方向下限永远额外保留;
- **蒸馏队列**:淘汰即入队(L3 闭环前"只增不减"是临时状态,v2 蒸馏后回收)。

**测试点**(`tests/test_maintenance.py`):保护集(含大亏)不被淘汰;方向下限保留熊市记忆;淘汰入蒸馏队列;完全重复去重;6 条不同日期不误并;检索在盈利主导时仍提取亏损;no-op。

### 6.3 步骤 7 —— 集成(你本机执行,需 LLM key)

- **命令**(A 股池;`disabled_sources` 已在 `.env` 持久化;`--analysts market,social,fundamentals` 跳过新闻分析师,规避地缘政治内容触发 GLM 审核):
  ```powershell
  python scripts/run_pool.py --pool demo_pool_ashare.json --categories l1_extreme_win --only 300750.SZ --analysts market,social,fundamentals --settle --settle-days 14 --log cost_log.csv
  python scripts/verify_l1.py --ticker 300750.SZ --trade-date 2026-03-04 --expect-settle
  ```
- **验收**:① `trading_lessons.md` 出现 `[EVENT | ...]` 条目(摘要 + 指针 + 保护标记)② 决策日志该条变 resolved ③ `cost_log.csv` 两条记录 ④ 注入含事件段(`verify_l1.py` 4/4 PASS);
- **产出**:Demo 1 三分钟剧本(现状缺陷 → 我们架构 → 现场跑 → 展示 lessons 落库与注入)。

---

## 7. 技术难点与权威参考(带给老师)

| 难点 | 结论 | 权威参考 |
|---|---|---|
| ① 牛/熊 regime 识别 | 市场 regime 是**潜在的市场级状态**,应由市场数据(实现协方差/波动率/签名)检测,而非单次决策结果符号;v1 的 alpha 符号是过渡占位,真正 regime 并入 L2/v3 状态指纹 | Bucci & Ciciretti, [Market Regime Detection via Realized Covariances](https://arxiv.org/abs/2104.03667)(VLSTAR + 层次聚类);[签名法 regime 分类](https://ar5iv.labs.arxiv.org/html/2107.00066);[在线非参 regime 聚类](https://ar5iv.labs.arxiv.org/html/2306.15835) |
| ③ 蒸馏闭环(存储只增不减) | L3 蒸馏 = 记忆"巩固(consolidation)",是 LLM-agent 记忆的标准操作;写完 L3 即闭环回收 | Zhang et al., [A Survey on the Memory Mechanism of LLM-based Agents](https://arxiv.org/abs/2404.13501)(写入/读取/反思-巩固分类法);Generative Agents(反思式巩固范式) |
| ④ 软上限优先级 | "cap 只约束普通条目、高重要性/多样性豁免" = 综述里的**重要性保留遗忘**,文献一致做法 | 同上 Zhang et al. 综述(遗忘/重要性保留策略) |

---

## 8. 环境与 provider 适配(网络受限下的工程适配)

> 本机网络限制导致 Yahoo 宽请求返回空、Reddit/StockTwits/Polymarket/FRED 不可达,且 GLM 触发内容审核。以下改动让框架在这些约束下稳定运行;论文中对应"数据可得性与工程适配"一节。

| 改动 | 位置 | 作用 |
|---|---|---|
| 分块取数 + history 优先 | `dataflows/stockstats_utils.py` | 5 年 OHLCV 按 `TRADINGAGENTS_OHLCV_CHUNK_DAYS`(默认 365 天)分块请求再合并;优先 `Ticker.history`、回退 `yf.download`。单次宽请求在受限网络下会返回空("possibly delisted") |
| 审核感知重试 | `llm_clients/openai_client.py` | 捕获 GLM `contentFilter` / 错误码 1301 → 重试 `TRADINGAGENTS_MODERATION_RETRIES`(默认 2)次;非审核错误不重试 |
| 声明式关闭外部源 | `default_config.py`、`agent_utils.py`、`sentiment_analyst.py`、`news_analyst.py`、`trading_graph.py` | `disabled_sources`(env `TRADINGAGENTS_DISABLED_SOURCES`)按 `social` / `macro` / `prediction_markets` 关闭;工具列表 + prompt 文案 + ToolNode 三处同步,避免模型幻觉调用已移除的工具 |
| 运行可观测性 | `scripts/run_pool.py`、`scripts/cost_accounting.py` | `--analysts` 裁剪分析师(如跳过 news,规避内容审核);`--debug` 流式打印节点进度;`--heartbeat N` 每 N 秒打印"仍在运行"(默认 60s),避免长 run 看起来像卡死 |
| 诊断 / 验收脚本 | `scripts/diagnose_yfinance.py`、`scripts/verify_l1.py` | 网络路径诊断 / L1 落盘确定性验收 |

**原则**:个人 provider / 模型写 `.env`,**不改源码默认值**(源码默认值保持 provider 中立——否则测试会红、可复现性受损)。

**诊断实测(本机网络,`scripts/diagnose_yfinance.py`)**:`Ticker.history`(显式 start/end)对 600519.SS / 300750.SZ / AAPL 的 **1y 与 5y 全部成功**(243 / 1211 / 1254 行);`yf.download` 对其中 2 个标的一律返回空并打印 "possibly delisted";`period="1mo"` 形式时好时坏(框架不使用该形式)。结论:**优先 `Ticker.history` + 显式 start/end 是正确主路径**,`yf.download` 仅作兜底。

---

## 9. 里程碑与打磨清单

### 9.1 里程碑:步骤 7 完成(L1 端到端跑通,实测)

| 环节 | 证据(实测) |
|---|---|
| 写入 | `settle_now.py --ticker 300750.SZ` → `status ok`,`EVENT before=0 after=1`,产出 `[EVENT \| E-2026-03-04-300750.SZ \| 2026-03-04 \| 300750.SZ \| Buy \| +13.1% \| +17.1%]`,`PROTECTED: true`(alpha 13.1% ≥ 10%) |
| 结算联动 | 决策日志该条由 `\| pending]` 变为 `[2026-03-04 \| 300750.SZ \| Buy \| +17.1% \| +13.1% \| 5d]` |
| 取用/注入 | `show_injection.py` 打印的三段 `past_context`:准则(空)+ L1 事件摘要 + 决策日志兜底(含 DECISION/REFLECTION) |
| 验收 | `verify_l1.py --expect-settle` → **4/4 checks passed** |
| 成本 | 完整分析 12 调用 / 152,589 tok / 904s / $0.086;结算+复盘 2 调用 / 27,227 tok / 258s / $0.019 → **一个"决策→结算→落库"周期 ≈ $0.105 / ~19 分钟** |
| 复盘价值(实证) | 复盘摘要抓到了原管线未发现的缺陷:① "证据的缺席被当成利好证据"(情绪报告已警告过);② 基本面报告"常态化净利 266.7 亿"的口径/算术不一致,且全链条无人复核;③ 主动声明结果偏倚 |

### 9.2 打磨清单(v1.1)

- [ ] **事件摘要过长**:实测单条 SUMMARY ≈ 2,300 字,与设计目标"在保证内容的前提下尽量精简"冲突,直接抬高注入预算(削弱 P4"同等或更低 token 预算"的论据)。建议:① 在 `EventPostmortem` 各字段 description 里加长度约束(如 Summary ≤ 2 句、Key Basis/Missed Factors ≤ 4 条);② `store_event` 加硬上限(超出截断并标注);③ 注入时按事件截断。
- [ ] **legacy 反思语言不一致**:`Reflector` 的 prompt 未调用 `get_language_instruction()`,因此 `output_language=Chinese` 时,注入内容里 L1 事件是中文、决策日志 REFLECTION 是英文。建议在 `reflection.py` 补上语言指令。
- [ ] `hits` / `last_accessed` 回写已有实现,但尚无"遗忘是否真的按访问频次生效"的端到端验证(多跑几轮后观察 distill queue)。
- [ ] 保护集只增不减(L3 未实现前),文件体积会单调增长——v2 蒸馏落地后闭环。
