# 三层记忆链路设计草案

> **状态**:评审中(尚未实现)· 关联代码:`tradingagents/agents/utils/memory.py`、`tradingagents/graph/trading_graph.py`、`tradingagents/graph/reflection.py`、`tradingagents/agents/managers/portfolio_manager.py`、`tradingagents/default_config.py`
>
> **一句话目标**:把现在"每条决策平权、2–4 句反思、无遗忘、只注入 PM"的单一日志,改造成 **L1 极端事件精简记忆(情节)→ L2 状态匹配检索(策略)→ L3 季度准则蒸馏(语义)** 的三层闭环,在不膨胀上下文的前提下让决策团"记得住教训、用得上经验"。

---

## 1. 背景与动机

### 1.1 现状(基于 v0.3.1 代码逐行核对)

| 现状机制 | 代码位置 | 局限 |
|---|---|---|
| 每次 run 落一条 `[date \| ticker \| rating \| pending]` 决策条目 | `memory.py: store_decision` | 写入时无反思,只有决策正文 |
| 反思**延迟到下次跑同 ticker 时结算**(取已实现收益 + 相对基准 alpha 后写 2–4 句) | `trading_graph.py: _resolve_pending_entries` → `Reflector.reflect_on_final_decision` | 反思**输入只有最终决策文本 + 数字**,完全看不到当时的分析师报告与辩论内容 —— 事后无法回答"当时为什么这么决定" |
| 取用:解析全部条目后按 n_same=5 / n_cross=3 硬截断 | `memory.py: get_past_context` | 平权注入,与当前市场状态无关;无访问记录 |
| 注入点只有一个:Portfolio Manager 的 `lessons_line` | `portfolio_manager.py` | 其他 agent 读不到历史 |
| 容量控制:`_apply_rotation` 只数 resolved 条目、超 `memory_log_max_entries` 丢最旧 | `memory.py: _apply_rotation` | 纯"丢最旧",极端事件(一年前的大亏)最先被丢;pending 永不丢 |

### 1.2 记忆膨胀的两条成本曲线(本设计要同时解决)

1. **解析成本**:每次 run 的 `get_past_context()` 都 `load_entries()` 全文读取 + 正则切块(`memory.py`),日志越大每次 run 越慢;
2. **注入成本**:`past_context` 拼成一段字符串灌进 PM prompt,条目越多 token 费越高;
3. **过拟合风险**:平权注入 + 无检索,决策团会在稀疏样本上把噪声当规律。

### 1.3 一个关键事实:完整过程本来就在磁盘上

每次 run 都会把**分析师报告、多空辩论全文、风险辩论全文、trader 计划、最终决策**写入
`results/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json`
(`trading_graph.py: _log_state`)。因此**记忆条目不需要、也不应该携带完整过程**——它只需做"索引 + 摘要 + 指针",需要时按指针读 JSON。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  读取路径(每次 run 的 past_context 注入,按优先级拼装)                 │
│  ┌───────────────────┐  ┌──────────────────────────────┐          │
│  │ L3 团队准则(preamble)│  │ L1 事件记忆(检索策略选出 top-k)│          │
│  └───────────────────┘  └──────────────────────────────┘          │
│         ▲  固定全量                 ▲  L2 检索策略(指纹余弦+两段式排序)  │
└─────────┼───────────────────────────┼─────────────────────────────┘
          │ 增量蒸馏(季度/淘汰队列触发)  │ 写入(仅结算时 |alpha| ≥ 阈值)
