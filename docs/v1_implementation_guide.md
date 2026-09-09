# v1 实施指南:记忆主路径改动点地图

> **用途**:W1 精读成果固化 + v1 实现时的逐处参照。关联:`docs/memory_three_layer_design.md`(设计)、`docs/competition_implementation_roadmap.md`(进度)。
>
> **进度**:步骤 1(配置项)与步骤 2(lessons 存储骨架)已实现并配单测;步骤 3–7 待做。本文件是"改哪里、怎么改"的唯一地图。

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
       ├─ get_past_context(ticker) → past_context  # memory.py L70 【取用】
       ├─ init_agent_state(past_context=…)         # propagation.py
       ├─ graph.invoke(...)
       ├─ _log_state() → full_states_log_<date>.json  # L484 【全量辩论落盘】
       └─ store_decision()                         # memory.py L30 【写 pending】
```

`past_context` 唯一消费者 = `portfolio_manager.py` 的 `lessons_line`(L36–41)。

---

## 2. 逐文件 v1 改动点

### `tradingagents/agents/utils/memory.py`

| 位置 | 现状 | v1 改动 | 状态 |
|---|---|---|---|
| `__init__` | 只读 `memory_log_path`/`memory_log_max_entries` | 增加 `lessons_path`、`max_event_entries`、`event_protect_threshold` | ✅ 已做 |
| `store_decision` | 写 pending 决策 | 不动(审计日志保留) | — |
| `load_entries`/`_parse_entry` | 解析决策条目 | 不动;lessons 用独立解析 | — |
| `get_past_context` | 只读决策日志、无访问记录 | 改/新增:准则 + 事件 + 决策日志兜底;访问追踪回写 | ⏳ 步骤 5 |
| `batch_update_with_outcomes` | 结算改 tag+追加 REFLECTION | 不动;事件写入在它之后(见 trading_graph) | — |
| `_apply_rotation` | 决策日志丢最旧 resolved | 保留;lessons 另写 `maintain_memory` | ⏳ 步骤 6 |
| 新增 `store_event` / `load_lessons` / `_parse_lessons_entry` | 无 | L1 事件条目写入与解析(EVENT 前缀) | ✅ 已做 |

### `tradingagents/graph/trading_graph.py`

| 位置 | v1 改动 | 状态 |
|---|---|---|
| `__init__` | 按 `postmortem_llm` 选 deep/quick 客户端,构造复盘 agent | ⏳ 步骤 3 |
| `_resolve_pending_entries` | 结算拿到 alpha 后判阈值 → 读 `full_states_log_<date>.json` → 复盘 agent → `store_event`;循环后 `maintain_memory` | ⏳ 步骤 4 |
| `_run_graph` | `past_context` 改为"准则+事件+兜底"三段合并 | ⏳ 步骤 5 |
| `_fetch_returns` / `_log_state` | 不动(分别产出结算数字与复盘输入) | — |

### 新增/配套

| 文件 | 内容 | 状态 |
|---|---|---|
| `tradingagents/agents/utils/postmortem_agent.py` | 复盘 agent 工厂(读 full JSON + 结算数字 → `EventPostmortem`) | ⏳ 步骤 3 |
| `tradingagents/agents/schemas.py` | 新增 `EventPostmortem` + `render_event_postmortem` | ⏳ 步骤 3 |
| `tradingagents/default_config.py` | 新增配置键 + env 覆盖 | ✅ 已做 |
| `portfolio_manager.py` | 可选:标签改为"团队记忆"(注入内容在 `_run_graph` 拼装) | ⏳ 步骤 5 |
| `tests/test_lessons_store.py` | lessons 解析/事件写入单测 | ✅ 已做 |

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
3. ⏳ 复盘 schema + agent(`EventPostmortem` + 工厂)
4. ⏳ 结算挂钩(`_resolve_pending_entries` 阈值→复盘→写事件)
5. ⏳ 注入改造(`_run_graph` 三段合并)
6. ⏳ 维护(上限/保护集/冗余剔除 + 访问追踪)
7. ⏳ 集成:`run_pool.py` 跑真实 quick 档验证 + `cost_log.csv`

---

## 5. W1 完成条件

- [x] 环境:官方项目本地跑通一次
- [x] 演示标的池:`demo_pool.json`(scan_demo_pool.py 生成并人工定稿)
- [x] 成本记账:`cost_accounting.py` + `run_pool.py`(离线验证通过)
- [x] 精读主路径并固化改动点(本文件)
- [x] 步骤 1–2(配置项 + lessons 存储骨架 + 单测)
- [ ] **git 功能分支**:`git checkout -b feature/memory-v1`(动码前/提交前建,保持 main 干净)
- [ ] 新单测 + 既有 `test_memory_log.py` 全绿(回归确认)

> W1 收尾的唯一剩余动作是**建功能分支 + 跑测试确认全绿**;完成后进入 W2(步骤 3–4:复盘 agent 与结算挂钩)。