┌─────────┴───────────────────────────┴─────────────────────────────┐
│  写入路径(Phase B 结算批次,挂 _resolve_pending_entries 之后)          │
│  ┌──────────────┐      ┌──────────────────┐      ┌───────────────┐ │
│  │ L1 事件记忆     │      │ 遗忘机制           │      │ L3 准则库       │ │
│  │ 结构化+精简+指纹 │ ───► │ 上限+淘汰分+保护集    │ ───► │ 淘汰队列 → 蒸馏  │ │
│  └──────────────┘      └──────────────────┘      └───────────────┘ │
│  全文证据:results/…/full_states_log_<date>.json(指针引用,永不进 prompt)│
└───────────────────────────────────────────────────────────────────┘
```

### 2.1 分层职责

| 层 | 性质 | 存什么 | 数量控制 | 成本控制 |
|---|---|---|---|---|
| **L1 事件记忆** | 物理存储(情节) | 结算时 |alpha| ≥ 阈值的**结构化精简条目**(摘要+元数据+JSON 指针+当时市场指纹) | 上限 + 遗忘 + 保护集 | 写入门槛过滤 + 双层存储(正文不进 prompt) |
| **L2 检索策略** | **非存储**,只是读取路径上的筛选排序 | 不存数据 | 指纹标准化余弦匹配 + 两段式排序(过滤 → 排序 → 标注) | 只读不写、确定性、无额外 LLM 调用 |
| **L3 准则库** | 物理存储(语义) | 蒸馏出的团队准则(自带证据、可修订) | 少量(如 ≤ 50 条) | 增量蒸馏(只处理淘汰批次,不重读历史) |

### 2.2 与现状的兼容策略

- **决策日志(`trading_memory.md`)语义不变**:每条决策仍记录(审计用途),反思回填逻辑保留;
- **新增独立记忆文件 `trading_lessons.md`**(L1 事件 + L3 准则),`past_context` 的注入来源逐步迁移到新文件;迁移期两种来源并存(见 §7);
- 旧条目解析、`parse_rating`、alpha 计算、tag 格式全部向后兼容。

---

## 3. L1 — 事件化精简记忆

### 3.1 采样:什么时候值得记(写入门槛)

- **触发点 = 结算时(Phase B)**,不是决策时 —— 决策当天无法知道结果;
- **判据基于 alpha(相对区域基准,代码已自动映射 SPY/^N225/^HSI…),不用 raw return**,剥离大盘 beta,避免牛熊市系统性淹没;
- 仅当 `|alpha| ≥ event_alpha_threshold` 才调复盘 agent 写 L1 事件条目 —— 保证条目是"稀有高价值事件",同时把额外 LLM 调用次数压到极低。

### 3.2 双层存储:条目 = 索引 + 摘要 + 指针

L1 条目**绝不复制完整过程**,格式草案:

```markdown
[EVENT | {entry_id} | {trade_date} | {ticker} | {rating} | {alpha:+.1%} | {raw:+.1%}]

SUMMARY:
复盘 agent 的 3–5 行结构化摘要(决策是什么、当时依据、漏看了什么、可迁移教训)

CONTEXT_POINTER: results/AAPL/TradingAgentsStrategy_logs/full_states_log_2026-01-15.json
FINGERPRINT: [realized_vol_20d, trend, rsi14, volume_ratio, atr_pct]   # 原始值;标准化在检索时做(§5.3)
LAST_ACCESSED: 2026-07-05  |  HITS: 3  |  PROTECTED: true
```

- `SUMMARY` 由复盘 agent 一次调用产出(见 §3.4),每个字段长度受限,紧凑行而非散文;
- `CONTEXT_POINTER` 指向已存在的完整 JSON;审计或深挖时按指针读取;
- `FINGERPRINT` 存决策日**原始特征向量**(定义见 §5.2),**不进 prompt**,只供 L2 检索与 L3 蒸馏使用;
- tag 前缀 `EVENT` 区分于决策日志条目,`_parse_entry` 按前缀分流,旧条目解析不受影响。

### 3.3 条目元数据(为遗忘与 L2 预留)

| 字段 | 说明 | 写入时机 |
|---|---|---|
| `entry_id` | 稳定 id(如 `E-<n>`),供引用/蒸馏/修订 | 写入时 |
| `trade_date` / `ticker` / `rating` | 决策身份 | 写入时(继承自结算的 pending 条目) |
| `alpha` / `raw` | 结算结果 | 写入时 |
| `state_fingerprint` | 决策日**原始特征向量**(§5.2;标准化在检索时用记忆库统计量做,§5.3) | 决策时计算预存,事件写入时继承 |
| `state_bucket` | 由指纹派生的粗粒度桶(样本不足时检索降级用,§5.6) | 决策时派生,事件写入时继承 |
| `last_accessed` / `hits` | 遗忘机制输入 | 每次被检索注入时更新 |
| `protected` | |alpha| ≥ 保护阈值时置真 | 写入时 |
| `distilled` | 已被 L3 蒸馏消化(之后才可被常规淘汰) | 蒸馏时 |

### 3.4 复盘 agent(新增)

- **触发**:`_resolve_pending_entries` 结算出 `|alpha| ≥ event_alpha_threshold` 时;
- **输入**:读 `CONTEXT_POINTER` 指向的 full_states_log JSON(分析师报告、bull/bear 全文、risk 全文、trader 计划、最终决策)+ 结算数字(raw/alpha/持有天数);
- **输出**:结构化(仿 `schemas.py` 的 Pydantic 模式 + `with_structured_output`,失败回退自由文本,参照 `structured.py`)—— 字段:决策摘要、关键依据、未预见因素、可迁移教训、自评当时逻辑缺陷;
- **模型档位**:默认用 **deep LLM**(总结"为什么成功/失败"需要重推理),但可配置;由于只在极端事件触发,调用成本可接受。

### 3.5 遗忘机制(本设计的核心新增)

- **双重控制**:阈值负责定义"值得记的人群";上限+遗忘负责管理"长尾",两件事分开做;
- **容量上限**:新配置 `max_event_entries`(事件条目数),与现有 `memory_log_max_entries`(决策日志 resolved 数)并存;
- **淘汰分数(v2 起用,v1 可先退化简化)**
  `score = w_recency · recency_norm + w_freq · log1p(hits) + w_extremity · |alpha|_norm − redundancy_penalty`
  按分数升序淘汰,直到低于上限;
- **保护集**:`|alpha| ≥ event_protect_threshold` 的条目 `PROTECTED: true`,**不参与常规淘汰**;只有被 L3 蒸馏消化(打 `distilled`)后才降级为可淘汰 —— 保证一年前的大亏不会被"最旧优先"误杀;
- **冗余剔除(规则,不用模型)**:同 ticker + 同评级 + 同派生状态桶(§5.2 指纹粗分)+ 短时间窗内多条事件 → 保留 |alpha| 最大一条,其余直接进蒸馏队列;
- **维护时机**:Phase B 结算批次内与反思回填一起做(`_apply_rotation` 现有的 tmp + `os.replace` 原子写模式直接复用),不打断分析主流程;
- **访问追踪**:`get_past_context`(或未来的检索器)选中条目注入时,回写 `last_accessed`/`hits`(每次 run 一次小体积原子写,≤几百条目成本可忽略)。

---

## 4. L3 — 季度知识蒸馏(挂在淘汰队列上)

### 4.1 定位修正:蒸馏是"回收站升级",不是单纯加高优先级

被遗忘的细节不能白丢:淘汰前先进蒸馏队列,提炼成"规律级结论",信息从**逐条细节**升级为**团队准则**。

### 4.2 增量蒸馏,绝不重读历史

- **触发**:蒸馏队列非空 **且** 距上次蒸馏 ≥ `distill_interval_days`(默认 90 天);
- **输入**:上次产出的准则集 + 本批淘汰/蒸馏队列条目(**不是**全量日志重读) —— 否则蒸馏成本随日志总量上涨,与"解决膨胀"自相矛盾;
- **输出**:结构化准则列表(deep LLM),每条:
  `{rule_id, text, evidence(内联:哪只票/什么状态/什么决定/什么结果), superseded_by?}`;
- **准则要自我完备**:原始条目淘汰后引用即失效,所以**证据在写入时内联**,不留悬空指针;
- **可修订**:允许后续蒸馏 `superseded_by` 推翻前期准则,防止坏教训在最高优先级位置复利(如模型换版、数据源变更导致的系统性误判期)。

### 4.3 读取优先级

注入顺序(见 §2 读取路径):**L3 准则(preamble,全量,≤50 条)→ L1 事件(top-k)→ 原决策日志反思(迁移期兜底)**。准则作为"团队怎么想"的稳定底座,事件作为"具体情境下的实例"。

---

## 5. L2 — 情景加权检索(可量化实现,v3 生效;基线 v1.0,公式留待消融定稿)

> 评审结论落点:L2 必须把"上调权重"变成**可计算**的检索流程,但有两类实现必须避开——**特征选错对象**(组合/持仓类特征在本框架不存在;VIX 类特征只对美股有意义)和**打分结构错误**(量纲未标准化就做余弦;带符号 alpha 给"历史赢家"加权 = 检索版幸存者偏差)。修订版如下。

### 5.1 定位:检索策略,不是权重

文本 prompt 里不存在"权重乘法",只有"注入哪些、什么顺序、怎么标注"。L2 只回答三个问题:**用什么特征描述市场状态 → 怎样算"相似" → 取出哪些、以什么顺序呈现**。整体是读取路径上的**确定性函数**:只读不写、无额外 LLM 调用。

### 5.2 特征集:按市场/资产类型定义,主向量取标的自身

框架每次 run 只分析**单一标的**(无组合/持仓),且跨市场(US/HK/东京/印度/A 股/加密)、跨资产(股票/加密货币)——**不允许一套固定向量打天下**:

- **主向量(所有市场通用;标的自身、日期钉死、确定性可得)**——市场分析师工具链(stockstats)本来就在算这些特征,取数路径现成:

  `fingerprint = [realized_vol_20d, trend(close 相对 50sma 的方向与幅度), rsi14, volume_ratio(当日量/20日均量), atr_pct]`

- **可选次元(市场环境)**:标的所在市场**有对应波动率指数才加**(如美股用 VIX 类),没有就省略该维;检索前按**特征掩码**对齐,缺失维一律不参与余弦;
- **明确不用**:`vix` 本身(非美市场无意义)、sector/板块动量(历史钉死日期下数据不可靠、易 look-ahead)、组合级特征(本框架无组合概念)。

### 5.3 相似度:先逐维标准化,再算余弦

余弦**不是**逐维度缩放不变的:RSI(0–100)会压掉 volume_ratio(0.5–2)等小量纲维度的贡献。**必须在记忆库上逐维标准化后再算余弦**,否则公式写得再漂亮,结果也基本由 RSI 一维决定:

```python
# 检索时(标准化统计量 Z 在全部已结算事件上估计,每维各一,随事件写入增量更新)
cur = (current_raw - Z["mean"]) / Z["std"]

def sim(m):
    fp = (m["state_fingerprint"] - Z["mean"]) / Z["std"]
    return cosine(cur, fp)                    # ∈ [-1, 1]
```

### 5.4 两段式检索:先过滤,再排序,后标注

**阶段 1 — 候选过滤(只决定"进不进",不决定"排多前")**
- 余弦相似度 ≥ τ(起步 `0.7`,消融调参);
- 同 ticker 配额(默认 ≤ 2)与多样性约束,防单票记忆垄断 top-k;
- 保护集(§3.5)条目**保证在候选内,但不额外加分**——极端度由写入期保护机制负责,读取期不重复计算。

**阶段 2 — 排序与注入**
- 主序 = 相似度;recency 只做**乘法衰减**,唯一超参 γ(默认 `0.2`),而非三个子分做固定权重线性相加:

  `score(m) = sim(m) · recency(m)^γ` ,按分数降序取 top-K(默认 5~8);

- `recency = 1/(1+Δdays)`:Δdays 取**分析时间轴上的交易日差**(钉死日期,非墙上时钟——用日历日会被周末/节假日假性"变旧");
- 注入时每条按相关度打 **高/中/低** 标注(§3.3 无此字段,读取路径临时判定即可),引导 PM 注意力;同档并列时允许用 `|alpha|`(不分方向)作 tiebreak。

### 5.5 明确不做的事(评审结论,写死防回潮)

- **不用带符号 alpha 给"历史赢家"加权**——大亏记忆(最该防重蹈覆辙)会被系统性降权,牛市里检索结果越检越激进;win/loss 的信息价值大致对称;
- **不做全局重排加权、不做连续高维距离上的硬 top-k**——事件样本只有几十条时是噪声放大器;
- **不把 performance 并入主得分**——效果信号归写入期的保护集/淘汰机制,读取期只回答"和现在像不像 + 多近";
- **不引入额外 LLM 调用**——L2 全程确定性计算。

### 5.6 指纹来源与验证节奏

- **指纹时机**:决策时(分析 run 内)用日期钉住的确定性数据算一次,随 pending 条目预存;结算生成事件时**继承**——保证事件携带"决策日状态"而非"结算日状态"(§9 开放问题 3 有懒算备选);
- **样本不足时降级**:已结算事件 < ~30 条前,检索退化为**粗粒度状态桶匹配**(由指纹派生,即 §3.3 的 `state_bucket`)——粗桶才有样本量,连续距离在个位数样本下是噪声放大器;
- **验证先于信仰**:样本充足后做 leave-one-out / 配对 A/B 消融,阶梯对比 `(A) 纯相似度 → (B) 相似度×recency^γ → (C) vs 现基线(同 ticker 最近 n 条平权)`,验证"状态匹配是否真的挑得出更相关的记忆",公式与 τ/K/γ 依据数据定稿,而不是现在拍死。

---

## 6. 新增配置项(default_config.py)

| Key | 默认 | 说明 | 状态 |
|---|---|---|---|
| `lessons_path` | `~/.tradingagents/memory/trading_lessons.md` | L1 事件 + L3 准则存储(独立于决策日志;`TRADINGAGENTS_LESSONS_PATH` 覆盖) | ✅ v1 |
| `event_alpha_threshold` | `0.05` | |alpha| ≥ 该值才写 L1 事件(可调) | ✅ v1 |
| `event_protect_threshold` | `0.10` | |alpha| ≥ 该值进保护集,不参与常规淘汰 | ✅ v1 |
| `max_event_entries` | `200` | L1 事件条目上限 | ✅ v1 |
| `max_rule_entries` | `50` | L3 准则上限 | ⏳ v2 |
| `distill_interval_days` | `90` | 蒸馏最小间隔(日历天;回测语义见 §9) | ⏳ v2 |
| `postmortem_llm` | `"deep"` | 复盘/蒸馏 agent 用 deep 还是 quick 档 | ✅ v1 |
| `retention_weights` | `{"recency":0.4,"freq":0.2,"extremity":0.4}` | 淘汰分数权重(超参,v2 实验调) | ⏳ v2 |
| `retrieval_tau` | `0.7` | L2 余弦匹配阈值(§5.4) | ⏳ v3 |
| `retrieval_top_k` | `8` | L2 top-K 注入条数(§5.4) | ⏳ v3 |
| `retrieval_recency_gamma` | `0.2` | L2 recency 乘法门控指数(§5.4) | ⏳ v3 |
| `retrieval_same_ticker_cap` | `2` | L2 同 ticker 注入配额(§5.4) | ⏳ v3 |
| `retrieval_min_events` | `30` | 已结算事件数低于此值时检索退化为粗桶匹配(§5.6) | ⏳ v3 |

环境变量沿用 `TRADINGAGENTS_*` 覆盖机制(`default_config.py: _ENV_OVERRIDES` 加行即可);配置键为**平键**(与 `DEFAULT_CONFIG` 扁平风格一致),v2/v3 键在实现对应阶段时再进代码。

---

## 7. 代码改动点清单

| 位置 | 改动 | 阶段 |
|---|---|---|
| `tradingagents/agents/utils/memory.py` | `_parse_entry` 按 `EVENT`/`RULE` 前缀分流;新增 lessons 文件读写、`store_event`、`get_rules`;`get_past_context` 增加访问追踪回写;新增 `maintain_memory`(冗余剔除→淘汰打分→保护集→蒸馏队列) | v1/v2 |
| `tradingagents/graph/trading_graph.py` | `_resolve_pending_entries` 结算后接阈值判定 + 复盘 agent 调用;run 注入前跑 L1 检索(L2 就绪前用 recency top-k);`maintain_memory` 调度点 | v1 |
| 新增 `tradingagents/agents/utils/postmortem_agent.py` + `schemas`(如 `EventPostmortem`,仿 `TraderProposal`/`PortfolioDecision` 模式) | 复盘摘要结构化输出,读 full_states_log JSON | v1 |
| `tradingagents/agents/managers/portfolio_manager.py` | `lessons_line` 改为三段式注入:L3 准则 + L1 事件(检索选中)+ 决策日志反思兜底 | v1(准则段)/v3(检索段) |
| `tradingagents/agents/utils/agent_utils.py` | `get_language_instruction` 等保持;必要时暴露 lessons 注入 helper | v1 |
| `tradingagents/default_config.py` | §6 配置项 + `_ENV_OVERRIDES` 行 | v1 |
| `tests/test_memory_log.py` 等 | 新条目格式解析、遗忘/保护集/冗余剔除、蒸馏调度单测;现有 871 行测试需按新分流语义回归 | v1+ |

> 注:复盘/蒸馏发生在结算与 run 之间,不进 LangGraph 主流程;是否在 CLI agent 面板展示复盘进度是 UX 开放问题(见 §9)。

---

## 8. 成功度量(实验协议)

LLM 输出有采样噪声(见 README「Reproducibility」),单次对比无效,必须配对多轮:

1. **配对 A/B**:同一批 (ticker, date)(日期足够靠后、已实现收益可从 yfinance 确定性取得),同一配置下开关三层记忆链路各跑多轮;
2. **指标**:(a) 方向命中率(评级 vs 已实现 alpha 符号);(b) 模拟 alpha 均值/方差;(c) 复盘摘要质量(人工抽查:是否抓住真正原因、是否可迁移);
3. **分阶段验收**:
   - v1(L1):事件条目量级、注入 token 前后对比、方向命中率 vs 现基线;
   - v2(L3):准则是否"空话率"低(可操作、带证据)、坏教训修订是否生效;
   - v3(L2):按 §5.6 消融阶梯 (A→B→C) + leave-one-out,检验状态指纹匹配是否真的提升 top-k 命中;τ/K/γ 据数据定稿。

---

## 9. 开放问题(待评审决定)

1. `event_alpha_threshold` / `event_protect_threshold` 的合理默认值(0.05/0.10 只是起点;阈值高低直接决定 L1 记忆量级与复盘 agent 调用频率);
2. "季度/蒸馏间隔"在**回测**(日期钉在历史交易日)与**实盘**语义不同 —— 是否按分析时间轴推进、按结算批次推进,还是日历天;
3. 状态指纹在**决策时**算(每次 run 多一次确定性取数,但准确)还是**结算时**懒算(只对幸存条目,成本低但需从历史重取决策日数据);配套:标准化统计量 Z 的冷启动与增量更新边界、缺维条目的特征掩码对齐(§5.3/§5.6);
4. 复盘/蒸馏 agent 默认 deep 档的成本是否可接受(极端事件频率低,预计可接受,需实测);
5. 决策日志与 lessons 文件是否需要在 CLI agent 面板/报告中可见(复盘发生在 run 间隙,现有 MessageBuffer 只展示主流程 agent);
6. 淘汰分数各权重初值是否先用 v1 简化策略(先冗余剔除 + 保护集 + 最旧未用)验证正确性,再上加权打分。

---

## 10. 里程碑

- **v1 — L1 落地**:采样阈值 + 复盘 agent + 结构化事件条目 + 双层存储指针 + 上限/保护集/冗余剔除/访问追踪;A/B 对比现基线;
- **v2 — L3 落地**:增量蒸馏挂在淘汰队列 + 准则库存取 + PM 三段式注入(preamble 段);决策日志可随之放开 rotation;
- **v3 — L2 落地**:状态指纹入条目(§5.2 特征集)+ 标准化余弦两段式检索(过滤/排序/标注,§5.3–5.4)+ 粗桶降级 + §5.6 消融定稿公式。
